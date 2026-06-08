#!/usr/bin/env python3
"""
resolve_ui_ahi.py

Transform canonical → tiny, UI-ready JSON using rigid assumptions:

Priority of sources (per date): sleep_study > followup/hospital report > flat report
Date precedence: study_date > report_date > date ; ignore upload dates
Emit: baseline_ahi, current_ahi, therapy_start_date, timeline[date, ahi, context, provenance]
Timeline: one point per week (Mon-start), chosen by source priority

Context rules:
- Explicit keywords win (baseline/diagnostic/untreated vs treated/follow-up/appliance vs CPAP)
- If events include device_delivery/titration → split "before" vs "after"
- Else infer by magnitude & recency (earlier-highest ≈ baseline, latest-lowest ≈ current)
"""

import json, re, sys
from pathlib import Path
from datetime import timedelta
import pandas as pd

KEY = "ahi"
SRC_PRIORITY = {"sleep_study": 0, "report": 1}

BASELINE_RE = re.compile(r"\b(baseline|diagnostic|pre[- ]?treat|untreated|initial)\b", re.I)
TREATED_RE  = re.compile(r"\b(follow[- ]?up|treated|with (oa|appliance|device)|on (oa|appliance|device)|post[- ]?treat|current)\b", re.I)
CPAP_RE     = re.compile(r"\bcpap\b", re.I)

def _to_dt(s):
    try:
        return pd.to_datetime(s)
    except Exception:
        return pd.NaT

def _is_upload(item):
    return (item.get("date_kind") == "upload") or (item.get("is_upload_date") is True)

def _push(container, source_kind):
    rows = []
    for it in container or []:
        if _is_upload(it):                # Assumption 2.3: ignore upload dates
            continue
        # Assumption 2: study > report > generic date
        d = it.get("study_date") or it.get("report_date") or it.get("date")
        dt = _to_dt(d)
        if pd.isna(dt):
            continue
        v = it.get(KEY) if source_kind != "report_flat" else (it.get("value") if it.get("key")==KEY else None)
        if v is None:
            continue
        try:
            v = float(v)
        except Exception:
            continue
        rows.append({
            "date": dt,
            "ahi": v,
            "source_kind": "report" if source_kind.startswith("report") else source_kind,
            "file_name": it.get("file_name"),
            "title": it.get("title"),
            "text": it.get("text"),
        })
    return rows

def _classify(row):
    blob = " ".join(str(x) for x in [row.get("title"), row.get("text"), row.get("file_name")] if x).lower()
    if CPAP_RE.search(blob): return "historical_cpap"
    if BASELINE_RE.search(blob): return "baseline"
    if TREATED_RE.search(blob):  return "current"
    return None  # infer later

def _therapy_start(timeline):
    evts = []
    for e in (timeline or {}).get("events", []):
        t = (e.get("type") or "").lower()
        if t in {"device_delivery","titration"} and e.get("date"):
            evts.append(_to_dt(e["date"]))
    return min(evts).strftime("%Y-%m-%d") if evts else None

def resolve(canonical: dict):
    tl = (canonical.get("canonical_derived") or {}).get("timeline") or {}
    # 1) Gather rows from all sources
    rows = []
    rows += _push(tl.get("sleep_studies"), "sleep_study")
    rows += _push(tl.get("reports_grouped"), "report_grouped")
    rows += _push(tl.get("reports"), "report_flat")
    df = pd.DataFrame(rows)
    if df.empty:
        return {"baseline_ahi": None, "current_ahi": None, "therapy_start_date": None, "timeline": []}

    # 2) Prefer sleep_study over report on identical dates
    df["src_priority"] = df["source_kind"].map(SRC_PRIORITY).fillna(2)
    df = df.sort_values(["date", "src_priority", "file_name"]).drop_duplicates(subset=["date"], keep="first")

    # 3) Classify by explicit keywords
    df["context"] = df.apply(_classify, axis=1)

    # 4) Event-informed inference
    ts = _therapy_start(tl)
    if ts:
        ts_dt = _to_dt(ts)
        df.loc[df["context"].isna() & (df["date"] <= ts_dt) & (df["ahi"] >= 15), "context"] = "baseline"
        df.loc[df["context"].isna() & (df["date"] >  ts_dt) & (df["ahi"] <= 15), "context"] = "current"

    # 5) Fallback inference by magnitude/recency
    if df["context"].isna().any():
        q_early  = df["date"].quantile(0.25)
        q_late   = df["date"].quantile(0.75)
        early    = df[df["date"] <= q_early] if not df[df["date"] <= q_early].empty else df
        late     = df[df["date"] >= q_late]  if not df[df["date"] >= q_late].empty  else df
        df.loc[early["ahi"].idxmax(), "context"] = df.loc[early["ahi"].idxmax(), "context"] or "baseline"
        df.loc[late["ahi"].idxmin(),  "context"] = df.loc[late["ahi"].idxmin(),  "context"]  or "current"
    df["context"] = df["context"].fillna("historical")

    # 6) Group by week (Mon start) → one point per week by source priority
    df["week"] = df["date"].dt.to_period("W").apply(lambda p: p.start_time)
    df = df.sort_values(["week", "src_priority", "date"])
    df_week = df.groupby("week", as_index=False).first()

    # 7) Baseline/current singletons
    baseline_row = df[df["context"]=="baseline"].sort_values("date").head(1)
    current_row  = df[df["context"]=="current"].sort_values("date").tail(1)
    baseline_ahi = float(baseline_row["ahi"].iloc[0]) if len(baseline_row) else None
    current_ahi  = float(current_row["ahi"].iloc[0]) if len(current_row) else float(df.sort_values("date")["ahi"].iloc[-1])

    # 8) Emit small JSON
    timeline = [{
        "date": r["week"].strftime("%Y-%m-%d"),
        "ahi": float(r["ahi"]),
        "context": str(r["context"]),
        "provenance": {"source_kind": r["source_kind"], "file_name": r.get("file_name")}
    } for _, r in df_week.iterrows()]

    return {
        "baseline_ahi": baseline_ahi,
        "current_ahi": current_ahi,
        "therapy_start_date": ts,
        "timeline": timeline
    }

def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path or not path.exists():
        print("Usage: python resolve_ui_ahi.py /path/to/canonical.json", file=sys.stderr)
        sys.exit(1)
    canonical = json.loads(Path(path).read_text(encoding="utf-8"))
    ui = resolve(canonical)
    print(json.dumps(ui, indent=2))

if __name__ == "__main__":
    main()

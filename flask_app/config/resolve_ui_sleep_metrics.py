#!/usr/bin/env python3
"""
resolve_ui_sleep_metrics.py

Transform canonical -> UI-ready sleep metrics JSON using rigid assumptions.
Handles all sleep metrics: AHI, ODI, O2_nadir, time_below_90_pct, etc.

Priority of sources (per date): sleep_study > followup/hospital report > flat report
Date precedence: study_date > report_date > date ; ignore upload dates
Emit: baseline_*, current_*, therapy_start_date, timeline[date, metrics, context, provenance]
Timeline: one point per measured date, chosen by source priority
"""

import json, re, sys
from pathlib import Path
from datetime import timedelta
import pandas as pd

# All sleep metrics we want to extract
SLEEP_METRICS = [
    "ahi", "odi", "o2_nadir_pct", "time_below_90_pct", "time_below_88_pct",
    "supine_ahi", "ahi_rem", "ahi_nrem", "rdi", "sleep_efficiency",
    "total_sleep_time", "sleep_duration_h", "desaturation_events"
]

SRC_PRIORITY = {"sleep_study": 0, "report": 1}

BASELINE_RE = re.compile(r"\b(baseline|diagnostic|pre[- ]?treat|untreated|initial)\b", re.I)
TREATED_RE  = re.compile(r"\b(follow[- ]?up|treated|with (oa|appliance|device)|on (oa|appliance|device)|post[- ]?treat|current)\b", re.I)
CPAP_RE     = re.compile(r"\bcpap\b", re.I)

def _to_dt(s):
    """
    Parse date values robustly across mixed locales.

    Sleep reports can contain ISO (YYYY-MM-DD), US (MM/DD/YYYY), or
    Hebrew/European (DD/MM/YYYY). Prefer day-first only when the input
    strongly indicates it (first token > 12), and keep ISO behavior strict.
    """
    if s is None:
        return pd.NaT
    if isinstance(s, pd.Timestamp):
        return s
    text = str(s).strip()
    if not text:
        return pd.NaT

    # ISO-like values are unambiguous and should remain year-first.
    if re.match(r"^\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}", text):
        try:
            return pd.to_datetime(text, errors="coerce")
        except Exception:
            return pd.NaT

    # For slash-separated formats, infer day-first only when clearly needed.
    if "/" in text:
        m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\D.*)?$", text)
        if m:
            first_token = int(m.group(1))
            dayfirst = first_token > 12
            try:
                return pd.to_datetime(text, dayfirst=dayfirst, errors="coerce")
            except Exception:
                return pd.NaT

    try:
        return pd.to_datetime(text, errors="coerce")
    except Exception:
        return pd.NaT

def _is_upload(item):
    return (item.get("date_kind") == "upload") or (item.get("is_upload_date") is True)

def _extract_metrics(item, source_kind):
    """Extract all sleep metrics from a single item"""
    metrics = {}
    
    if source_kind == "report_flat":
        # For flat reports, check if key matches our metrics
        key = item.get("key")
        if key in SLEEP_METRICS:
            try:
                metrics[key] = float(item.get("value"))
            except (ValueError, TypeError):
                pass
    else:
        # For structured data, extract all available metrics
        for metric in SLEEP_METRICS:
            value = item.get(metric)
            if value is not None:
                try:
                    metrics[metric] = float(value)
                except (ValueError, TypeError):
                    pass
    
    return metrics

def _push(container, source_kind):
    rows = []
    for it in container or []:
        if _is_upload(it):                # Ignore upload dates
            continue
        
        # Get date with precedence: study_date > report_date > date
        d = it.get("study_date") or it.get("report_date") or it.get("date")
        dt = _to_dt(d)
        
        # Extract all metrics from this item
        metrics = _extract_metrics(it, source_kind)
        if not metrics:
            continue
        
        # Mark as undated if no date found
        is_undated = pd.isna(dt)
        if is_undated:
            dt = pd.Timestamp("1900-01-01")  # Placeholder for undated
        
        rows.append({
            "date": dt,
            "source_kind": "report" if source_kind.startswith("report") else source_kind,
            "file_name": it.get("file_name"),
            "title": it.get("title"),
            "text": it.get("text"),
            "_undated": is_undated,
            **metrics  # Spread all metrics into the row
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
    # IMPORTANT (sleep-report-only mode):
    # Only include true sleep study report-derived measurements. This prevents mixing AHI mentions from
    # other documents (progress notes, emails, etc.) into the AHI chart and clinical displays.
    rows += _push(tl.get("sleep_studies"), "sleep_study")
    
    if not rows:
        return {"baseline": {}, "current": {}, "therapy_start_date": None, "timeline": []}
    
    df = pd.DataFrame(rows)
    
    # 2) Prefer sleep_study over report on identical dates
    df["src_priority"] = df["source_kind"].map(SRC_PRIORITY).fillna(2)
    df = df.sort_values(["date", "src_priority", "file_name"]).drop_duplicates(subset=["date"], keep="first")

    # 3) Classify by explicit keywords
    df["context"] = df.apply(_classify, axis=1)

    # 4) Event-informed inference
    ts = _therapy_start(tl)
    if ts:
        ts_dt = _to_dt(ts)
        # Use AHI for therapy split if available, otherwise use any available metric
        ahi_col = "ahi" if "ahi" in df.columns else None
        if ahi_col is not None:
            df.loc[df["context"].isna() & (df["date"] <= ts_dt) & (df[ahi_col] >= 15), "context"] = "baseline"
            df.loc[df["context"].isna() & (df["date"] >  ts_dt) & (df[ahi_col] <= 15), "context"] = "current"

    # 5) Fallback inference by magnitude/recency (using AHI if available)
    if df["context"].isna().any() and "ahi" in df.columns:
        # Only use dated measurements for inference
        df_dated_for_inference = df[~df["_undated"]]
        
        if len(df_dated_for_inference) == 1:
            # If only one dated measurement, it should be "current"
            df.loc[df_dated_for_inference.index[0], "context"] = "current"
        elif len(df_dated_for_inference) > 1:
            # Multiple dated measurements - use magnitude/recency logic
            q_early  = df_dated_for_inference["date"].quantile(0.25)
            q_late   = df_dated_for_inference["date"].quantile(0.75)
            early    = df_dated_for_inference[df_dated_for_inference["date"] <= q_early] if not df_dated_for_inference[df_dated_for_inference["date"] <= q_early].empty else df_dated_for_inference
            late     = df_dated_for_inference[df_dated_for_inference["date"] >= q_late]  if not df_dated_for_inference[df_dated_for_inference["date"] >= q_late].empty  else df_dated_for_inference
            
            # Only assign baseline if we have multiple values and highest AHI is significantly higher
            if len(early) > 0 and len(late) > 0:
                highest_ahi_idx = early["ahi"].idxmax()
                lowest_ahi_idx = late["ahi"].idxmin()
                
                # Only assign baseline if highest AHI is significantly higher (e.g., >5 points difference)
                if early.loc[highest_ahi_idx, "ahi"] - late.loc[lowest_ahi_idx, "ahi"] > 5:
                    df.loc[highest_ahi_idx, "context"] = "baseline"
                    df.loc[lowest_ahi_idx, "context"] = "current"
                else:
                    # If AHI values are similar, treat all as current
                    df.loc[df_dated_for_inference.index, "context"] = "current"
    
    df["context"] = df["context"].fillna("historical")

    # 6) Separate dated and undated measurements
    df_dated = df[~df["_undated"]].copy()
    df_undated = df[df["_undated"]].copy()
    
    # 7) Process dated measurements for timeline
    timeline = []
    needs_review = []
    
    if not df_dated.empty:
        # Keep exact measured date (normalized to day) to avoid UI/date mismatch.
        df_dated["date_only"] = df_dated["date"].dt.normalize()
        df_dated = df_dated.sort_values(["date_only", "src_priority", "date"]).drop_duplicates(
            subset=["date_only"], keep="first"
        )

        # Build timeline from dated measurements
        for _, r in df_dated.iterrows():
            timeline_entry = {
                "date": r["date_only"].strftime("%Y-%m-%d"),
                "context": str(r["context"]),
                "provenance": {"source_kind": r["source_kind"], "file_name": r.get("file_name")},
                "metrics": {}
            }
            tx = r.get("text")
            if tx and str(tx).strip():
                timeline_entry["clinical_notes"] = str(tx).strip()[:16000]
            
            for metric in SLEEP_METRICS:
                if metric in r and not pd.isna(r[metric]):
                    timeline_entry["metrics"][metric] = float(r[metric])
            
            timeline.append(timeline_entry)
    
    # 8) Process undated measurements for needs review
    if not df_undated.empty:
        for _, r in df_undated.iterrows():
            review_entry = {
                "context": str(r["context"]),
                "provenance": {"source_kind": r["source_kind"], "file_name": r.get("file_name")},
                "metrics": {},
                "reason": "No date found in document"
            }
            
            for metric in SLEEP_METRICS:
                if metric in r and not pd.isna(r[metric]):
                    review_entry["metrics"][metric] = float(r[metric])
            
            needs_review.append(review_entry)
    
    # 9) Extract baseline and current values from dated measurements only
    baseline_metrics = {}
    current_metrics = {}
    
    if not df_dated.empty:
        baseline_row = df_dated[df_dated["context"]=="baseline"].sort_values("date").head(1)
        current_row  = df_dated[df_dated["context"]=="current"].sort_values("date").tail(1)
        
        if len(baseline_row):
            for metric in SLEEP_METRICS:
                if metric in baseline_row.columns and not pd.isna(baseline_row[metric].iloc[0]):
                    baseline_metrics[metric] = float(baseline_row[metric].iloc[0])
        
        if len(current_row):
            for metric in SLEEP_METRICS:
                if metric in current_row.columns and not pd.isna(current_row[metric].iloc[0]):
                    current_metrics[metric] = float(current_row[metric].iloc[0])
        else:
            # Fallback to most recent values
            latest_row = df_dated.sort_values("date").tail(1)
            for metric in SLEEP_METRICS:
                if metric in latest_row.columns and not pd.isna(latest_row[metric].iloc[0]):
                    current_metrics[metric] = float(latest_row[metric].iloc[0])

    return {
        "baseline": baseline_metrics,
        "current": current_metrics,
        "therapy_start_date": ts,
        "timeline": timeline,
        "needs_review": needs_review
    }

def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path or not path.exists():
        print("Usage: python resolve_ui_sleep_metrics.py /path/to/canonical.json", file=sys.stderr)
        sys.exit(1)
    canonical = json.loads(Path(path).read_text(encoding="utf-8"))
    ui = resolve(canonical)
    print(json.dumps(ui, indent=2))

if __name__ == "__main__":
    main()

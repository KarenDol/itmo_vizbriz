# sleep_first_policy.py
import copy, math, statistics
from datetime import datetime, timedelta

SLEEP_KEYS = {"ahi","ahi_overall","ahi_rem","ahi_nrem","ahi_supine","ahi_non_supine",
              "o2_nadir_pct","odi","odi3","rdi","rdi_overall","sleep_efficiency_pct",
              "time_below_90_pct","t90_pct","tst_min","trt_min","supine_ahi","rem_ahi"}

# tolerances for matching report metrics to an episode (units-aware)
TOL = {"ahi": 1.0, "ahi_overall": 1.0, "ahi_rem": 2.0, "ahi_supine": 2.0,
       "o2_nadir_pct": 2.0, "odi": 1.0, "odi3": 1.0, "rdi": 1.0,
       "sleep_efficiency_pct": 3.0, "time_below_90_pct": 1.0}

def _as_date(s):
    if not s: return None
    return datetime.fromisoformat(s[:10])

def _most_common(vals):
    try:
        return statistics.mode(vals)
    except statistics.StatisticsError:
        # tie → median for numerics; first for strings
        nums = [v for v in vals if isinstance(v,(int,float))]
        return statistics.median(nums) if nums else vals[0]

def _apply_medical_ahi_prioritization(episodes):
    """Apply medical logic: higher AHI = baseline (earlier date), lower AHI = improvement (later date)."""
    if len(episodes) < 2:
        return episodes
    
    # Extract AHI values and dates
    ahi_episodes = []
    for ep in episodes:
        ahi = ep.get("metrics", {}).get("ahi") or ep.get("ahi")
        if ahi is not None:
            date = ep.get("observed_at") or ep.get("date")
            ahi_episodes.append((ahi, date, ep))
    
    if len(ahi_episodes) < 2:
        return episodes
    
    # Sort by AHI value (highest first)
    ahi_episodes.sort(key=lambda x: x[0], reverse=True)
    
    # Check if we have a significant difference (>= 8 AHI points)
    highest_ahi = ahi_episodes[0][0]
    lowest_ahi = ahi_episodes[-1][0]
    
    if highest_ahi - lowest_ahi >= 8.0:
        print(f"🏥 Medical Logic: Prioritizing higher AHI {highest_ahi} as baseline over {lowest_ahi}")
        
        # Reorder episodes: highest AHI gets earliest date, lowest AHI gets latest date
        # Find the earliest and latest dates
        dates = [ep[1] for ep in ahi_episodes if ep[1]]
        if len(dates) >= 2:
            dates.sort()
            earliest_date = dates[0]
            latest_date = dates[-1]
            
            # Update episodes with medical logic
            for i, (ahi, date, ep) in enumerate(ahi_episodes):
                if i == 0:  # Highest AHI
                    ep["observed_at"] = earliest_date
                    ep["date"] = earliest_date
                elif i == len(ahi_episodes) - 1:  # Lowest AHI
                    ep["observed_at"] = latest_date
                    ep["date"] = latest_date
    
    return episodes

def _merge_same_day_episodes(episodes):
    """Merge multiple sleep-study entries from the same observed date."""
    by_day = {}
    for e in episodes:
        d = _as_date(e.get("observed_at") or e.get("date"))
        key = d.date().isoformat() if d else None
        if key not in by_day: by_day[key] = []
        by_day[key].append(e)

    merged = []
    for day, group in by_day.items():
        metrics_pool = {}
        files, ids = [], []
        for e in group:
            files.append(e.get("file_name"))
            if e.get("episode_id"): ids.append(e["episode_id"])
            # unify metrics from {key:value} AND from list-of-metrics
            kv = {}
            if isinstance(e.get("metrics"), list):
                for m in e["metrics"]:
                    k, v = m.get("key"), m.get("value")
                    if k and v is not None:
                        metrics_pool.setdefault(k, []).append(v)
                        kv[k] = v
            # top-level fallbacks
            for k,v in e.items():
                if k in SLEEP_KEYS and isinstance(v, (int,float)):
                    metrics_pool.setdefault(k, []).append(v)
                    kv[k] = v

        merged_metrics = {k: _most_common(vs) for k,vs in metrics_pool.items()}
        merged.append({
            "observed_at": day,
            "metrics": merged_metrics,
            "episode_ids": ids,
            "files": files,
            "source_kind": "sleep_study"
        })
    # sort by observed_at
    merged.sort(key=lambda x: x["observed_at"] or "")
    return merged

def _sanity_fix_episode_metrics(m):
    """Fix clearly inconsistent values (e.g., REM AHI wildly larger than overall when a plausible REM exists elsewhere) and apply medical logic for AHI prioritization."""
    # unify naming
    if "ahi_overall" in m and "ahi" not in m: m["ahi"] = m["ahi_overall"]
    if "supine_AHI" in m and "supine_ahi" not in m: m["supine_ahi"] = m["supine_AHI"]

    # Medical Logic: If no clear dates, assume higher AHI = baseline (before treatment)
    # Collect all AHI values
    ahi_values = []
    for key in ["ahi", "ahi_overall", "ahi_rem", "ahi_nrem", "ahi_supine"]:
        if key in m and m[key] is not None:
            ahi_values.append((key, float(m[key])))
    
    if len(ahi_values) >= 2:
        # Sort by AHI value (highest first)
        ahi_values.sort(key=lambda x: x[1], reverse=True)
        highest_ahi = ahi_values[0]
        lowest_ahi = ahi_values[-1]
        
        # If we have a significant difference (>= 8), prioritize the higher value as baseline
        if highest_ahi[1] - lowest_ahi[1] >= 8.0:
            print(f"🏥 Medical Logic: Prioritizing higher AHI {highest_ahi[1]} as baseline over {lowest_ahi[1]}")
            # Set the highest AHI as the overall AHI (baseline)
            m["ahi"] = highest_ahi[1]
            m["ahi_overall"] = highest_ahi[1]
            # Keep the lower AHI as positional (e.g., supine)
            if lowest_ahi[0] not in ["ahi", "ahi_overall"]:
                m[lowest_ahi[0]] = lowest_ahi[1]

    # If REM AHI looks like an OCR slip (e.g., 92) and a plausible REM value exists (e.g., 14.9) in evidence/other blocks, keep the plausible one.
    if "ahi" in m and "ahi_rem" in m and m["ahi"] < 10 and m["ahi_rem"] > 60:
        # prefer any other rem-ish candidate in the episode metrics (sometimes duplicated with a different key)
        for alt_key in ("rem_ahi","REM_AHI","ahi_REM"):
            if alt_key in m and 0 <= m[alt_key] <= 50:
                m["ahi_rem"] = m[alt_key]
                break

    # Don't allow overall to equal NREM when a different supine/REM exists far away
    if "ahi" in m and "ahi_nrem" in m and abs(m["ahi"] - m["ahi_nrem"]) < 1e-6:
        for k in ("ahi_rem","ahi_supine"):
            if k in m and abs(m[k]-m["ahi"]) >= 8.0:
                # leave overall as-is; sanity will be checked by ROI in extraction; nothing to do here
                pass
    return m

def _episodes_from_canonical(c):
    eps = []
    for s in c.get("sleep_studies", []):
        observed = s.get("observed_at") or s.get("date")
        m = {}
        # list-of-metrics
        if isinstance(s.get("metrics"), list):
            for kv in s["metrics"]:
                if kv.get("key") and kv.get("value") is not None:
                    m[kv["key"]] = kv["value"]
        # top-level fallbacks on the item
        for k,v in s.items():
            if k in SLEEP_KEYS and isinstance(v,(int,float)):
                m[k] = v
        eps.append({
            "observed_at": observed,
            "metrics": _sanity_fix_episode_metrics(m),
            "episode_id": s.get("episode_id"),
            "file_name": s.get("file_name"),
            "source_kind": "sleep_study"
        })
    # Apply medical logic before merging
    eps = _apply_medical_ahi_prioritization(eps)
    return _merge_same_day_episodes(eps)

def _closest_episode(episodes, ref_date):
    """Pick the chronologically closest episode (preferring <= ref_date)."""
    if not ref_date or not episodes: return None
    ref = _as_date(ref_date)
    # prefer same day or before; then after
    past = [e for e in episodes if _as_date(e["observed_at"]) and _as_date(e["observed_at"]) <= ref]
    future = [e for e in episodes if _as_date(e["observed_at"]) and _as_date(e["observed_at"]) > ref]
    cand = past or future
    if not cand: return None
    return min(cand, key=lambda e: abs((_as_date(e["observed_at"]) - ref).days))

def _classify_report_metric(ep, key, value):
    """Label report metric relative to an episode as confirming / conflicting / fill_only."""
    if not ep: return "unlinked"
    if key not in SLEEP_KEYS: return "ignored"
    # pick episode metric name
    kmap = {"ahi_overall":"ahi", "rem_ahi":"ahi_rem", "supine_ahi":"ahi_supine", "odi3":"odi"}
    ek = kmap.get(key, key)
    v_ep = ep["metrics"].get(ek)
    if v_ep is None:
        return "fill_only"   # episode lacks it; allow add
    tol = TOL.get(key, 1.0)
    return "confirming" if abs(float(v_ep) - float(value)) <= tol else "conflicting"

def apply_sleep_first_policy(canonical: dict) -> tuple[dict, dict]:
    """
    Returns (cleaned_canonical, policy_report)
    - Trend & baseline/latest from sleep studies only
    - Reports kept but never overwrite primary
    - Each report metric linked & classified
    """
    c = copy.deepcopy(canonical)

    # 1) Build & merge sleep-study episodes
    episodes = _episodes_from_canonical(c)

    # 2) Rebuild timeline (sleep studies only) - preserve existing format if present
    tl = c.setdefault("canonical_derived", {}).setdefault("timeline", {})
    
    # Check if canonical_derived.timeline.sleep_studies already exists with proper format
    existing_ss = tl.get("sleep_studies", [])
    preserve_existing = False
    
    # Helper to normalize dates for matching
    def _normalize_date_for_matching(d):
        if not d:
            return None
        if isinstance(d, str):
            return d[:10] if len(d) >= 10 else d  # Take YYYY-MM-DD part
        if hasattr(d, 'strftime'):
            return d.strftime('%Y-%m-%d')
        return str(d)[:10]
    
    if existing_ss and len(existing_ss) > 0 and isinstance(existing_ss[0], dict):
        # Check if existing format has flattened metrics (has 'ahi' as direct key)
        has_flattened_format = any('ahi' in ss for ss in existing_ss if isinstance(ss, dict))
        if has_flattened_format:
            preserve_existing = True
            print(f"Preserving existing canonical_derived.timeline.sleep_studies format with {len(existing_ss)} entries")
            # Preserve existing format, just update with episodes data if needed
            # Match episodes to existing sleep studies by date/episode_id
            episode_map = {}
            episode_id_map = {}
            for e in episodes:
                ep_date = _normalize_date_for_matching(e.get("observed_at"))
                if ep_date:
                    episode_map[ep_date] = e
                # Also map by episode_id if available
                ep_id = e.get("episode_id")
                if ep_id:
                    episode_id_map[ep_id] = e
            
            for ss in existing_ss:
                ss_date = _normalize_date_for_matching(ss.get("date") or ss.get("observed_at"))
                ss_ep_id = ss.get("episode_id")
                ep = None
                # Try to match by normalized date first
                if ss_date and ss_date in episode_map:
                    ep = episode_map[ss_date]
                # Fall back to episode_id matching
                elif ss_ep_id and ss_ep_id in episode_id_map:
                    ep = episode_id_map[ss_ep_id]
                
                if ep:
                    # Only update metrics that are missing, don't overwrite existing ones
                    metrics = ep.get("metrics", {})
                    for key in ["ahi", "odi", "rdi", "o2_nadir_pct", "supine_ahi", "rem_ahi", 
                               "time_below_90_pct", "sleep_efficiency_pct", "o2_mean_pct",
                               "non_supine_ahi", "nrem_ahi", "supine_rdi", "rem_rdi", 
                               "supine_odi", "rem_odi", "sleep_duration_h", "desaturation_events"]:
                        # Only add if missing or None
                        if key in metrics and metrics[key] is not None and (key not in ss or ss.get(key) is None):
                            ss[key] = metrics[key]
                    # Update episode_id and file_name if missing
                    if not ss.get("episode_id") and ep.get("episode_id"):
                        ss["episode_id"] = ep.get("episode_id")
                    if not ss.get("file_name") and ep.get("file_name"):
                        ss["file_name"] = ep.get("file_name")
                    if not ss.get("study_type") and ep.get("study_type"):
                        ss["study_type"] = ep.get("study_type")
            # Don't rebuild, just return - existing structure is preserved
            print(f"Preserved {len(existing_ss)} sleep studies in canonical_derived.timeline.sleep_studies")
    
    # Build new timeline if not preserving existing
    if not preserve_existing:
        tl["sleep_studies"] = []
        for e in episodes:
            # Extract AHI and other key metrics for UI compatibility
            metrics = e.get("metrics", {})
            ahi = metrics.get("ahi") or metrics.get("ahi_overall")
            date = e["observed_at"]
            
            # Build item with flattened metrics (matching the format from create_minimal_canonical_json_for_patient)
            item = {
                "source_kind": "sleep_study",
                "observed_at": e["observed_at"],
                "date": date,
                "episode_id": e.get("episode_id"),
                "file_name": e.get("file_name"),
                "study_type": e.get("study_type")
            }
            
            # Flatten metrics into item (matching expected format)
            for key in ["ahi", "odi", "rdi", "o2_nadir_pct", "supine_ahi", "rem_ahi", 
                       "time_below_90_pct", "sleep_efficiency_pct", "o2_mean_pct",
                       "non_supine_ahi", "nrem_ahi", "supine_rdi", "rem_rdi", 
                       "supine_odi", "rem_odi", "sleep_duration_h", "desaturation_events"]:
                if key in metrics and metrics[key] is not None:
                    item[key] = metrics[key]
            
            tl["sleep_studies"].append(item)

    # 3) Baseline / latest (sleep studies only) - only update if not already set with metrics
    if episodes:
        base, last = episodes[0], episodes[-1]
        def pick(sm):  # compact summary
            out = {}
            for k in ("ahi","ahi_rem","ahi_supine","o2_nadir_pct","odi","sleep_efficiency_pct","time_below_90_pct"):
                if k in sm: out[k] = sm[k]
            return out
        
        # Convert dates to string format (YYYY-MM-DD)
        def _date_to_str(d):
            if not d:
                return None
            if isinstance(d, str):
                return d[:10] if len(d) >= 10 else d
            if hasattr(d, 'strftime'):
                return d.strftime('%Y-%m-%d')
            return str(d)[:10]
        
        base_date = _date_to_str(base["observed_at"])
        last_date = _date_to_str(last["observed_at"])
        
        # Only update baseline/latest if they don't already have metrics
        existing_baseline = c.get("canonical_derived", {}).get("baseline", {}).get("sleep_study", {})
        existing_latest = c.get("canonical_derived", {}).get("latest", {}).get("sleep_study", {})
        
        # Check if existing baseline/latest have metrics (not just date)
        baseline_has_metrics = any(k in existing_baseline for k in ["ahi", "odi", "o2_nadir_pct"])
        latest_has_metrics = any(k in existing_latest for k in ["ahi", "odi", "o2_nadir_pct"])
        
        if not baseline_has_metrics:
            c["canonical_derived"]["baseline"] = {"sleep_study": {**pick(base["metrics"]), "date": base_date}}
        else:
            # Preserve existing but update date if different
            if existing_baseline.get("date") != base_date:
                c["canonical_derived"]["baseline"]["sleep_study"]["date"] = base_date
        
        if not latest_has_metrics:
            c["canonical_derived"]["latest"] = {"sleep_study": {**pick(last["metrics"]), "date": last_date}}
        else:
            # Preserve existing but update date if different
            if existing_latest.get("date") != last_date:
                c["canonical_derived"]["latest"]["sleep_study"]["date"] = last_date

    # 4) Respiratory indices = mirror from latest episode (single source of truth)
    if episodes:
        latest_m = episodes[-1]["metrics"]
        c["respiratory_indices"] = {
            "ahi_overall": latest_m.get("ahi"),
            "ahi_rem": latest_m.get("ahi_rem"),
            "ahi_nrem": latest_m.get("ahi_nrem"),
            "ahi_supine": latest_m.get("ahi_supine"),
            "ahi_non_supine": latest_m.get("ahi_non_supine"),
            "odi3": latest_m.get("odi") or latest_m.get("odi3"),
            "rdi_overall": latest_m.get("rdi") or latest_m.get("rdi_overall"),
        }
        # Keep a slim summary too
        c["sleep_study"] = {
            "ahi": latest_m.get("ahi"),
            "rem_ahi": latest_m.get("ahi_rem"),
            "supine_ahi": latest_m.get("ahi_supine"),
            "o2_nadir_pct": latest_m.get("o2_nadir_pct"),
            "odi": latest_m.get("odi") or latest_m.get("odi3"),
            "sleep_efficiency_pct": latest_m.get("sleep_efficiency_pct"),
            "time_below_90_pct": latest_m.get("time_below_90_pct"),
        }

    # 5) Reports: link & classify; never overwrite primary
    report_items = c.get("reported_metrics") or c.get("report_mentions") or []
    reconciled = []
    for rm in report_items:
        key = rm.get("key")
        val = rm.get("value")
        rep_date = rm.get("reported_at") or rm.get("mention_date")
        ep = _closest_episode(episodes, rep_date)
        disposition = _classify_report_metric(ep, key, val)
        rm["linked_episode_observed_at"] = ep["observed_at"] if ep else None
        rm["disposition"] = disposition
        reconciled.append(rm)
        # DO NOT write into primary metrics here; fill-only happens later if you want it

    c["reported_metrics"] = reconciled
    # optional: keep a lean "timeline.reports" only for display, but it won't feed the graph
    # Filter out ignored reports from timeline (they're still in reported_metrics for audit)
    tl["reports"] = [
        {k:v for k,v in {
            "reported_at": (ri.get("reported_at") or ri.get("mention_date")),
            "file_name": ri.get("file_name"),
            "key": ri.get("key"),
            "value": ri.get("value"),
            "disposition": ri.get("disposition"),
            "linked_episode_observed_at": ri.get("linked_episode_observed_at"),
            "source_kind": "report"
        }.items() if v is not None}
        for ri in reconciled
        if ri.get("disposition") != "ignored"  # Exclude ignored reports from timeline
    ]

    # 6) Remove noisy/duplicative blocks that confuse consumers
    for path in ("canonical_derived.timeline.reports_grouped",
                 "timeline.reports_grouped",
                 "positional_metrics"):
        # best-effort deep pop
        cur = c
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.get(p, {})
        cur.pop(parts[-1], None)

    # 7) Policy banner (so downstream knows what we did)
    c.setdefault("policy_flags", {})
    c["policy_flags"]["sleep_metrics_source"] = "sleep_study_only"
    c["policy_flags"]["reports_usage"] = "confirm_or_fill_only"
    c["policy_flags"]["trend_ignores_reports"] = True

    # 8) Report back conflicts for UI/QA
    conflicts = [ri for ri in reconciled if ri.get("disposition") == "conflicting"]
    summary = {
        "episodes": len(episodes),
        "report_metrics": len(reconciled),
        "conflicts": len(conflicts),
        "conflict_examples": conflicts[:5]
    }
    return c, summary

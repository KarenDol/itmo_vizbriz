from flask import Blueprint, jsonify, render_template, request
import json
import os
from datetime import datetime
from flask_app.models import Patient, PatientComment, PatientDeviceOrder
from flask_app import db

bp_sleep = Blueprint("sleep", __name__)

def _series_from_canonical(data):
    """Extract AHI time series from canonical data, preferring sleep_studies over reports"""
    tl = (data.get("canonical_derived") or {}).get("timeline") or {}
    rows = []

    # Sleep studies (preferred)
    for it in tl.get("sleep_studies", []):
        if it.get("date") and it.get("ahi") is not None:
            # Convert date to MM/DD/YYYY format if needed
            date_str = it["date"]
            if "-" in date_str:  # Convert from YYYY-MM-DD to MM/DD/YYYY
                from datetime import datetime
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    date_str = dt.strftime("%m/%d/%Y")
                except:
                    pass
            rows.append({"date": date_str, "ahi": float(it["ahi"]), "src": "sleep_study"})

    # Grouped reports
    for it in tl.get("reports_grouped", []):
        if it.get("date") and it.get("ahi") is not None:
            # Convert date to MM/DD/YYYY format if needed
            date_str = it["date"]
            if "-" in date_str:  # Convert from YYYY-MM-DD to MM/DD/YYYY
                from datetime import datetime
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    date_str = dt.strftime("%m/%d/%Y")
                except:
                    pass
            rows.append({"date": date_str, "ahi": float(it["ahi"]), "src": "report"})

    # Flat reports (key/value)
    for it in tl.get("reports", []):
        if it.get("key") == "ahi" and it.get("date"):
            # Convert date to MM/DD/YYYY format if needed
            date_str = it["date"]
            if "-" in date_str:  # Convert from YYYY-MM-DD to MM/DD/YYYY
                from datetime import datetime
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    date_str = dt.strftime("%m/%d/%Y")
                except:
                    pass
            rows.append({"date": date_str, "ahi": float(it["value"]), "src": "report"})

    # Prefer sleep_study over report on identical dates
    rows.sort(key=lambda r: (r["date"], 0 if r["src"] == "sleep_study" else 1))
    uniq = {}
    for r in rows:
        uniq[r["date"]] = r
    series = [uniq[d] for d in sorted(uniq.keys())]
    return series

def _events_from_canonical(data):
    """Extract timeline events from canonical data"""
    tl = (data.get("canonical_derived") or {}).get("timeline") or {}
    events = []
    for e in tl.get("events", []):
        if not e.get("date"):
            continue
        t = (e.get("type") or "").lower()
        # normalize device fitting synonyms
        if t in {"device_fitting", "device fitting"}:
            t = "device_delivery"
        if t not in {"consultation", "device_delivery", "titration"}:
            continue
        events.append({
            "date": e["date"],
            "type": t,
            "label": e.get("title") or t.title()
        })
    # sort by date
    events.sort(key=lambda x: x["date"])
    return events

def _events_from_database(patient_id):
    """Extract timeline events from database (comments and device orders)"""
    events = []
    
    # Get ALL comments - every comment should appear on timeline
    comments = PatientComment.query.filter_by(patient_id=patient_id).order_by(PatientComment.created_date.desc()).all()
    for comment in comments:
        if comment.created_date:
            # Determine event type based on comment_type (default to consultation for all)
            event_type = "consultation"  # default for all comments
            if comment.comment_type:
                comment_type_lower = comment.comment_type.lower()
                if comment_type_lower in ["titration", "adjustment", "advancement"]:
                    event_type = "titration"
                elif comment_type_lower in ["delivery", "device", "fitting"]:
                    event_type = "device_delivery"
                # All other types (initial, general, consultation, follow-up, etc.) are consultations
            
            # Create descriptive label
            label = comment.content
            if len(label) > 60:
                label = label[:57] + "..."
            
            # Add numeric value if present
            if comment.numeric_value and comment.numeric_unit:
                label += f" ({comment.numeric_value}{comment.numeric_unit})"
            
            # Add comment type to label for clarity (but make it more descriptive)
            if comment.comment_type:
                if comment.comment_type.lower() == "initial":
                    label = f"Initial Consultation: {label}"
                elif comment.comment_type.lower() == "general":
                    label = f"General Note: {label}"
                elif comment.comment_type.lower() not in ["consultation", "follow-up", "followup"]:
                    label = f"{comment.comment_type.title()}: {label}"
            
            events.append({
                "date": comment.created_date.strftime("%m/%d/%Y"),  # Match AHI date format
                "type": event_type,
                "label": label,
                "source": "comment",
                "comment_id": comment.id,
                "comment_type": comment.comment_type
            })
    
    # Get device orders
    device_orders = PatientDeviceOrder.query.filter_by(patient_id=patient_id).order_by(PatientDeviceOrder.fitting_date.desc()).all()
    for order in device_orders:
        if order.fitting_date:
            # Create descriptive label
            device_name = order.device_name if order.device_name else ""
            label = f"Device delivered: {order.device_type}"
            if device_name and device_name.lower() != "other":
                label += f" ({device_name})"
            
            events.append({
                "date": order.fitting_date.strftime("%m/%d/%Y"),  # Match AHI date format
                "type": "device_delivery",
                "label": label,
                "source": "device_order",
                "device_type": order.device_type,
                "device_name": device_name
            })
    
    # Sort by date (newest first for display)
    events.sort(key=lambda x: x["date"], reverse=True)
    return events

@bp_sleep.route("/sleep/timeline")
def sleep_timeline_page():
    """Server-render page; it fetches JSON via /sleep/timeline.json"""
    return render_template("sleep_timeline.html")

@bp_sleep.route("/sleep/timeline.json")
def sleep_timeline_json():
    """API endpoint that returns sleep metrics timeline data"""
    patient_id = request.args.get('patient_id', type=int)
    
    if not patient_id:
        return jsonify({"error": "patient_id required"}), 400
    
    try:
        # Get patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"error": "Patient not found"}), 404
        
        # Try to get canonical data first
        canonical_data = None
        if hasattr(patient, 'canonical_data') and patient.canonical_data:
            try:
                canonical_data = json.loads(patient.canonical_data)
            except:
                canonical_data = None
        
        # Extract AHI series from canonical data (if available)
        series = []
        if canonical_data:
            series = _series_from_canonical(canonical_data)
        
        # Always get events from database (comments and device orders)
        events = _events_from_database(patient_id)
        
        # If no series from canonical, create minimal series from events
        if not series:
            # Create a basic series that includes event dates for proper chart display
            from datetime import datetime, timedelta
            today = datetime.now().strftime("%m/%d/%Y")  # Use MM/DD/YYYY format
            
            # Add event dates to series so they appear on the chart
            event_dates = set()
            for event in events:
                if event.get('date'):
                    event_dates.add(event['date'])
            
            # Create series with event dates and current date
            series = []
            for date in sorted(event_dates):
                series.append({"date": date, "ahi": 0.0, "src": "estimated"})
            
            # Add current date if not already included
            if today not in event_dates:
                series.append({"date": today, "ahi": 0.0, "src": "estimated"})
        
        if not series:
            return jsonify({"series": [], "events": [], "kpis": {}})

        # Calculate KPIs
        initial = series[0]["ahi"]
        current = series[-1]["ahi"]
        improvement = 0.0 if initial == 0 else max(0.0, (initial - current) / initial * 100.0)

        payload = {
            "series": series,  # [{date, ahi}]
            "events": events,  # [{date, type, label}]
            "kpis": {
                "initial_ahi": round(initial, 1),
                "current_ahi": round(current, 1),
                "improvement_pct": round(improvement, 1)
            },
            "bands": {  # severity thresholds
                "mild_min": 5, "mild_max": 15,
                "moderate_min": 15, "moderate_max": 30,
                "severe_min": 30
            }
        }
        return jsonify(payload)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

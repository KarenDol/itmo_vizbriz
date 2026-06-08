import json
from datetime import datetime
from typing import Dict, Any, Tuple, List

from flask_app import db
from flask_app.models import PatientCaseEnvelope, ObservationStore

ALLOWED_TOP_LEVEL_KEYS = {
    'sleep_study',
    'observations',
    'treatment_considerations',
    'device_design',
    'follow_up_plan',
    'demographics',
    'confidence',
    'validation',
    'provenance',
}


def _filter_delta(delta: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(delta, dict):
        return {}
    return {k: v for k, v in delta.items() if k in ALLOWED_TOP_LEVEL_KEYS}


def _json_merge_patch(target: Any, patch: Any) -> Any:
    # RFC 7396
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    result = dict(target)
    for key, value in patch.items():
        if value is None:
            if key in result:
                del result[key]
        else:
            result[key] = _json_merge_patch(result.get(key), value)
    return result


def _upsert_per_report_envelope(patient_id: int, delta: Dict[str, Any]) -> None:
    report_id = (delta.get('report_meta') or {}).get('report_id') or 'unknown_report'
    
    # Try to get source_uri from report_meta, or construct a meaningful one
    source_uri = (delta.get('report_meta') or {}).get('source_uri')
    if not source_uri:
        # If no source_uri provided, create one based on report_id
        if report_id.endswith('.json'):
            source_uri = f'/api/delta/{report_id}'
        elif report_id.endswith('.pdf') or report_id.endswith('.docx'):
            source_uri = f'/home/ec2-user/vizbriz/report repository/{report_id}'
        else:
            source_uri = f'/api/delta/{report_id}'
    
    env = PatientCaseEnvelope.query.filter_by(patient_id=patient_id, report_id=report_id).first()
    if env:
        env.document_type = delta.get('document_type') or env.document_type
        env.source_uri = source_uri  # Always update with proper source_uri
        env.case_json = delta
        env.updated_at = datetime.utcnow()
    else:
        env = PatientCaseEnvelope(
            patient_id=patient_id,
            report_id=report_id,
            document_type=delta.get('document_type') or 'per_report_delta',
            source_uri=source_uri,
            case_json=delta,
            provider='system',
        )
        db.session.add(env)


def _upsert_canonical(patient_id: int, filtered_delta: Dict[str, Any]) -> Dict[str, Any]:
    canonical = PatientCaseEnvelope.query.filter_by(patient_id=patient_id, report_id='canonical').first()
    now_iso = datetime.utcnow().isoformat()
    if canonical is None:
        merged = {
            'schema_version': '1.0',
            'document_type': 'canonical',
            'patient_id': str(patient_id),
            'as_of': now_iso,
            'version': 1,
        }
    else:
        merged = dict(canonical.case_json or {})
    merged = _json_merge_patch(merged, filtered_delta)

    if canonical is None:
        canonical = PatientCaseEnvelope(
            patient_id=patient_id,
            report_id='canonical',
            document_type='canonical',
            source_uri='',
            case_json=merged,
            provider='system',
        )
        db.session.add(canonical)
    else:
        canonical.case_json = merged
        canonical.updated_at = datetime.utcnow()

    return merged


def _explode_and_store(patient_id: int, report_id: str, filtered_delta: Dict[str, Any]) -> None:
    # Only explode the changed subtree paths
    changed_rows: List[Tuple[str, Any]] = []

    def walk(prefix: str, node: Any):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, list):
            for item in node:
                # store list entries individually under the same path
                changed_rows.append((prefix, item))
        else:
            changed_rows.append((prefix, node))

    for k, v in filtered_delta.items():
        walk(k, v)

    # Dedupe by deleting existing rows for this report_id + path
    for path, value in changed_rows:
        ObservationStore.query.filter_by(
            patient_id=patient_id,
            file_name=report_id,
            source_type='case_report',
            source_text=path,
        ).delete()
        row = ObservationStore(
            patient_id=patient_id,
            file_name=report_id,
            source_type='case_report',
            source_text=path,
            extracted_observations={'path': path, 'value': value},
            provider='system',
        )
        db.session.add(row)


def apply_delta_for_patient(patient_id: int, delta_json: Dict[str, Any]) -> Dict[str, Any]:
    # Store per-report envelope for traceability
    _upsert_per_report_envelope(patient_id, delta_json)

    # Merge only allowed subtree into canonical
    filtered = _filter_delta(delta_json)
    merged = _upsert_canonical(patient_id, filtered)

    # Explode changed subtree into observation_store
    report_id = (delta_json.get('report_meta') or {}).get('report_id') or 'unknown_report'
    _explode_and_store(patient_id, report_id, filtered)

    db.session.commit()
    return {'success': True, 'patient_id': patient_id, 'report_id': report_id}


def backfill_canonical_from_complete_envelopes(patient_id: int) -> Dict[str, Any]:
    """Backfill canonical envelope with complete data from per-report envelopes."""
    try:
        # Get all per-report envelopes for this patient (excluding canonical)
        per_reports = PatientCaseEnvelope.query.filter_by(patient_id=patient_id).filter(
            PatientCaseEnvelope.report_id != 'canonical'
        ).all()
        
        if not per_reports:
            return {'success': False, 'message': 'No per-report envelopes found'}
        
        # Find the most complete envelope (has the most non-empty fields)
        most_complete = None
        max_fields = 0
        
        for env in per_reports:
            if not env.case_json:
                continue
            # Count non-empty fields
            field_count = 0
            for key, value in env.case_json.items():
                if key in ALLOWED_TOP_LEVEL_KEYS and value and value != {} and value != []:
                    if isinstance(value, dict):
                        field_count += len([v for v in value.values() if v and v != {} and v != []])
                    else:
                        field_count += 1
            
            if field_count > max_fields:
                max_fields = field_count
                most_complete = env
        
        if not most_complete:
            return {'success': False, 'message': 'No complete envelopes found'}
        
        # Use the most complete envelope as the base for canonical
        base_data = dict(most_complete.case_json)
        
        # Update metadata for canonical
        base_data.update({
            'document_type': 'canonical',
            'report_id': 'canonical',
            'as_of': datetime.utcnow().isoformat(),
            'canonical_meta': {
                'version': 1,
                'report_refs': [
                    {
                        'report_id': env.report_id,
                        'source_uri': env.source_uri or '',
                        'ingested_at': env.imported_at.isoformat() if env.imported_at else datetime.utcnow().isoformat()
                    }
                    for env in per_reports
                ]
            }
        })
        
        # Upsert canonical envelope
        canonical = PatientCaseEnvelope.query.filter_by(patient_id=patient_id, report_id='canonical').first()
        if canonical:
            canonical.case_json = base_data
            canonical.updated_at = datetime.utcnow()
        else:
            canonical = PatientCaseEnvelope(
                patient_id=patient_id,
                report_id='canonical',
                document_type='canonical',
                source_uri='',
                case_json=base_data,
                provider='system',
            )
            db.session.add(canonical)
        
        db.session.commit()
        
        return {
            'success': True,
            'patient_id': patient_id,
            'base_report_id': most_complete.report_id,
            'total_reports': len(per_reports)
        }
        
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'message': str(e)}


def backfill_all_canonicals() -> Dict[str, Any]:
    """Backfill canonical envelopes for all patients with per-report data."""
    try:
        # Get all patients with per-report envelopes but no canonical
        patients_with_reports = db.session.query(PatientCaseEnvelope.patient_id).filter(
            PatientCaseEnvelope.report_id != 'canonical'
        ).distinct().all()
        
        results = []
        for (patient_id,) in patients_with_reports:
            result = backfill_canonical_from_complete_envelopes(patient_id)
            results.append({'patient_id': patient_id, 'result': result})
        
        return {
            'success': True,
            'total_patients': len(patients_with_reports),
            'results': results
        }
        
    except Exception as e:
        return {'success': False, 'message': str(e)}



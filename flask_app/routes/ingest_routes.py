from flask import Blueprint, request, jsonify
from flask_login import login_required
from flask_app import db
from flask_app.models import Patient, ObservationStore, PatientCaseEnvelope
from flask_app.services.case_ingest import (
    scan_directory,
    extract_text_from_file,
    normalize_to_patient_case_json_v1,
    explode_observations_from_snapshot,
)
from datetime import datetime
from typing import Optional
import re
import os


ingest = Blueprint('ingest', __name__, url_prefix='/admin')


@ingest.route('/import_case_reports', methods=['POST'])
@login_required
def import_case_reports():
    payload = request.get_json(silent=True) or {}
    base_dir = payload.get('dir', '/home/ec2-user/vizbriz/report repository')
    dentist_id = int(payload.get('dentist_id', 9))
    status_val = payload.get('status', 'Archived')
    limit = int(payload.get('limit', 0) or 0)
    dry_run = bool(payload.get('dry_run', True))
    write_envelope = bool(payload.get('write_envelope', True))
    delete_after = bool(payload.get('delete_after', True))
    patient_id_override = payload.get('patient_id')
    infer_from_filename = bool(payload.get('infer_patient_id_from_filename', True))

    try:
        files = scan_directory(base_dir, limit=limit)
        results = []
        processed = 0

        for fpath in files:
            fname = fpath.split('/')[-1]
            # extract text
            text_content = extract_text_from_file(fpath)

            # derive minimal patient name from filename
            base_no_ext = fname.rsplit('.', 1)[0]
            patient_name = ' '.join(base_no_ext.replace('_', ' ').replace('-', ' ').split())[:100] or 'Case'

            # build patient snapshot JSON (id backfilled after flush)
            snapshot = normalize_to_patient_case_json_v1(
                file_path=fpath,
                file_name=fname,
                text_content=text_content,
                patient_id=None,
            )

            observations = explode_observations_from_snapshot(snapshot)

            if dry_run:
                results.append({
                    'file': fname,
                    'patient_preview': {'name': patient_name, 'dentist_id': dentist_id, 'status': status_val},
                    'patient_id_inferred': _infer_patient_id(fname) if infer_from_filename else None,
                    'patient_id_override': patient_id_override,
                    'snapshot_preview': snapshot,
                    'observations_preview': observations,
                })
                processed += 1
                continue

            # resolve target patient (override > inferred > new)
            target_patient = None
            target_patient_id = None
            if patient_id_override:
                try:
                    target_patient_id = int(patient_id_override)
                except Exception:
                    target_patient_id = None
            elif infer_from_filename:
                target_patient_id = _infer_patient_id(fname)

            if target_patient_id:
                target_patient = Patient.query.get(target_patient_id)

            if target_patient is None:
                # create a new patient if not found
                target_patient = Patient(name=patient_name, dentist_id=dentist_id, status=status_val)
                db.session.add(target_patient)
                db.session.flush()

            # backfill patient_id in snapshot and upsert envelope
            snapshot['patient_id'] = str(target_patient.id)

            if write_envelope:
                existing = PatientCaseEnvelope.query.filter_by(patient_id=target_patient.id, report_id=fname).first()
                if existing:
                    existing.case_json = snapshot
                    existing.source_uri = fpath
                    existing.document_type = snapshot.get('document_type')
                    existing.updated_at = datetime.utcnow()
                else:
                    env = PatientCaseEnvelope(
                        patient_id=target_patient.id,
                        report_id=fname,
                        document_type=snapshot.get('document_type'),
                        source_uri=fpath,
                        case_json=snapshot,
                        provider='system',
                    )
                    db.session.add(env)

            # Remove any prior observations for this patient+file to avoid duplication
            try:
                ObservationStore.query.filter_by(
                    patient_id=target_patient.id,
                    file_name=fname,
                    source_type='case_report',
                ).delete()
            except Exception:
                pass

            # write observations (simple explode; schema-guided)
            for ob in observations:
                row = ObservationStore(
                    patient_id=target_patient.id,
                    file_name=fname,
                    source_type='case_report',
                    source_text=ob.get('path'),
                    extracted_observations=ob,
                    provider='system',
                )
                db.session.add(row)

            db.session.commit()
            deleted_flag = False
            if delete_after:
                try:
                    os.remove(fpath)
                    deleted_flag = True
                except Exception:
                    deleted_flag = False
            results.append({'file': fname, 'patient_id': target_patient.id, 'observations': len(observations), 'deleted': deleted_flag})
            processed += 1

        return jsonify(success=True, processed=processed, results=results)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e)), 500



def _infer_patient_id(file_name: str) -> Optional[int]:
    """Infer a 5-digit patient id from filename like 'case Rol-10309.docx.pdf'."""
    try:
        m = re.search(r'(\d{5})', file_name)
        if m:
            return int(m.group(1))
    except Exception:
        return None
    return None

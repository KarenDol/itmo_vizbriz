"""
Admin Routes - Patient Management Dashboard
Admin-only routes for comprehensive patient management and workflow oversight
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from flask_app.models import db, Patient, File, Dentist, AdminFile, Claim, Comment, StatusOption, PatientComment, Clinic, DSO, dentist_clinic_association, L4DeviceDesign, L4DeviceOption
from sqlalchemy import or_
from flask_app.config.manifest_config import get_manifest_definition, get_next_step_for_stage, get_stage_by_key
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func, desc
from datetime import datetime, timedelta
import logging
import json
import os

# Create admin blueprint
admin = Blueprint('admin', __name__, url_prefix='/admin')

logger = logging.getLogger(__name__)

def admin_required(f):
    """Decorator to ensure only admin users can access the route"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function


def _documentation_base_dir() -> str:
    """Same logic as docs_routes: DOCUMENTATION_DIR env, or repo 'Documentation', or flask_app/static/documentation."""
    env_dir = os.environ.get("DOCUMENTATION_DIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return os.path.abspath(env_dir)
    repo_docs = os.path.abspath(os.path.join(current_app.root_path, "..", "Documentation"))
    if os.path.isdir(repo_docs):
        return repo_docs
    return os.path.abspath(os.path.join(current_app.root_path, "static", "documentation"))


def _documentation_manifest_path() -> str:
    return os.path.join(_documentation_base_dir(), 'documentation_manifest.json')


def _load_doc_manifest() -> dict:
    path = _documentation_manifest_path()
    if not os.path.exists(path):
        return {'english': {'Imaging protocols': []}, 'hebrew': {'הוראות למטופל': []}, 'hidden_system': {'english': {}, 'hebrew': {}}}
    with open(path, 'r', encoding='utf-8') as f:
        manifest = json.load(f) or {}
    manifest.setdefault('english', {})
    manifest.setdefault('hebrew', {})
    manifest.setdefault('hidden_system', {'english': {}, 'hebrew': {}})
    manifest['english'].setdefault('Imaging protocols', [])
    manifest['hebrew'].setdefault('הוראות למטופל', [])
    return manifest


def _save_doc_manifest(manifest: dict) -> None:
    path = _documentation_manifest_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _safe_folder_name(name: str) -> str:
    cleaned = (name or '').strip()
    cleaned = cleaned.replace('\\', '/')
    cleaned = cleaned.replace('..', '')
    cleaned = cleaned.strip('/').strip()
    return cleaned[:64]


@admin.route('/documentation', methods=['GET'])
@login_required
@admin_required
def documentation_manager():
    manifest = _load_doc_manifest()
    # Ensure base dirs exist
    docs_dir = _documentation_base_dir()
    os.makedirs(os.path.join(docs_dir, 'english'), exist_ok=True)
    os.makedirs(os.path.join(docs_dir, 'hebrew'), exist_ok=True)
    for folder in (manifest.get('english') or {}).keys():
        os.makedirs(os.path.join(docs_dir, 'english', _safe_folder_name(folder) or 'Imaging protocols'), exist_ok=True)
    for folder in (manifest.get('hebrew') or {}).keys():
        os.makedirs(os.path.join(docs_dir, 'hebrew', _safe_folder_name(folder) or 'הוראות למטופל'), exist_ok=True)
    _save_doc_manifest(manifest)

    folders_by_lang = {
        'english': sorted(list((manifest.get('english') or {}).keys())),
        'hebrew': sorted(list((manifest.get('hebrew') or {}).keys())),
    }

    system_items = {
        'english': {
            'Imaging protocols': [
                {'key': 'intraoral', 'title': 'Intraoral protocol', 'url': '/imaging-protocols/intraoral'},
                {'key': 'clinical', 'title': 'Clinical photos protocol', 'url': '/imaging-protocols/clinical'},
                {'key': 'cbct', 'title': 'CBCT protocol', 'url': '/imaging-protocols/cbct'},
            ]
        },
        'hebrew': {
            'הוראות למטופל': [
                {'key': 'oral_appliance_care', 'title': 'הוראות למטופל - טיפול ושימוש בהתקן האוראלי', 'url': '/documentation/patient/oral_appliance_care'},
                {'key': 'post_delivery_instructions', 'title': 'הנחיות לאחר קבלת התקן אורלי לטיפול בדום נשימה חסימתי בשינה', 'url': '/documentation/patient/post_delivery_instructions'},
                {'key': 'informed_consent_sleep_related_breathing', 'title': 'הסכמה מדעת לטיפול בהפרעות נשימה הקשורות לשינה', 'url': '/documentation/patient/informed_consent_sleep_related_breathing'},
            ]
        }
    }
    # Use url_for for imaging so they point to /documentation/imaging/...
    for item in system_items['english']['Imaging protocols']:
        key = item['key']
        item['url'] = url_for('main.download_imaging_protocol', protocol_key=key)

    return render_template('admin/documentation_manager.html', folders_by_lang=folders_by_lang, doc_manifest=manifest, system_items=system_items)


@admin.route('/documentation/upload', methods=['POST'])
@login_required
@admin_required
def documentation_upload():
    try:
        uploaded = request.files.get('file')
        title = (request.form.get('title') or '').strip()
        language = (request.form.get('language') or '').strip().lower()
        folder_new = _safe_folder_name(request.form.get('folder_new'))
        folder_existing = (request.form.get('folder_existing') or '').strip()

        if not uploaded:
            flash('File is required.', 'error')
            return redirect(url_for('admin.documentation_manager'))

        if language not in {'english', 'hebrew'}:
            flash('Please choose a language.', 'error')
            return redirect(url_for('admin.documentation_manager'))

        folder = ''
        if folder_existing:
            try:
                lang_from_existing, folder_from_existing = folder_existing.split('::', 1)
                lang_from_existing = (lang_from_existing or '').strip().lower()
                folder_from_existing = _safe_folder_name(folder_from_existing)
                if lang_from_existing in {'english', 'hebrew'} and folder_from_existing:
                    language = lang_from_existing
                    folder = folder_from_existing
            except ValueError:
                pass
        if not folder:
            folder = folder_new
        if not folder:
            flash('Please select an existing folder or create a new folder.', 'error')
            return redirect(url_for('admin.documentation_manager'))

        manifest = _load_doc_manifest()
        docs_dir = _documentation_base_dir()
        folder_dir = os.path.join(docs_dir, language, folder)
        os.makedirs(folder_dir, exist_ok=True)

        original_filename = (uploaded.filename or '').strip()
        filename = secure_filename(original_filename)
        if not filename:
            _, ext = os.path.splitext(original_filename)
            ext = (ext or '').strip()
            if ext and not ext.startswith('.'):
                ext = f".{ext}"
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            filename = f"document_{timestamp}{ext}"

        save_path = os.path.join(folder_dir, filename)
        uploaded.save(save_path)

        if not title:
            original_base = os.path.splitext(os.path.basename(original_filename))[0]
            title = (original_base or os.path.basename(original_filename) or os.path.splitext(filename)[0] or filename).strip()

        rel_path = os.path.join(folder, filename).replace('\\', '/')
        manifest.setdefault(language, {})
        manifest[language].setdefault(folder, [])
        manifest[language][folder].append({'title': title, 'path': rel_path})
        _save_doc_manifest(manifest)

        flash('Uploaded.', 'success')
        return redirect(url_for('admin.documentation_manager'))
    except Exception as e:
        logger.error(f"Documentation upload failed: {e}")
        flash('Upload failed.', 'error')
        return redirect(url_for('admin.documentation_manager'))


@admin.route('/documentation/delete', methods=['POST'])
@login_required
@admin_required
def documentation_delete():
    try:
        language = (request.form.get('language') or '').strip().lower()
        rel_path = (request.form.get('path') or '').strip()
        if language not in {'english', 'hebrew'} or not rel_path:
            flash('Invalid delete request.', 'error')
            return redirect(url_for('admin.documentation_manager'))

        safe_rel = os.path.normpath(rel_path).replace('\\', '/')
        if safe_rel.startswith('..') or os.path.isabs(safe_rel):
            flash('Invalid path.', 'error')
            return redirect(url_for('admin.documentation_manager'))

        manifest = _load_doc_manifest()
        lang_section = manifest.get(language) or {}
        removed = False
        for folder_name, items in list(lang_section.items()):
            if not isinstance(items, list):
                continue
            new_items = [it for it in items if (it or {}).get('path') != safe_rel]
            if len(new_items) != len(items):
                removed = True
                lang_section[folder_name] = new_items
        manifest[language] = lang_section
        _save_doc_manifest(manifest)

        docs_dir = _documentation_base_dir()
        base_dir = os.path.abspath(os.path.join(docs_dir, language))
        file_path = os.path.abspath(os.path.join(base_dir, safe_rel))
        if file_path.startswith(base_dir + os.sep) and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                logger.warning(f"Failed to delete file: {file_path}")

        flash('Deleted.' if removed else 'Not found.', 'success')
        return redirect(url_for('admin.documentation_manager'))
    except Exception as e:
        logger.error(f"Documentation delete failed: {e}")
        flash('Delete failed.', 'error')
        return redirect(url_for('admin.documentation_manager'))


@admin.route('/documentation/hide-system', methods=['POST'])
@login_required
@admin_required
def documentation_hide_system():
    try:
        language = (request.form.get('language') or '').strip().lower()
        folder = (request.form.get('folder') or '').strip()
        key = (request.form.get('key') or '').strip()
        if language not in {'english', 'hebrew'} or not folder or not key:
            flash('Invalid request.', 'error')
            return redirect(url_for('admin.documentation_manager'))

        manifest = _load_doc_manifest()
        hidden = manifest.setdefault('hidden_system', {'english': {}, 'hebrew': {}})
        hidden.setdefault('english', {})
        hidden.setdefault('hebrew', {})
        hidden.setdefault(language, {})
        hidden[language].setdefault(folder, [])
        if key not in hidden[language][folder]:
            hidden[language][folder].append(key)
        _save_doc_manifest(manifest)

        flash('Hidden.', 'success')
        return redirect(url_for('admin.documentation_manager'))
    except Exception as e:
        logger.error(f"Hide system doc failed: {e}")
        flash('Action failed.', 'error')
        return redirect(url_for('admin.documentation_manager'))

@admin.route('/patient-management')
@login_required
@admin_required
def patient_management_dashboard():
    """Admin patient management dashboard with comprehensive overview"""
    try:
        # Get manifest definition for stages
        manifest = get_manifest_definition()
        
        # Get all non-archived patients with their latest stage information
        patients = Patient.query.filter(Patient.status != 'Archived').order_by(desc(Patient.create_date)).all()
        
        # Calculate metrics based on patient stages
        metrics = calculate_patient_metrics(patients, manifest)
        
        # Get patient list with stage information (LIFO - Last In, First Out)
        patient_list = get_patient_list_with_stages(patients, manifest)
        
        return render_template('admin/patient_management_dashboard.html',
                             metrics=metrics,
                             patients=patient_list,
                             manifest=manifest)
        
    except Exception as e:
        logger.error(f"Error in admin patient management dashboard: {str(e)}")
        flash('Error loading patient management dashboard', 'error')
        return redirect(url_for('main.index'))

@admin.route('/api/patient-search')
@login_required
def patient_search():
    """API endpoint for patient search with permission-based filtering"""
    try:
        search_term = request.args.get('q', '').strip()
        logger.info(f"Patient search called with term: '{search_term}' by user {current_user.email} (role: {current_user.role})")
        
        if not search_term:
            logger.info("No search term provided, returning empty results")
            return jsonify({'patients': []})
        
        # Build base query with search filters
        base_query = Patient.query.filter(
            Patient.status != 'Archived',
            db.or_(
                Patient.name.ilike(f'%{search_term}%'),
                Patient.email.ilike(f'%{search_term}%'),
                Patient.id == search_term if search_term.isdigit() else False
            )
        )
        
        # Apply permission-based filtering (same logic as admin_home)
        if current_user.role == 'admin':
            # Admin can see all patients
            patients = base_query.order_by(desc(Patient.create_date)).limit(20).all()
        elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
            # Dentist can only see patients associated with the same clinic(s) as the dentist
            dentist_clinic_ids = current_user.get_clinic_ids()
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
            
            if dentist_clinic_ids:
                patients = (base_query
                         .filter(
                             db.or_(
                                 # Patients directly assigned to dentist's clinics
                                 Patient.clinic_id.in_(dentist_clinic_ids),
                                 # Patients whose dentists work at the same clinics
                                 db.and_(
                                     Patient.clinic_id.is_(None),
                                     Patient.dentist_id.isnot(None),
                                     db.exists().where(
                                         db.and_(
                                             dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                             dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                                         )
                                     )
                                 )
                             )
                         )
                         .order_by(desc(Patient.create_date))
                         .limit(20)
                         .all())
            else:
                # No clinic associations found - try DSO fallback
                if dentist_dso_ids:
                    patients = (base_query
                             .join(Dentist)
                             .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                             .filter(
                                 db.or_(
                                     Clinic.dso_id.in_(dentist_dso_ids),
                                     db.and_(Patient.clinic_id.is_(None), Dentist.DSO == getattr(current_user, 'DSO', None))
                                 )
                             )
                             .order_by(desc(Patient.create_date))
                             .limit(20)
                             .all())
                else:
                    patients = []
        else:
            # Other users get no patients
            patients = []
        
        logger.info(f"Found {len(patients)} patients for search term: '{search_term}'")
        
        # Get manifest for stage information
        manifest = get_manifest_definition()
        
        # Format patient data
        patient_data = []
        for patient in patients:
            latest_stage = get_latest_completed_stage(patient.id, manifest)
            next_stage = get_next_stage_for_patient(patient.id, manifest)
            
            # Get DSO and clinic information
            dso_name = None
            clinic_name = None
            if patient.clinic_id:
                from flask_app.models import Clinic, DSO
                clinic = Clinic.query.get(patient.clinic_id)
                if clinic:
                    clinic_name = clinic.name
                    if clinic.dso_info:
                        dso_name = clinic.dso_info.name
            
            # Calculate age if DOB is available
            age = None
            if patient.dob:
                from datetime import date
                today = date.today()
                age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
            
            patient_data.append({
                'id': patient.id,
                'name': patient.name,
                'email': patient.email,
                'phone': patient.phone,
                'gender': patient.gender,
                'age': age,
                'dso_name': dso_name,
                'clinic_name': clinic_name,
                'created_at': patient.create_date.strftime('%Y-%m-%d %H:%M') if patient.create_date else '',
                'latest_stage': latest_stage,
                'next_stage': next_stage,
                'workflow_url': url_for('main.patient_workflow_manifest', patient_id=patient.id)
            })
        
        return jsonify({'patients': patient_data})
        
    except Exception as e:
        logger.error(f"Error in patient search: {str(e)}")
        return jsonify({'error': 'Search failed'}), 500

@admin.route('/api/debug-patients')
@login_required
def debug_patients():
    """Debug endpoint to check if patients exist"""
    try:
        # Get total count of patients
        total_patients = Patient.query.count()
        non_archived = Patient.query.filter(Patient.status != 'Archived').count()
        
        # Get a few sample patients
        sample_patients = Patient.query.filter(Patient.status != 'Archived').limit(5).all()
        
        sample_data = []
        for patient in sample_patients:
            sample_data.append({
                'id': patient.id,
                'name': patient.name,
                'email': patient.email,
                'status': patient.status
            })
        
        return jsonify({
            'total_patients': total_patients,
            'non_archived_patients': non_archived,
            'sample_patients': sample_data
        })
        
    except Exception as e:
        logger.error(f"Error in debug patients: {str(e)}")
        return jsonify({'error': str(e)}), 500

def calculate_patient_metrics(patients, manifest):
    """Calculate key metrics for the dashboard"""
    try:
        # Initialize counters
        new_patients = 0
        waiting_sleep_test = 0
        waiting_dental_consult = 0
        waiting_reports = 0
        
        # Define stage keys for categorization
        sleep_test_stages = ['sleep_study_scheduled', 'sleep_test_completed']
        dental_consult_stages = ['dental_sleep_doctor_consult_scheduled', 'met_with_dental_sleep_expert']
        report_stages = ['osa_report_ready', 'dental_approval_osa_report']
        
        for patient in patients:
            # Get patient's current stage from manifest
            patient_manifest = get_patient_manifest_data(patient.id)
            
            if not patient_manifest:
                # New patient (no manifest data)
                new_patients += 1
                continue
            
            # Find the latest completed stage
            latest_completed = None
            for stage in manifest:
                stage_key = stage['key']
                if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                    latest_completed = stage_key
            
            if latest_completed:
                if latest_completed in sleep_test_stages:
                    waiting_sleep_test += 1
                elif latest_completed in dental_consult_stages:
                    waiting_dental_consult += 1
                elif latest_completed in report_stages:
                    waiting_reports += 1
        
        return {
            'total_patients': len(patients),
            'new_patients': new_patients,
            'waiting_sleep_test': waiting_sleep_test,
            'waiting_dental_consult': waiting_dental_consult,
            'waiting_reports': waiting_reports
        }
        
    except Exception as e:
        logger.error(f"Error calculating patient metrics: {str(e)}")
        return {
            'total_patients': 0,
            'new_patients': 0,
            'waiting_sleep_test': 0,
            'waiting_dental_consult': 0,
            'waiting_reports': 0
        }

def get_patient_list_with_stages(patients, manifest):
    """Get patient list with stage information (LIFO order)"""
    try:
        patient_list = []
        
        for patient in patients:
            # Get patient's manifest data
            patient_manifest = get_patient_manifest_data(patient.id)
            
            # Get latest completed stage
            latest_stage = get_latest_completed_stage(patient.id, manifest)
            
            # Get next stage
            next_stage = get_next_stage_for_patient(patient.id, manifest)
            
            patient_data = {
                'id': patient.id,
                'name': patient.name,
                'email': patient.email,
                'created_at': patient.create_date,
                'status': patient.status,
                'latest_stage': latest_stage,
                'next_stage': next_stage,
                'workflow_url': url_for('main.patient_workflow_manifest', patient_id=patient.id)
            }
            
            patient_list.append(patient_data)
        
        # Sort by created_at descending (LIFO)
        patient_list.sort(key=lambda x: x['created_at'], reverse=True)
        
        return patient_list
        
    except Exception as e:
        logger.error(f"Error getting patient list with stages: {str(e)}")
        return []

def get_patient_manifest_data(patient_id):
    """Get patient manifest data from database"""
    conn = None
    try:
        import mysql.connector
        
        # Database configuration
        DB_CONFIG = {
            'host': os.getenv('DB_HOST', 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com'),
            'user': os.getenv('DB_USERNAME', 'admin'),
            'password': os.getenv('DB_PASSWORD', 'Vizbriz2025!'),
            'database': os.getenv('DB_NAME', 'vizbriz'),
            'port': int(os.getenv('DB_PORT', '3306'))
        }
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get all manifest entries for this patient
        cursor.execute("""
            SELECT * FROM patient_manifest 
            WHERE patient_id = %s 
            ORDER BY stage_number
        """, (patient_id,))
        manifest_entries = cursor.fetchall()
        
        # Create a dictionary of manifest entries by stage_key
        manifest_dict = {}
        for entry in manifest_entries:
            manifest_dict[entry['stage_key']] = {
                'is_completed': entry.get('is_completed', False),
                'completion_date': entry.get('completion_date'),
                'status_message': entry.get('status_message', ''),
                'stage_data': entry.get('stage_data')
            }
        
        return manifest_dict
        
    except Exception as e:
        logger.error(f"Error getting patient manifest data: {str(e)}")
        return None
    finally:
        if conn:
            conn.close()

def get_latest_completed_stage(patient_id, manifest):
    """Get the latest completed stage for a patient"""
    try:
        patient_manifest = get_patient_manifest_data(patient_id)
        if not patient_manifest:
            return None
        
        # Find the highest stage number that is completed
        latest_completed = None
        for stage in manifest:
            stage_key = stage['key']
            if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                latest_completed = stage
        
        return latest_completed
        
    except Exception as e:
        logger.error(f"Error getting latest completed stage: {str(e)}")
        return None

def get_next_stage_for_patient(patient_id, manifest):
    """Get the next stage for a patient"""
    try:
        latest_stage = get_latest_completed_stage(patient_id, manifest)
        if not latest_stage:
            # If no completed stages, next stage is the first one
            return manifest[0] if manifest else None
        
        # Get the next stage from manifest
        current_stage_number = latest_stage.get('stage_number', 0)
        next_stage_number = current_stage_number + 1
        
        for stage in manifest:
            if stage.get('stage_number') == next_stage_number:
                return stage
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting next stage for patient: {str(e)}")
        return None


def _build_causality_data(all_designs):
    """
    Build causality table data from L4 device designs: one row per report/device context.
    Joins with Patient and Dentist, deduplicates by patient. Returns list of row dicts.
    """
    import re
    seen_patient_ids = set()
    causality_data = []
    for design in all_designs:
        patient = None
        dentist = None
        if design.patient_id:
            try:
                if design.patient_id.isdigit():
                    patient = Patient.query.get(int(design.patient_id))
                else:
                    numbers = re.findall(r'\d+', design.patient_id)
                    for num in numbers:
                        patient = Patient.query.get(int(num))
                        if patient:
                            break
            except Exception:
                pass
        if patient and patient.dentist_id:
            dentist = Dentist.query.get(patient.dentist_id)
        patient_identifier = (
            f"patient_{patient.id}" if patient else
            f"report_patient_{design.patient_id}" if design.patient_id else
            f"report_{design.source_report_id}"
        )
        if patient_identifier in seen_patient_ids:
            continue
        seen_patient_ids.add(patient_identifier)
        options = L4DeviceOption.query.filter_by(
            source_report_id=design.source_report_id,
            design_context=design.design_context
        ).all()
        causality_row = {
            'report_id': design.source_report_id or 'Unknown',
            'patient_id': design.patient_id or '',
            'patient_name': patient.name if patient else '',
            'dentist_name': dentist.name if dentist else '',
            'design_context': design.design_context or '',
            'ahi': design.ahi or '',
            'rdi': design.rdi or '',
            'odi': design.odi or '',
            'o2_nadir': design.o2_nadir or '',
            'snoring_level': design.snoring_level or '',
            'clinical_background': design.clinical_background or '',
            'patient_complaints': design.patient_complaints or '',
            'obstruction_sites': design.obstruction_sites or '',
            'tongue_position': design.tongue_position or '',
            'bite_structure': design.bite_structure or '',
            'soft_palate_uvula': design.soft_palate_uvula or '',
            'treatment_considerations': design.treatment_considerations or '',
            'mandibular_advancement': design.mandibular_advancement or '',
            'preset_mm': design.preset_mm or '',
            'vertical_opening': design.vertical_opening or '',
            'anterior_window': design.anterior_window or '',
            'material': design.material or '',
            'retention_features': design.retention_features or '',
            'coverage_notes': design.coverage_notes or '',
            'clinical_notes': design.clinical_notes or '',
            'device_options': ', '.join([opt.device_name for opt in options]) if options else '',
            'device_options_list': [opt.device_name for opt in options] if options else [],
            'extraction_confidence': design.extraction_confidence or 'med',
        }
        causality_data.append(causality_row)
    return causality_data


@admin.route('/patient-observations-devices', methods=['GET'])
@login_required
@admin_required
def patient_observations_devices():
    """
    Admin page showing Level 4 report data: Clinical context, Device Design, Device Options
    Shows only Level 4 report observations (not all patient observations)
    Matches the case card structure used for KB uploads
    """
    try:
        all_designs = L4DeviceDesign.query.order_by(L4DeviceDesign.created_at.desc()).all()
        reports_dict = {}
        for design in all_designs:
            report_id = design.source_report_id
            if report_id not in reports_dict:
                report_designs = L4DeviceDesign.query.filter_by(source_report_id=report_id).all()
                report_options = L4DeviceOption.query.filter_by(source_report_id=report_id).all()
                reports_dict[report_id] = {
                    'source_report_id': report_id,
                    'patient_id': design.patient_id,
                    'device_designs': report_designs,
                    'device_options': report_options,
                    'created_at': design.created_at
                }
        report_data_list = list(reports_dict.values())
        causality_data = _build_causality_data(all_designs)
        return render_template('admin/patient_observations_devices.html',
                             report_data_list=report_data_list,
                             causality_data=causality_data)
    except Exception as e:
        logger.error(f"Error loading Level 4 report data: {e}", exc_info=True)
        flash(f'Error loading data: {str(e)}', 'error')
        return redirect(url_for('admin.documentation_manager'))


@admin.route('/query-level4-patterns', methods=['POST'])
@login_required
@admin_required
def query_level4_patterns():
    """
    Let the user query the LLM about patterns and design in the Level 4 report data.
    Sends the current causality table data (observations + device design + options) to the LLM
    with a system prompt so the LLM answers only from the provided data.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON body'}), 400
        query = (data.get('query') or '').strip()
        if not query:
            return jsonify({'success': False, 'error': 'Query is required'}), 400

        all_designs = L4DeviceDesign.query.order_by(L4DeviceDesign.created_at.desc()).all()
        causality_data = _build_causality_data(all_designs)

        # Limit data size to avoid token limits (e.g. ~80 rows or ~35k chars)
        max_chars = 35000
        total_rows = len(causality_data)
        if total_rows > 80:
            causality_data = causality_data[:80]
        data_str = json.dumps(causality_data, indent=0, default=str)
        if total_rows > 80:
            data_str += '\n... (showing first 80 of {} rows)'.format(total_rows)
        if len(data_str) > max_chars:
            data_str = data_str[:max_chars] + '\n... (data truncated)'

        system_instruction = """You are an expert analyst for Level 4 sleep study reports and oral appliance design.

You have been given a JSON table of real data from processed Level 4 reports. Each row contains:
- Observations: AHI, RDI, ODI, O2 nadir, snoring level, clinical background, patient complaints, obstruction sites, tongue position, bite structure, soft palate/uvula, treatment considerations.
- Device design: design_context (e.g. Nighttime MAD, Daytime TMJ), mandibular_advancement, preset_mm, vertical_opening, anterior_window, material, retention_features, coverage_notes, clinical_notes.
- Device options: list of recommended device names.

Your task: Answer the user's question about PATTERNS and DESIGN. PRIORITIZE the provided table data - identify trends (e.g. high AHI vs advancement, common materials, common devices), summarize design choices, or suggest design implications. You may also reference Level 4 report knowledge (style guidelines, clinical protocols) from the knowledge base for additional context, but the table data is your primary source. Be concise and evidence-based; cite the data (e.g. "In X rows with AHI > 15..."). If the data is empty or too small to answer, say so. Do not make up data."""

        user_content = f"""DATA (Level 4 report rows):\n{data_str}\n\nUSER QUESTION: {query}"""

        from flask_app.config.bedrock_config import query_bedrock_claude_enhanced
        messages = [{'role': 'user', 'content': system_instruction + '\n\n' + user_content}]
        result = query_bedrock_claude_enhanced(
            messages,
            max_tokens=1500,
            temperature=0.2,
            use_knowledge_base=True,  # Enable KB to get Level 4 report style/clinical context
            endpoint='admin_level4_patterns'
        )

        if result.get('success') and result.get('response'):
            out = {'success': True, 'response': result['response']}
            # Include KB sources when data came from the Knowledge Base
            citations = result.get('knowledge_base_citations') or []
            if citations:
                import re
                sources = []
                for c in citations:
                    uri = c.get('uri') or (c.get('s3Location') or {}).get('uri') or ''
                    if uri:
                        # Show filename (last path segment) as source label
                        uri_clean = re.sub(r'^s3://[^/]+/', '', uri)
                        name = uri_clean.split('/')[-1] if '/' in uri_clean else uri_clean
                        if name and name not in sources:
                            sources.append(name)
                if sources:
                    out['sources'] = sources
            return jsonify(out)
        return jsonify({
            'success': False,
            'error': result.get('message', 'LLM did not return a response')
        }), 400
    except Exception as e:
        logger.error(f"Error in query-level4-patterns: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin.route('/generate-decision-tree', methods=['POST'])
@login_required
@admin_required
def generate_decision_tree():
    """
    Generate a decision tree from Level 4 report data.
    Uses structured reasoning to create IF-THEN rules mapping observations → device design.
    Returns a JSON decision tree structure.
    """
    try:
        all_designs = L4DeviceDesign.query.order_by(L4DeviceDesign.created_at.desc()).all()
        causality_data = _build_causality_data(all_designs)
        
        if not causality_data:
            return jsonify({
                'success': False,
                'error': 'No data available. Please process Level 4 reports first.'
            }), 400
        
        # Limit data size
        max_chars = 35000
        total_rows = len(causality_data)
        if total_rows > 80:
            causality_data = causality_data[:80]
        data_str = json.dumps(causality_data, indent=0, default=str)
        if total_rows > 80:
            data_str += '\n... (showing first 80 of {} rows)'.format(total_rows)
        if len(data_str) > max_chars:
            data_str = data_str[:max_chars] + '\n... (data truncated)'
        
        system_instruction = """You are an expert at creating clinical decision trees from data patterns.

Your task: Analyze the provided Level 4 report data and create a structured decision tree that maps clinical observations → device design recommendations.

OUTPUT FORMAT (JSON):
{
  "tree_name": "Level 4 Device Design Decision Tree",
  "description": "Brief description of the tree's purpose",
  "nodes": [
    {
      "id": "node_1",
      "type": "decision",  // "decision" or "outcome"
      "condition": "IF AHI > 15 AND obstruction_sites contains 'tongue_base'",
      "children": ["node_2", "node_3"],  // IDs of child nodes
      "outcome": null  // null for decision nodes
    },
    {
      "id": "node_2",
      "type": "outcome",
      "condition": null,
      "children": [],
      "outcome": {
        "mandibular_advancement": "6-8mm",
        "vertical_opening": "2-3mm",
        "anterior_window": "Medium",
        "material": "Specific material if pattern found",
        "device_options": ["Device names if pattern found"],
        "confidence": "high|medium|low",
        "data_support": "X cases in dataset support this"
      }
    }
  ],
  "root_node": "node_1",
  "summary": "Key patterns identified: ..."
}

RULES:
1. Start with most discriminating factors (e.g., AHI severity, obstruction sites)
2. Create clear IF-THEN conditions based on actual data patterns
3. Each outcome should include specific device design parameters found in the data
4. Include confidence levels and data support counts
5. Handle missing data gracefully (use "unknown" or "varies" when no clear pattern)
6. Focus on actionable decision points that clinicians can use

Be specific: cite actual values from the data (e.g., "In 5 cases with AHI > 20, mandibular_advancement was 6-8mm")."""

        user_content = f"""DATA (Level 4 report rows):\n{data_str}\n\nTASK: Create a decision tree that helps clinicians choose device design parameters based on observations. Focus on the strongest patterns in the data."""

        from flask_app.config.bedrock_config import query_bedrock_claude_enhanced
        messages = [{'role': 'user', 'content': system_instruction + '\n\n' + user_content}]
        result = query_bedrock_claude_enhanced(
            messages,
            max_tokens=3000,  # More tokens for structured output
            temperature=0.1,  # Lower temperature for more consistent structure
            use_knowledge_base=True,
            endpoint='admin_decision_tree'
        )
        
        if result.get('success') and result.get('response'):
            response_text = result['response']
            # Try to extract JSON from response (might be wrapped in markdown code blocks)
            import re
            json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', response_text, re.DOTALL)
            if json_match:
                try:
                    decision_tree = json.loads(json_match.group(1))
                except:
                    decision_tree = None
            else:
                # Try to find JSON object directly
                try:
                    decision_tree = json.loads(response_text)
                except:
                    decision_tree = None
            
            out = {
                'success': True,
                'response': response_text,
                'decision_tree': decision_tree  # Parsed JSON if available, else None
            }
            citations = result.get('knowledge_base_citations') or []
            if citations:
                import re
                sources = []
                for c in citations:
                    uri = c.get('uri') or (c.get('s3Location') or {}).get('uri') or ''
                    if uri:
                        uri_clean = re.sub(r'^s3://[^/]+/', '', uri)
                        name = uri_clean.split('/')[-1] if '/' in uri_clean else uri_clean
                        if name and name not in sources:
                            sources.append(name)
                if sources:
                    out['sources'] = sources
            return jsonify(out)
        
        return jsonify({
            'success': False,
            'error': result.get('message', 'LLM did not return a response')
        }), 400
    except Exception as e:
        logger.error(f"Error in generate-decision-tree: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin.route('/kb-stats', methods=['GET'])
@login_required
@admin_required
def kb_stats():
    """Return document counts for each Knowledge Base (default, Level 4 Style, Level 4 Clinic)."""
    try:
        from flask_app.services.bedrock_service import BedrockService
        bedrock = BedrockService()
        result = bedrock.get_kb_document_counts()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting KB stats: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'knowledge_bases': []}), 500


@admin.route('/upload-all-to-kb', methods=['POST'])
@login_required
@admin_required
def upload_all_to_kb():
    """
    Upload all Level 4 reports from database to Knowledge Base.
    Uses L4KBUploader to generate case cards and upload to S3.
    """
    try:
        from flask_app.services.l4_kb_uploader import L4KBUploader
        
        uploader = L4KBUploader()
        results = uploader.upload_all_case_cards(format="json")
        
        return jsonify({
            'success': True,
            'results': results
        })
    except Exception as e:
        logger.error(f"Error uploading to KB: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin.route('/compare-db-to-kb', methods=['GET'])
@login_required
@admin_required
def compare_db_to_kb():
    """
    Compare Level 4 reports in database vs Knowledge Base.
    Shows which reports are in DB but NOT uploaded to KB.
    """
    try:
        import re
        import boto3
        from flask_app.services.bedrock_service import BedrockService
        
        # Get all unique reports from database
        all_designs = L4DeviceDesign.query.all()
        db_reports = set()
        for design in all_designs:
            if design.source_report_id:
                # Sanitize same way as uploader does
                report_id = design.source_report_id.replace(" ", "_").replace("(", "").replace(")", "")
                db_reports.add(report_id.lower())  # Normalize to lowercase for comparison
        
        # Get KB documents from Level 4 KBs (Style and Clinic)
        bedrock = BedrockService()
        kb_reports = set()
        kb_docs = []
        
        try:
            agent_client = boto3.client('bedrock-agent', region_name=bedrock.KNOWLEDGE_BASE_REGION)
            for kb_id, kb_label in [
                (bedrock.KB_LEVEL4_STYLE_ID, "Level 4 Style"),
                (bedrock.KB_LEVEL4_CLINIC_ID, "Level 4 Clinic")
            ]:
                try:
                    ds_response = agent_client.list_data_sources(
                        knowledgeBaseId=kb_id,
                        maxResults=20
                    )
                    for ds in ds_response.get("dataSourceSummaries", []):
                        ds_id = ds.get("dataSourceId", "")
                        next_token = None
                        while True:
                            params = {
                                "knowledgeBaseId": kb_id,
                                "dataSourceId": ds_id,
                                "maxResults": 100
                            }
                            if next_token:
                                params["nextToken"] = next_token
                            doc_response = agent_client.list_knowledge_base_documents(**params)
                            doc_list = doc_response.get("documentDetailList") or doc_response.get("documentDetails") or []
                            for doc in doc_list:
                                # Extract report ID from document identifier/URI
                                identifier = doc.get("identifier", {})
                                uri = identifier.get("s3Location", {}).get("uri", "") if isinstance(identifier, dict) else str(identifier)
                                if not uri:
                                    uri = doc.get("uri", "")
                                # Extract filename from URI (e.g., s3://bucket/level4-case-cards/ReportName_context.json)
                                if uri:
                                    filename = uri.split('/')[-1] if '/' in uri else uri
                                    # Remove extension and context suffix (e.g., "ReportName_context.json" -> "ReportName")
                                    # Pattern: {report_id}_{design_context}.{ext}
                                    match = re.match(r'^(.+?)_(?:nighttime_mad|daytime_tmj|unknown)\.(?:json|txt)$', filename, re.IGNORECASE)
                                    if match:
                                        report_id = match.group(1).lower()
                                        kb_reports.add(report_id)
                                        kb_docs.append({
                                            "kb": kb_label,
                                            "filename": filename,
                                            "report_id": report_id,
                                            "status": doc.get("status", "unknown")
                                        })
                                    else:
                                        # Try without context suffix (legacy format?)
                                        base = filename.rsplit('.', 1)[0] if '.' in filename else filename
                                        kb_reports.add(base.lower())
                                        kb_docs.append({
                                            "kb": kb_label,
                                            "filename": filename,
                                            "report_id": base.lower(),
                                            "status": doc.get("status", "unknown")
                                        })
                            next_token = doc_response.get("nextToken")
                            if not next_token:
                                break
                except Exception as e:
                    logger.warning(f"Error listing KB {kb_label} documents: {e}")
        except Exception as e:
            logger.warning(f"Error accessing KB: {e}")
        
        # Compare: reports in DB but not in KB
        missing_from_kb = sorted(list(db_reports - kb_reports))
        in_both = sorted(list(db_reports & kb_reports))
        only_in_kb = sorted(list(kb_reports - db_reports))  # Should be rare
        
        # Get original report names for missing ones
        missing_details = []
        for missing_id in missing_from_kb:
            # Find original source_report_id that matches
            for design in all_designs:
                if design.source_report_id:
                    sanitized = design.source_report_id.replace(" ", "_").replace("(", "").replace(")", "").lower()
                    if sanitized == missing_id:
                        missing_details.append({
                            "report_id": missing_id,
                            "original_name": design.source_report_id,
                            "design_contexts": list(set([d.design_context for d in all_designs if d.source_report_id == design.source_report_id]))
                        })
                        break
        
        return jsonify({
            "success": True,
            "database": {
                "total_reports": len(db_reports),
                "report_ids": sorted(list(db_reports))
            },
            "knowledge_base": {
                "total_reports": len(kb_reports),
                "total_documents": len(kb_docs),
                "report_ids": sorted(list(kb_reports)),
                "documents": kb_docs[:50]  # First 50 for preview
            },
            "comparison": {
                "in_both": {
                    "count": len(in_both),
                    "report_ids": in_both
                },
                "missing_from_kb": {
                    "count": len(missing_from_kb),
                    "report_ids": missing_from_kb,
                    "details": missing_details
                },
                "only_in_kb": {
                    "count": len(only_in_kb),
                    "report_ids": only_in_kb
                }
            }
        })
    except Exception as e:
        logger.error(f"Error comparing DB to KB: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin.route('/process-level4-reports', methods=['POST'])
@login_required
@admin_required
def process_level4_reports():
    """
    Process Level 4 reports from admin_files table
    Finds Level 4 reports that haven't been processed yet and processes them
    """
    try:
        from flask_app.services.l4_document_processor import L4DocumentProcessor
        from flask_app.services.l4_extraction_service import L4ExtractionService
        from flask_app.services.l4_validation_service import L4ValidationService
        from flask_app.services.l4_persistence_service import L4PersistenceService
        from pathlib import Path
        import json
        import boto3
        import tempfile
        import os
        
        # Find Level 4 reports in admin_files that haven't been processed
        # Check if they're already in l4_device_design table
        level4_files = AdminFile.query.filter(
            or_(
                AdminFile.file_category.like('%Level 4%'),
                AdminFile.file_category.like('%level 4%'),
                AdminFile.name.like('%Level_4%'),
                AdminFile.name.like('%level4%'),
                AdminFile.name.like('%Level4%')
            )
        ).all()
        
        if not level4_files:
            return jsonify({
                'success': False,
                'error': 'No Level 4 reports found in admin_files table'
            }), 400
        
        # Filter to PDF and DOCX files (support both formats)
        processable_files = [f for f in level4_files if f.name.lower().endswith(('.pdf', '.docx'))]
        
        if not processable_files:
            return jsonify({
                'success': False,
                'error': f'Found {len(level4_files)} Level 4 files, but none are PDF or DOCX format.'
            }), 400
        
        # Check which ones are already processed
        processed_report_ids = {d.source_report_id for d in L4DeviceDesign.query.all()}
        unprocessed_files = [f for f in processable_files if f.name not in processed_report_ids]
        
        if not unprocessed_files:
            return jsonify({
                'success': True,
                'message': f'All {len(docx_files)} Level 4 reports have already been processed',
                'results': {
                    'total': len(docx_files),
                    'already_processed': len(docx_files),
                    'processed': []
                }
            })
        
        # Initialize processors
        document_processor = L4DocumentProcessor()
        extraction_service = L4ExtractionService()
        validation_service = L4ValidationService()
        persistence_service = L4PersistenceService()
        
        # Import PDF parser
        from flask_app.services.document_parser_service import DocumentParserService
        pdf_parser = DocumentParserService()
        
        # Initialize S3 client
        try:
            s3_client = boto3.client('s3')
            s3_bucket = os.getenv('S3_BUCKET_NAME')
        except Exception as e:
            logger.error(f"Could not initialize S3 client: {e}")
            s3_client = None
            s3_bucket = None
        
        results = {
            'total': len(unprocessed_files),
            'successful': 0,
            'failed': 0,
            'processed': []
        }
        
        # Create temp directory for downloaded files
        with tempfile.TemporaryDirectory() as temp_dir:
            # Process each file
            for admin_file in unprocessed_files:
                try:
                    logger.info(f"Processing: {admin_file.name} (ID: {admin_file.id})")
                    
                    # Download from S3 if available
                    file_bytes = None
                    is_pdf = admin_file.name.lower().endswith('.pdf')
                    is_docx = admin_file.name.lower().endswith('.docx')
                    
                    if s3_client and s3_bucket and admin_file.s3_key:
                        try:
                            # Download file bytes
                            s3_response = s3_client.get_object(Bucket=s3_bucket, Key=admin_file.s3_key)
                            file_bytes = s3_response['Body'].read()
                            logger.info(f"Downloaded {admin_file.name} from S3 ({len(file_bytes)} bytes)")
                        except Exception as e:
                            logger.error(f"Could not download {admin_file.name} from S3: {e}")
                            results['failed'] += 1
                            results['processed'].append({
                                'file': admin_file.name,
                                'success': False,
                                'error': f'Could not download from S3: {str(e)}'
                            })
                            continue
                    else:
                        results['failed'] += 1
                        results['processed'].append({
                            'file': admin_file.name,
                            'success': False,
                            'error': 'No S3 key available or S3 not configured'
                        })
                        continue
                    
                    # Step 1: Pre-process - extract text and split into sections
                    if is_pdf:
                        # Extract text from PDF
                        try:
                            pdf_text = pdf_parser.extract_text_from_pdf(file_bytes)
                            logger.info(f"Extracted {len(pdf_text)} characters from PDF")
                            
                            # Split into sections (reuse the section splitting logic)
                            sections = document_processor.split_into_sections(pdf_text)
                            
                            # Extract patient ID and demographics
                            patient_id = document_processor.extract_patient_id(pdf_text, admin_file.name)
                            demographics = document_processor.extract_age_sex(pdf_text)
                            
                            processed = {
                                'filename': admin_file.name,
                                'full_text': pdf_text,
                                'sections': sections,
                                'patient_id': patient_id,
                                'age': demographics.get('age'),
                                'sex': demographics.get('sex')
                            }
                        except Exception as e:
                            logger.error(f"Error processing PDF {admin_file.name}: {e}", exc_info=True)
                            results['failed'] += 1
                            results['processed'].append({
                                'file': admin_file.name,
                                'success': False,
                                'error': f'PDF processing error: {str(e)}'
                            })
                            continue
                    elif is_docx:
                        # Process DOCX file
                        try:
                            # Save to temp file for processing
                            local_file_path = os.path.join(temp_dir, admin_file.name)
                            with open(local_file_path, 'wb') as f:
                                f.write(file_bytes)
                            processed = document_processor.process_document(local_file_path)
                        except Exception as e:
                            logger.error(f"Error processing DOCX {admin_file.name}: {e}", exc_info=True)
                            results['failed'] += 1
                            results['processed'].append({
                                'file': admin_file.name,
                                'success': False,
                                'error': f'DOCX processing error: {str(e)}'
                            })
                            continue
                    else:
                        results['failed'] += 1
                        results['processed'].append({
                            'file': admin_file.name,
                            'success': False,
                            'error': 'Unsupported file format (must be PDF or DOCX)'
                        })
                        continue
                    
                    # Step 2: Extract
                    extraction = extraction_service.extract_device_data(
                        sections=processed["sections"],
                        patient_id=processed["patient_id"] or str(admin_file.patient_id),
                        filename=processed["filename"]
                    )
                    
                    # Step 3: Normalize
                    normalized = extraction_service.normalize_extraction(extraction)
                    
                    # Step 4: Validate
                    is_valid, error_msg = validation_service.validate_extraction(normalized)
                    if not is_valid:
                        results['failed'] += 1
                        results['processed'].append({
                            'file': admin_file.name,
                            'success': False,
                            'error': error_msg
                        })
                        continue
                    
                    # Step 5: Persist
                    persisted = persistence_service.persist_extraction(
                        source_report_id=processed["filename"],
                        patient_id=processed["patient_id"] or str(admin_file.patient_id),
                        extraction=normalized
                    )
                    
                    results['successful'] += 1
                    results['processed'].append({
                        'file': admin_file.name,
                        'success': True,
                        'designs': len(persisted.get("device_designs", [])),
                        'options': len(persisted.get("device_options", []))
                    })
                    
                except Exception as e:
                    logger.error(f"Error processing {admin_file.name}: {e}", exc_info=True)
                    results['failed'] += 1
                    results['processed'].append({
                        'file': admin_file.name,
                        'success': False,
                        'error': str(e)
                    })
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error processing Level 4 reports: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@admin.route('/api/report/<path:report_id>/level4-data', methods=['GET'])
@login_required
@admin_required
def get_report_level4_data(report_id):
    """
    API endpoint to get Level 4 data for a specific report
    Returns JSON matching case card structure
    """
    try:
        # Get device designs for this report
        device_designs = L4DeviceDesign.query.filter_by(
            source_report_id=report_id
        ).all()
        
        # Get device options
        device_options = []
        for design in device_designs:
            options = L4DeviceOption.query.filter_by(
                source_report_id=report_id,
                design_context=design.design_context
            ).all()
            device_options.extend([opt.to_dict() for opt in options])
        
        return jsonify({
            'success': True,
            'source_report_id': report_id,
            'device_designs': [design.to_dict() for design in device_designs],
            'device_options': device_options
        })
        
    except Exception as e:
        logger.error(f"Error getting report data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
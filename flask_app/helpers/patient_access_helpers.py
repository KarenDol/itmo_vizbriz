"""
Shared patient filtering logic for patient_list, forms, and other views.
Ensures identical patient visibility across the application.
"""
from flask_app import db
from flask_app.models import Patient, Clinic, Dentist, dentist_clinic_association


def get_accessible_patients(include_archived=False, limit=None):
    """
    Get patients the current user can access. Same logic as patient_list.
    Used by: patient_list, get_patients_for_select, dentist treatment quiz, etc.

    Args:
        include_archived: If True, include archived patients (admin only)
        limit: Optional max number of patients to return (None = no limit)

    Returns:
        List of Patient objects
    """
    from flask_login import current_user

    if current_user.role == "admin":
        if include_archived:
            query = Patient.query.order_by(Patient.create_date.desc())
        else:
            query = (
                Patient.query.filter(Patient.status != "Archived")
                .order_by(Patient.create_date.desc())
            )
        if limit:
            query = query.limit(limit)
        return query.all()

    if current_user.role not in ["Dentist", "dentist", "Dentists"]:
        return []

    dentist_clinic_ids = current_user.get_clinic_ids()
    dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, "get_dso_ids") else []

    if dentist_clinic_ids:
        base_query = Patient.query.filter(
            db.or_(
                Patient.clinic_id.in_(dentist_clinic_ids),
                db.and_(
                    Patient.clinic_id.is_(None),
                    Patient.dentist_id.isnot(None),
                    db.exists().where(
                        db.and_(
                            dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                            dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids),
                        )
                    ),
                ),
            )
        )
        if not include_archived:
            base_query = base_query.filter(Patient.status != "Archived")
        base_query = base_query.order_by(Patient.create_date.desc())
        if limit:
            base_query = base_query.limit(limit)
        return base_query.all()

    if dentist_dso_ids:
        base_query = (
            Patient.query.join(Dentist)
            .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
            .filter(
                db.or_(
                    Clinic.dso_id.in_(dentist_dso_ids),
                    db.and_(
                        Patient.clinic_id.is_(None),
                        Dentist.DSO == getattr(current_user, "DSO", None),
                    ),
                )
            )
        )
        if not include_archived:
            base_query = base_query.filter(Patient.status != "Archived")
        base_query = base_query.order_by(Patient.create_date.desc())
        if limit:
            base_query = base_query.limit(limit)
        return base_query.all()

    return []

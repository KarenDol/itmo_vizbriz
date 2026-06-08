"""Build Hebrew lab referral email body for /wizard/sleep_labs send_referral."""
from flask import render_template

SCAN_LABELS_HE = {
    'CBCT': 'CBCT',
    'CLINICAL PICTURES': 'Clinical Pictures',
    'INTRAORAL SCANS': 'Intraoral Scans',
}

# Hebrew “coordinate arrival with …” line per lab (name, phone). Phone may be empty.
LAB_COORDINATION_HE = {
    'Or-Hashen': ('נחום', '052-440-3369'),
    'CT-Dent': ('עדי', '050-502-8061'),
}


def coordination_for_lab(lab_name):
    """Return (coordinator_name_he, phone) for the referral footer; may be ('','')."""
    key = (lab_name or '').strip()
    if key in LAB_COORDINATION_HE:
        return LAB_COORDINATION_HE[key]
    return ('', '')


def build_hebrew_referral_html(patient_name, patient_phone, patient_email,
                               dentist_name, dentist_email, image_types_list,
                               patient_id_number=None, lab_name=None):
    """Return HTML string (Hebrew/RTL) for the referral email body."""
    scans_list = [
        SCAN_LABELS_HE.get((s or '').strip(), (s or '').strip())
        for s in (image_types_list or [])
        if s and (s or '').strip()
    ]
    if not scans_list:
        scans_list = ['Not specified']
    coord_name, coord_phone = coordination_for_lab(lab_name)
    return render_template(
        'lab_reference_email_hebrew.html',
        patient_name=patient_name or '-',
        patient_phone=patient_phone or '-',
        patient_email=patient_email or '-',
        patient_id_number=patient_id_number or '-',
        dentist_name=dentist_name or '-',
        dentist_email=dentist_email or '-',
        scans_list=scans_list,
        coordinator_name=coord_name,
        coordinator_phone=coord_phone,
    )

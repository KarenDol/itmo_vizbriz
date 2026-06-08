#!/usr/bin/env python3

from urllib.parse import urlencode

def create_email_tracking_link(base_url, destination_url, patient_email=None, email_type=None, clinic_id=None, quiz_id=None, cta_type=None):
    """
    Create a tracking link for emails that logs clicks before redirecting
    
    Args:
        base_url: Your website base URL (e.g., 'https://yoursite.com')
        destination_url: Where user should end up after clicking
        patient_email: Patient's email address
        email_type: Type of email ('doctor_notification', 'patient_follow_up', etc.)
        clinic_id: Clinic ID for tracking
        quiz_id: Quiz ID if applicable
        cta_type: Specific CTA type (e.g., 'email_link_click - scheduled a sleep test')
    
    Returns:
        Full tracking URL string
    """
    
    # Build parameters for tracking
    params = {
        'redirect_url': destination_url
    }
    
    if patient_email:
        params['patient_email'] = patient_email
    if email_type:
        params['email_type'] = email_type
    if clinic_id:
        params['clinic_id'] = clinic_id
    if quiz_id:
        params['quiz_id'] = quiz_id
    if cta_type:
        params['cta_type'] = cta_type
    
    # Create the tracking URL
    tracking_endpoint = f"{base_url}/api/tracking/track-email-click"
    tracking_url = f"{tracking_endpoint}?{urlencode(params)}"
    
    return tracking_url

# Example usage:
if __name__ == '__main__':
    
    # Example 1: Doctor notification email
    doctor_link = create_email_tracking_link(
        base_url='https://yoursite.com',
        destination_url='https://yoursite.com/patient-list',
        patient_email='patient@example.com',
        email_type='doctor_notification',
        clinic_id=123
    )
    print("Doctor notification link:")
    print(doctor_link)
    print()
    
    # Example 2: Patient follow-up email
    patient_link = create_email_tracking_link(
        base_url='https://yoursite.com',
        destination_url='https://yoursite.com/advanced-quiz',
        patient_email='patient@example.com',
        email_type='patient_follow_up',
        clinic_id=123,
        quiz_id=456
    )
    print("Patient follow-up link:")
    print(patient_link)
    print()
    
    # Example 3: Schedule sleep test from email
    schedule_link = create_email_tracking_link(
        base_url='https://yoursite.com',
        destination_url='https://yoursite.com/schedule',
        patient_email='patient@example.com',
        email_type='quiz_results',
        clinic_id=123
    )
    print("Schedule sleep test link:")
    print(schedule_link) 
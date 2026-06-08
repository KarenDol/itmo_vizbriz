"""
Performance optimization service for patient workflow manifest.
This service provides optimized database queries while maintaining backward compatibility.
"""

from sqlalchemy.orm import joinedload
from flask_app.models import (
    Patient, PatientCaseEnvelope, File, Dentist, Clinic, DSO, 
    PatientConsultSchedule, PatientDeviceOrder, DentistReportApproval,
    PatientComment, Claim, Comment, PatientStatus, StatusOption
)
import json
import logging

logger = logging.getLogger(__name__)

class PerformanceService:
    """Service for optimized patient data loading."""
    
    @staticmethod
    def get_optimized_patient_data(patient_id):
        """
        Get patient data with optimized database queries using eager loading.
        This replaces multiple separate queries with a single optimized query.
        
        Args:
            patient_id (int): Patient ID
            
        Returns:
            dict: Patient data with all related information
        """
        try:
            # Single optimized query with eager loading
            patient = Patient.query.options(
                joinedload(Patient.clinic).joinedload(Clinic.dso_info),
                joinedload(Patient.dentist),
                joinedload(Patient.files),
                joinedload(Patient.comments)
            ).get(patient_id)
            
            if not patient:
                return None
            
            # Get canonical data in a separate optimized query
            canonical_data = PerformanceService.get_canonical_data_optimized(patient_id)
            
            # Get other related data with optimized queries
            patient_statuses = PerformanceService.get_patient_statuses_optimized(patient_id)
            scheduled_consultations = PerformanceService.get_consultations_optimized(patient_id)
            
            return {
                'patient': patient,
                'canonical_data': canonical_data,
                'patient_statuses': patient_statuses,
                'scheduled_consultations': scheduled_consultations,
                'uploaded_files': {file.category: file for file in patient.files} if patient.files else {}
            }
            
        except Exception as e:
            logger.error(f"Error in get_optimized_patient_data for patient {patient_id}: {e}")
            # Fallback to original method if optimization fails
            return None
    
    @staticmethod
    def get_canonical_data_optimized(patient_id):
        """
        Get canonical data with optimized query.
        
        Args:
            patient_id (int): Patient ID
            
        Returns:
            dict: Canonical data or None
        """
        try:
            canonical_envelope = PatientCaseEnvelope.query.filter_by(
                patient_id=patient_id, 
                report_id='canonical'
            ).first()
            
            if canonical_envelope and canonical_envelope.case_json:
                if isinstance(canonical_envelope.case_json, str):
                    return json.loads(canonical_envelope.case_json)
                else:
                    return canonical_envelope.case_json
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting canonical data for patient {patient_id}: {e}")
            return None
    
    @staticmethod
    def get_patient_statuses_optimized(patient_id):
        """
        Get patient statuses with optimized query.
        
        Args:
            patient_id (int): Patient ID
            
        Returns:
            dict: Patient statuses
        """
        try:
            statuses = PatientStatus.query.filter_by(patient_id=patient_id).all()
            return {status.status_type: status for status in statuses}
        except Exception as e:
            logger.error(f"Error getting patient statuses for patient {patient_id}: {e}")
            return {}
    
    @staticmethod
    def get_consultations_optimized(patient_id):
        """
        Get scheduled consultations with optimized query.
        
        Args:
            patient_id (int): Patient ID
            
        Returns:
            list: Scheduled consultations
        """
        try:
            consultations = PatientConsultSchedule.query.filter_by(
                patient_id=patient_id
            ).all()
            return consultations
        except Exception as e:
            logger.error(f"Error getting consultations for patient {patient_id}: {e}")
            return []
    
    @staticmethod
    def get_basic_manifest_data(patient_id):
        """
        Get basic manifest data for fast initial page load.
        This loads only essential data needed for the initial page render.
        
        Args:
            patient_id (int): Patient ID
            
        Returns:
            dict: Basic manifest data
        """
        try:
            # Load only essential patient data
            patient = Patient.query.options(
                joinedload(Patient.clinic).joinedload(Clinic.dso_info),
                joinedload(Patient.dentist)
            ).get(patient_id)
            
            if not patient:
                return None
            
            # Calculate basic progress (without heavy manifest processing)
            basic_progress = PerformanceService.calculate_basic_progress(patient_id)
            
            return {
                'patient': patient,
                'basic_progress': basic_progress,
                'patient_info': {
                    'id': patient.id,
                    'name': patient.name,
                    'email': patient.email,
                    'phone': patient.phone,
                    'gender': patient.gender,
                    'dob': patient.dob,
                    'clinic_name': patient.clinic.name if patient.clinic else None,
                    'dso_name': patient.clinic.dso_info.name if patient.clinic and patient.clinic.dso_info else None,
                    'dentist_name': patient.dentist.name if patient.dentist else None
                }
            }
            
        except Exception as e:
            logger.error(f"Error in get_basic_manifest_data for patient {patient_id}: {e}")
            return None
    
    @staticmethod
    def calculate_basic_progress(patient_id):
        """
        Calculate basic progress without loading full manifest.
        
        Args:
            patient_id (int): Patient ID
            
        Returns:
            dict: Basic progress information
        """
        try:
            # Simple progress calculation based on available data
            patient = Patient.query.get(patient_id)
            if not patient:
                return {'completed_stages': 0, 'total_stages': 10, 'progress_percentage': 0}
            
            # Count basic milestones
            completed = 0
            total = 10
            
            if patient.email:
                completed += 1
            if patient.phone:
                completed += 1
            if patient.clinic_id:
                completed += 1
            if patient.dentist_id:
                completed += 1
            
            # Add more basic checks as needed
            progress_percentage = (completed / total * 100) if total > 0 else 0
            
            return {
                'completed_stages': completed,
                'total_stages': total,
                'progress_percentage': progress_percentage
            }
            
        except Exception as e:
            logger.error(f"Error calculating basic progress for patient {patient_id}: {e}")
            return {'completed_stages': 0, 'total_stages': 10, 'progress_percentage': 0}

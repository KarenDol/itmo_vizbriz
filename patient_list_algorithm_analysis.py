#!/usr/bin/env python3
"""
Comprehensive Analysis of Patient List Algorithm for Dentists
This script explains how the patient list algorithm works for different user types.
"""

def analyze_patient_list_algorithm():
    """
    Analyze the patient list algorithm for dentists.
    """
    print("=== PATIENT LIST ALGORITHM ANALYSIS ===\n")
    
    print("1. OVERVIEW")
    print("=" * 50)
    print("The patient list algorithm determines which patients a dentist can see")
    print("based on their role and DSO (Dental Service Organization) associations.")
    print("The system supports both NEW and LEGACY approaches for backward compatibility.\n")
    
    print("2. USER ROLE DETERMINATION")
    print("=" * 50)
    print("Step 1: Check user role")
    print("  - If role == 'admin': Show ALL patients (no restrictions)")
    print("  - If role == 'Dentist' or 'dentist': Apply DSO-based filtering")
    print("  - Else: Unauthorized access (redirect to index)\n")
    
    print("3. DENTIST PATIENT ACCESS ALGORITHM")
    print("=" * 50)
    print("For dentists, the algorithm follows this priority order:\n")
    
    print("3.1 NEW SYSTEM (Preferred)")
    print("-" * 30)
    print("Condition: hasattr(current_user, 'dsos') and current_user.dsos.count() > 0")
    print("Method: Use many-to-many DSO associations table")
    print()
    print("Algorithm:")
    print("  1. Get dentist's DSO IDs: current_user.get_dso_ids()")
    print("  2. Query patients with complex JOIN:")
    print("     - JOIN Dentist (for legacy compatibility)")
    print("     - LEFT JOIN Clinic (for new system)")
    print("  3. Apply filters:")
    print("     - NEW SYSTEM: Clinic.dso_id IN (dentist's DSO IDs)")
    print("     - LEGACY FALLBACK: Patient.clinic_id IS NULL AND Dentist.DSO == current_user.DSO")
    print("     - STATUS: Patient.status != 'Archived'")
    print("  4. Order by: Patient.create_date DESC")
    print()
    
    print("3.2 LEGACY SYSTEM (Fallback)")
    print("-" * 30)
    print("Condition: hasattr(current_user, 'DSO') and current_user.DSO")
    print("Method: Use old DSO string field")
    print()
    print("Algorithm:")
    print("  1. Query patients with JOIN:")
    print("     - JOIN Dentist")
    print("  2. Apply filters:")
    print("     - Dentist.DSO == current_user.DSO")
    print("     - Patient.status != 'Archived'")
    print("  3. Order by: Patient.create_date DESC")
    print()
    
    print("3.3 NO ACCESS (Fallback)")
    print("-" * 30)
    print("Condition: No DSO associations found")
    print("Result: Empty patient list ([])\n")
    
    print("4. DETAILED QUERY ANALYSIS")
    print("=" * 50)
    print("4.1 NEW SYSTEM QUERY:")
    print("-" * 25)
    print("""
    SELECT p.* 
    FROM patients p
    JOIN dentists d ON p.dentist_id = d.id
    LEFT JOIN clinics c ON p.clinic_id = c.id
    WHERE (
        c.dso_id IN (dentist's DSO IDs)  -- New system patients
        OR 
        (p.clinic_id IS NULL AND d.DSO = current_user.DSO)  -- Legacy patients
    )
    AND p.status != 'Archived'
    ORDER BY p.create_date DESC
    """)
    
    print("4.2 LEGACY SYSTEM QUERY:")
    print("-" * 25)
    print("""
    SELECT p.* 
    FROM patients p
    JOIN dentists d ON p.dentist_id = d.id
    WHERE d.DSO = current_user.DSO
    AND p.status != 'Archived'
    ORDER BY p.create_date DESC
    """)
    
    print("5. ACCESS CONTROL METHODS")
    print("=" * 50)
    print("5.1 can_access_patient(patient) method:")
    print("-" * 40)
    print("""
    def can_access_patient(self, patient):
        if self.role == 'admin':
            return True
        
        # NEW SYSTEM: Patient has clinic_id
        if patient.clinic_id:
            clinic = Clinic.query.get(patient.clinic_id)
            if clinic and clinic.dso_id:
                return self.is_associated_with_dso(clinic.dso_id)
            return False
        
        # LEGACY SYSTEM: Use DSO string comparison
        if (patient.dentist and self.DSO and patient.dentist.DSO):
            return patient.dentist.DSO == self.DSO
        
        # FALLBACK: Direct ownership
        return patient.dentist_id == self.id
    """)
    
    print("5.2 get_dso_ids() method:")
    print("-" * 25)
    print("""
    def get_dso_ids(self):
        return [dso.id for dso in self.dsos]
    """)
    
    print("6. DATABASE RELATIONSHIPS")
    print("=" * 50)
    print("6.1 Dentist-DSO Association Table:")
    print("-" * 35)
    print("""
    dentist_dso_association:
    - dentist_id (FK to dentists.id)
    - dso_id (FK to dsos.id)
    - PRIMARY KEY (dentist_id, dso_id)
    """)
    
    print("6.2 Patient-Clinic Relationship:")
    print("-" * 35)
    print("""
    patients:
    - clinic_id (FK to clinics.id, NULL for legacy patients)
    
    clinics:
    - dso_id (FK to dsos.id)
    """)
    
    print("7. MIGRATION STRATEGY")
    print("=" * 50)
    print("The system supports both old and new approaches:")
    print()
    print("7.1 Legacy Patients (Old System):")
    print("  - No clinic_id assigned")
    print("  - Access controlled by dentist's DSO string")
    print("  - Filter: Dentist.DSO == current_user.DSO")
    print()
    print("7.2 New Patients (New System):")
    print("  - Has clinic_id assigned")
    print("  - Access controlled by clinic's DSO")
    print("  - Filter: Clinic.dso_id IN (dentist's DSO IDs)")
    print()
    print("7.3 Backward Compatibility:")
    print("  - Legacy patients still accessible via DSO string")
    print("  - New patients use DSO association table")
    print("  - System automatically chooses appropriate method")
    
    print("\n8. PERFORMANCE CONSIDERATIONS")
    print("=" * 50)
    print("8.1 Indexes:")
    print("  - idx_patients_clinic_id ON patients(clinic_id)")
    print("  - idx_dentist_dso_dentist_id ON dentist_dso_association(dentist_id)")
    print("  - idx_dentist_dso_dso_id ON dentist_dso_association(dso_id)")
    print()
    print("8.2 Query Optimization:")
    print("  - LEFT JOIN for clinics (handles NULL clinic_id)")
    print("  - IN clause for multiple DSO IDs")
    print("  - ORDER BY on indexed create_date field")
    
    print("\n9. SECURITY FEATURES")
    print("=" * 50)
    print("9.1 Access Control:")
    print("  - Role-based access (admin vs dentist)")
    print("  - DSO-based isolation")
    print("  - Individual patient access validation")
    print()
    print("9.2 Data Isolation:")
    print("  - Dentists only see patients in their DSO")
    print("  - Admin sees all patients")
    print("  - Archived patients excluded from all views")
    
    print("\n10. DEBUGGING AND LOGGING")
    print("=" * 50)
    print("10.1 Logging Features:")
    print("  - Logs which system is being used (new vs legacy)")
    print("  - Logs number of patients found")
    print("  - Logs first 5 patients with DSO info for debugging")
    print("  - Logs warnings for no DSO associations")
    print()
    print("10.2 Debug Information:")
    print("  - Patient name, dentist DSO, clinic DSO")
    print("  - System selection (new/legacy)")
    print("  - Access control method used")
    
    print("\n=== ALGORITHM SUMMARY ===")
    print("The patient list algorithm is a sophisticated multi-tier system that:")
    print("1. Prioritizes the new DSO association system")
    print("2. Falls back to legacy DSO string system")
    print("3. Maintains backward compatibility")
    print("4. Provides comprehensive access control")
    print("5. Includes detailed logging for debugging")
    print("6. Optimizes performance with proper indexing")
    print("7. Ensures data isolation between DSOs")

if __name__ == "__main__":
    analyze_patient_list_algorithm() 
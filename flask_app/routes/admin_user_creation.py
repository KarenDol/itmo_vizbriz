"""
Admin User Creation Routes - Wizard for Creating New Dentists
Admin-only routes for creating new dentists with DSO and Clinic associations
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import login_required, current_user
from flask_app.models import db, Dentist, DSO, Clinic
from flask_app.routes.admin_routes import admin_required
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
import logging

# Create admin user creation blueprint
admin_user_creation = Blueprint('admin_user_creation', __name__, url_prefix='/admin/user-creation')

logger = logging.getLogger(__name__)

@admin_user_creation.route('/')
@login_required
@admin_required
def create_dentist_wizard():
    """Main wizard page - Step 1: DSO Selection/Creation"""
    try:
        # Get all existing DSOs for selection
        dsos = DSO.query.filter_by(status='active').order_by(DSO.name).all()
        logger.info(f"Found {len(dsos)} active DSOs for wizard")
        
        # If no DSOs exist, create a sample one for testing
        if len(dsos) == 0:
            logger.info("No DSOs found, creating sample DSO for testing")
            sample_dso = DSO(
                name="Sample DSO",
                email="sample@dso.com",
                contact_person="Sample Contact",
                telephone="123-456-7890",
                status='active'
            )
            db.session.add(sample_dso)
            db.session.commit()
            dsos = [sample_dso]
            logger.info("Sample DSO created successfully")
        
        # Log DSO details for debugging
        for dso in dsos:
            logger.info(f"DSO: {dso.name} (ID: {dso.id}, Email: {dso.email})")
        
        return render_template('admin/create_dentist_wizard.html',
                             step=1,
                             dsos=dsos,
                             title="Create New Dentist - Step 1: Select DSO")
        
    except Exception as e:
        logger.error(f"Error in create dentist wizard: {str(e)}")
        flash('Error loading dentist creation wizard', 'error')
        return redirect(url_for('main.admin_home'))

@admin_user_creation.route('/step1', methods=['GET', 'POST'])
@login_required
@admin_required
def wizard_step1():
    """Step 1: DSO Selection/Creation"""
    try:
        if request.method == 'POST':
            dso_id = request.form.get('dso_id')
            dso_option = request.form.get('dso_option')
            create_new_dso = dso_option == 'new'
            
            logger.info(f"DSO Step 1 - dso_id: {dso_id}, dso_option: {dso_option}, create_new_dso: {create_new_dso}")
            
            if create_new_dso:
                # Create new DSO
                dso_name = request.form.get('dso_name', '').strip()
                dso_email = request.form.get('dso_email', '').strip()
                dso_contact_person = request.form.get('dso_contact_person', '').strip()
                dso_telephone = request.form.get('dso_telephone', '').strip()
                dso_logo = request.form.get('dso_logo', '').strip()
                
                # Debug: Log all form data
                logger.info(f"DSO Creation Form Data:")
                logger.info(f"  dso_name: '{dso_name}'")
                logger.info(f"  dso_email: '{dso_email}'")
                logger.info(f"  dso_contact_person: '{dso_contact_person}'")
                logger.info(f"  dso_telephone: '{dso_telephone}'")
                logger.info(f"  dso_logo: '{dso_logo}'")
                logger.info(f"  All form data: {dict(request.form)}")
                
                # Just check if we have a name - that's all we need
                if not dso_name:
                    flash('DSO name is required', 'error')
                    return redirect(url_for('admin_user_creation.create_dentist_wizard'))
                
                # Skip duplicate check - just create the DSO
                
                # Create new DSO - simple and straightforward
                new_dso = DSO(
                    name=dso_name,
                    email=dso_email or f"{dso_name.lower().replace(' ', '')}@dso.com",
                    contact_person=dso_contact_person or "Contact Person",
                    telephone=dso_telephone or "000-000-0000",
                    logo=dso_logo if dso_logo else None,
                    status='active'
                )
                
                # Simple save to database
                db.session.add(new_dso)
                db.session.commit()
                dso_id = new_dso.id
                
                flash(f'DSO "{dso_name}" created successfully', 'success')
            else:
                # Validate selected DSO
                if not dso_id:
                    flash('Please select a DSO', 'error')
                    return redirect(url_for('admin_user_creation.create_dentist_wizard'))
                
                dso = DSO.query.get(dso_id)
                if not dso:
                    flash('Selected DSO not found', 'error')
                    return redirect(url_for('admin_user_creation.create_dentist_wizard'))
                
                logger.info(f"Selected existing DSO: {dso.name} (ID: {dso_id})")
            
            # Verify DSO was created/found
            if not dso_id:
                logger.error("No DSO ID available after creation/selection")
                flash('DSO not found', 'error')
                return redirect(url_for('admin_user_creation.create_dentist_wizard'))
            
            # Double-check DSO exists
            dso_check = DSO.query.get(dso_id)
            if not dso_check:
                logger.error(f"DSO with ID {dso_id} not found after creation")
                flash('DSO not found', 'error')
                return redirect(url_for('admin_user_creation.create_dentist_wizard'))
            
            logger.info(f"DSO verified: {dso_check.name} (ID: {dso_id})")
            
            # Store DSO ID and creation flag in session for next step
            session['wizard_dso_id'] = dso_id
            session['wizard_new_dso_created'] = create_new_dso
            session['wizard_step'] = 2
            
            return redirect(url_for('admin_user_creation.wizard_step2'))
        
        # GET request - show step 1 form
        dsos = DSO.query.filter_by(status='active').order_by(DSO.name).all()
        return render_template('admin/create_dentist_wizard.html',
                             step=1,
                             dsos=dsos,
                             title="Create New Dentist - Step 1: Select DSO")
        
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in wizard step 1: {str(e)}")
        flash('Database error occurred', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))
    except Exception as e:
        logger.error(f"Error in wizard step 1: {str(e)}")
        flash('Error processing DSO selection', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))

@admin_user_creation.route('/step2')
@login_required
@admin_required
def wizard_step2():
    """Step 2: Clinic Selection/Creation"""
    try:
        # Check if we have DSO from previous step
        dso_id = session.get('wizard_dso_id')
        if not dso_id:
            flash('Please start from the beginning', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        # Get DSO info
        dso = DSO.query.get(dso_id)
        if not dso:
            flash('DSO not found', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        # Check if a new DSO was created
        new_dso_created = session.get('wizard_new_dso_created', False)
        
        if new_dso_created:
            # If new DSO was created, force creation of new clinic
            clinics = []  # No existing clinics to show
            force_new_clinic = True
            logger.info(f"New DSO created, forcing new clinic creation for DSO: {dso.name}")
        else:
            # Get clinics for this DSO
            clinics = Clinic.query.filter_by(dso_id=dso_id, status='active').order_by(Clinic.name).all()
            force_new_clinic = False
            
            # Debug: Check all clinics in database
            all_clinics = Clinic.query.all()
            logger.info(f"Total clinics in database: {len(all_clinics)}")
            for clinic in all_clinics:
                logger.info(f"Clinic: {clinic.name}, DSO ID: {clinic.dso_id}, Status: {clinic.status}")
            
            logger.info(f"Existing DSO selected: {dso.name} (ID: {dso_id}), found {len(clinics)} clinics")
            
            # If no clinics found, show all clinics for debugging
            if len(clinics) == 0:
                logger.warning(f"No clinics found for DSO {dso_id}. Showing all clinics for debugging.")
                clinics = Clinic.query.order_by(Clinic.name).all()
                # Also try without status filter
                if len(clinics) == 0:
                    clinics = Clinic.query.filter_by(dso_id=dso_id).order_by(Clinic.name).all()
                    logger.info(f"Found {len(clinics)} clinics without status filter")
        
        return render_template('admin/create_dentist_wizard.html',
                             step=2,
                             dso=dso,
                             clinics=clinics,
                             force_new_clinic=force_new_clinic,
                             title="Create New Dentist - Step 2: Select Clinic")
        
    except Exception as e:
        logger.error(f"Error in wizard step 2: {str(e)}")
        flash('Error loading clinic selection', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))

@admin_user_creation.route('/step2', methods=['POST'])
@login_required
@admin_required
def wizard_step2_process():
    """Process Step 2: Clinic Selection/Creation"""
    try:
        dso_id = session.get('wizard_dso_id')
        if not dso_id:
            flash('Please start from the beginning', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        clinic_id = request.form.get('clinic_id')
        clinic_option = request.form.get('clinic_option')
        create_new_clinic = clinic_option == 'new'
        
        if create_new_clinic:
            # Create new clinic
            clinic_name = request.form.get('clinic_name', '').strip()
            clinic_email = request.form.get('clinic_email', '').strip()
            clinic_address = request.form.get('clinic_address', '').strip()
            clinic_telephone = request.form.get('clinic_telephone', '').strip()
            clinic_contact_person = request.form.get('clinic_contact_person', '').strip()
            
            # Just check if we have a name - that's all we need
            if not clinic_name:
                flash('Clinic name is required', 'error')
                return redirect(url_for('admin_user_creation.wizard_step2'))
            
            # Create new clinic - simple and straightforward
            new_clinic = Clinic(
                name=clinic_name,
                dso_id=dso_id,
                email=clinic_email or f"{clinic_name.lower().replace(' ', '')}@clinic.com",
                address=clinic_address if clinic_address else None,
                telephone=clinic_telephone or "000-000-0000",
                contact_person=clinic_contact_person or "Contact Person",
                status='active'
            )
            
            # Simple save to database
            db.session.add(new_clinic)
            db.session.commit()
            clinic_id = new_clinic.id
            
            flash(f'Clinic "{clinic_name}" created successfully', 'success')
        else:
            # Validate selected clinic
            if not clinic_id:
                flash('Please select a clinic', 'error')
                return redirect(url_for('admin_user_creation.wizard_step2'))
            
            clinic = Clinic.query.get(clinic_id)
            if not clinic:
                flash('Selected clinic not found', 'error')
                return redirect(url_for('admin_user_creation.wizard_step2'))
        
        # Store clinic ID in session for next step
        session['wizard_clinic_id'] = clinic_id
        session['wizard_step'] = 3
        
        return redirect(url_for('admin_user_creation.wizard_step3'))
        
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in wizard step 2: {str(e)}")
        flash('Database error occurred', 'error')
        return redirect(url_for('admin_user_creation.wizard_step2'))
    except Exception as e:
        logger.error(f"Error in wizard step 2: {str(e)}")
        flash('Error processing clinic selection', 'error')
        return redirect(url_for('admin_user_creation.wizard_step2'))

@admin_user_creation.route('/step3')
@login_required
@admin_required
def wizard_step3():
    """Step 3: Dentist Creation"""
    try:
        # Check if we have DSO and Clinic from previous steps
        dso_id = session.get('wizard_dso_id')
        clinic_id = session.get('wizard_clinic_id')
        
        if not dso_id or not clinic_id:
            flash('Please start from the beginning', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        # Get DSO and Clinic info
        dso = DSO.query.get(dso_id)
        clinic = Clinic.query.get(clinic_id)
        
        if not dso or not clinic:
            flash('DSO or Clinic not found', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        dentists = Dentist.query.order_by(Dentist.name).all()

        return render_template('admin/create_dentist_wizard.html',
                             step=3,
                             dso=dso,
                             clinic=clinic,
                             dentists=dentists,
                             title="Create New Dentist - Step 3: Create Dentist")
        
    except Exception as e:
        logger.error(f"Error in wizard step 3: {str(e)}")
        flash('Error loading dentist creation form', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))

@admin_user_creation.route('/step3', methods=['POST'])
@login_required
@admin_required
def wizard_step3_process():
    """Process Step 3: Dentist Creation"""
    try:
        dso_id = session.get('wizard_dso_id')
        clinic_id = session.get('wizard_clinic_id')
        
        logger.info(f"Session data - DSO ID: {dso_id}, Clinic ID: {clinic_id}")
        
        if not dso_id or not clinic_id:
            logger.error(f"Missing session data - DSO ID: {dso_id}, Clinic ID: {clinic_id}")
            flash('Please start from the beginning', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        dentist_option = request.form.get('dentist_option', 'new')
        created_new_dentist = False
        target_dentist = None

        if dentist_option == 'existing':
            existing_dentist_id = request.form.get('existing_dentist_id')

            if not existing_dentist_id:
                flash('Please select an existing dentist to continue', 'error')
                return redirect(url_for('admin_user_creation.wizard_step3'))

            try:
                existing_dentist_id = int(existing_dentist_id)
            except (TypeError, ValueError):
                flash('Invalid dentist selected', 'error')
                return redirect(url_for('admin_user_creation.wizard_step3'))

            target_dentist = Dentist.query.get(existing_dentist_id)
            if not target_dentist:
                flash('Selected dentist was not found', 'error')
                return redirect(url_for('admin_user_creation.wizard_step3'))

            logger.info(f"Linking existing dentist {target_dentist.name} (ID: {target_dentist.id})")
        else:
            # Get form data for new dentist creation
            dentist_name = request.form.get('dentist_name', '').strip()
            dentist_email = request.form.get('dentist_email', '').strip()
            dentist_country = request.form.get('dentist_country', '').strip()
            dentist_password = request.form.get('dentist_password', '').strip()
            dentist_confirm_password = request.form.get('dentist_confirm_password', '').strip()

            if not dentist_name:
                flash('Dentist name is required', 'error')
                return redirect(url_for('admin_user_creation.wizard_step3'))

            # Create new dentist - simple and straightforward
            target_dentist = Dentist(
                name=dentist_name,
                email=dentist_email or f"{dentist_name.lower().replace(' ', '')}@dentist.com",
                country=dentist_country or "USA",
                role='dentist',
                status='Active'
            )

            password_to_use = dentist_password if dentist_password else "password123"
            target_dentist.set_password(password_to_use)

            db.session.add(target_dentist)
            db.session.commit()
            created_new_dentist = True
            logger.info(f"Created new dentist {target_dentist.name} (ID: {target_dentist.id})")

        if not target_dentist:
            flash('Unable to determine dentist record', 'error')
            return redirect(url_for('admin_user_creation.wizard_step3'))

        # Associate dentist with DSO and Clinic
        dso = DSO.query.get(dso_id)
        clinic = Clinic.query.get(clinic_id)

        if dso and not target_dentist.is_associated_with_dso(dso.id):
            target_dentist.dsos.append(dso)
            logger.info(f"Associated dentist {target_dentist.id} with DSO {dso.id}")

        if clinic and not target_dentist.is_associated_with_clinic(clinic.id):
            target_dentist.clinics.append(clinic)
            logger.info(f"Associated dentist {target_dentist.id} with Clinic {clinic.id}")

            try:
                # Only set primary clinic if dentist does not already have one
                if not target_dentist.get_primary_clinic_id():
                    target_dentist.set_primary_clinic(clinic.id)
            except Exception as e:
                logger.warning(f"Unable to set primary clinic for dentist {target_dentist.id}: {e}")

        db.session.commit()
        
        # Clear wizard session data
        session.pop('wizard_dso_id', None)
        session.pop('wizard_clinic_id', None)
        session.pop('wizard_step', None)
        session.pop('wizard_new_dso_created', None)
        
        flash_message = (f'Dentist "{target_dentist.name}" created successfully!'
                         if created_new_dentist
                         else f'Dentist "{target_dentist.name}" linked to the selected DSO/clinic.')
        flash(flash_message, 'success')

        return redirect(url_for(
            'admin_user_creation.wizard_success',
            dentist_id=target_dentist.id,
            action='created' if created_new_dentist else 'linked'
        ))
        
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in wizard step 3: {str(e)}")
        flash('Database error occurred', 'error')
        return redirect(url_for('admin_user_creation.wizard_step3'))
    except Exception as e:
        logger.error(f"Error in wizard step 3: {str(e)}")
        flash('Error creating dentist', 'error')
        return redirect(url_for('admin_user_creation.wizard_step3'))

@admin_user_creation.route('/success/<int:dentist_id>')
@login_required
@admin_required
def wizard_success(dentist_id):
    """Success page showing created dentist details"""
    try:
        dentist = Dentist.query.get(dentist_id)
        if not dentist:
            flash('Dentist not found', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        # Get associated DSO and Clinic
        dso = dentist.dsos.first() if dentist.dsos else None
        
        # Try to get primary clinic, fallback to first clinic if primary not set
        clinic = None
        try:
            clinic = dentist.get_primary_clinic()
        except Exception as e:
            logger.warning(f"Could not get primary clinic: {e}")
        
        # If no primary clinic, get the first associated clinic
        if not clinic and dentist.clinics:
            clinic = dentist.clinics.first()
        
        logger.info(f"Success page - Dentist: {dentist.name}, DSO: {dso.name if dso else 'None'}, Clinic: {clinic.name if clinic else 'None'}")
        
        action = request.args.get('action', 'created')

        return render_template('admin/create_dentist_wizard.html',
                             step=4,  # Use step 4 for success page
                             success=True,
                             dentist=dentist,
                             dso=dso,
                             clinic=clinic,
                             success_action=action,
                             title="Dentist Created Successfully")
        
    except Exception as e:
        logger.error(f"Error in wizard success page: {str(e)}")
        flash('Error loading success page', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))

@admin_user_creation.route('/test-success')
@login_required
@admin_required
def test_success():
    """Test success page with sample data"""
    try:
        # Get the most recent dentist
        dentist = Dentist.query.order_by(Dentist.id.desc()).first()
        if not dentist:
            flash('No dentists found for testing', 'error')
            return redirect(url_for('admin_user_creation.create_dentist_wizard'))
        
        # Get associated DSO and Clinic
        dso = dentist.dsos.first() if dentist.dsos else None
        clinic = dentist.clinics.first() if dentist.clinics else None
        
        logger.info(f"Test success page - Dentist: {dentist.name}, DSO: {dso.name if dso else 'None'}, Clinic: {clinic.name if clinic else 'None'}")
        
        return render_template('admin/create_dentist_wizard.html',
                             step=4,  # Use step 4 for success page
                             success=True,
                             dentist=dentist,
                             dso=dso,
                             clinic=clinic,
                             title="Test Success Page")
        
    except Exception as e:
        logger.error(f"Error in test success page: {str(e)}")
        flash(f'Error in test success page: {str(e)}', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))

@admin_user_creation.route('/debug')
@login_required
@admin_required
def debug_wizard():
    """Debug route to check DSO and Clinic data"""
    try:
        dsos = DSO.query.all()
        clinics = Clinic.query.all()
        dentists = Dentist.query.all()
        
        debug_info = {
            'total_dsos': len(dsos),
            'active_dsos': len([d for d in dsos if d.status == 'active']),
            'total_clinics': len(clinics),
            'active_clinics': len([c for c in clinics if c.status == 'active']),
            'total_dentists': len(dentists),
            'dsos': [{'id': d.id, 'name': d.name, 'email': d.email, 'status': d.status} for d in dsos],
            'clinics': [{'id': c.id, 'name': c.name, 'email': c.email, 'dso_id': c.dso_id, 'status': c.status} for c in clinics],
            'dentists': [{'id': d.id, 'name': d.name, 'email': d.email, 'status': d.status, 'dsos': [ds.id for ds in d.dsos], 'clinics': [c.id for c in d.clinics]} for d in dentists],
            'session_data': {
                'wizard_dso_id': session.get('wizard_dso_id'),
                'wizard_clinic_id': session.get('wizard_clinic_id'),
                'wizard_step': session.get('wizard_step')
            }
        }
        
        return jsonify(debug_info)
    except Exception as e:
        logger.error(f"Error in debug route: {str(e)}")
        return jsonify({'error': str(e)}), 500

@admin_user_creation.route('/debug-form', methods=['POST'])
@login_required
@admin_required
def debug_form():
    """Debug route to see what form data is being received"""
    try:
        form_data = dict(request.form)
        logger.info(f"Received form data: {form_data}")
        
        return jsonify({
            'success': True,
            'form_data': form_data,
            'method': request.method,
            'content_type': request.content_type
        })
    except Exception as e:
        logger.error(f"Error in debug form route: {str(e)}")
        return jsonify({'error': str(e)}), 500

@admin_user_creation.route('/test-create-dso')
@login_required
@admin_required
def test_create_dso():
    """Test route to create a DSO manually"""
    try:
        # First, check if we can query existing DSOs
        existing_dsos = DSO.query.all()
        logger.info(f"Found {len(existing_dsos)} existing DSOs")
        
        # Create a test DSO
        test_dso = DSO(
            name="Test DSO",
            email="test@dso.com",
            contact_person="Test Contact",
            telephone="123-456-7890",
            status='active'
        )
        
        db.session.add(test_dso)
        db.session.flush()  # Get the ID without committing
        test_dso_id = test_dso.id
        db.session.commit()  # Now commit
        
        # Verify the DSO was created
        created_dso = DSO.query.get(test_dso_id)
        if not created_dso:
            logger.error("DSO was not found after creation")
            return jsonify({
                'success': False,
                'error': 'DSO not found after creation'
            }), 500
        
        logger.info(f"Test DSO created: {created_dso.name} (ID: {created_dso.id})")
        return jsonify({
            'success': True,
            'message': f'Test DSO created successfully',
            'dso_id': created_dso.id,
            'dso_name': created_dso.name,
            'existing_dsos_count': len(existing_dsos)
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating test DSO: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@admin_user_creation.route('/reset')
@login_required
@admin_required
def reset_wizard():
    """Reset wizard session data"""
    try:
        session.pop('wizard_dso_id', None)
        session.pop('wizard_clinic_id', None)
        session.pop('wizard_step', None)
        session.pop('wizard_new_dso_created', None)
        flash('Wizard reset successfully', 'info')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))
    except Exception as e:
        logger.error(f"Error resetting wizard: {str(e)}")
        flash('Error resetting wizard', 'error')
        return redirect(url_for('admin_user_creation.create_dentist_wizard'))

@admin_user_creation.route('/debug-clinics')
@login_required
@admin_required
def debug_clinics():
    """Debug route to check all clinics and DSOs"""
    try:
        all_clinics = Clinic.query.all()
        all_dsos = DSO.query.all()
        
        clinics_data = []
        for clinic in all_clinics:
            clinics_data.append({
                'id': clinic.id,
                'name': clinic.name,
                'dso_id': clinic.dso_id,
                'status': clinic.status,
                'email': clinic.email
            })
        
        dsos_data = []
        for dso in all_dsos:
            dsos_data.append({
                'id': dso.id,
                'name': dso.name,
                'email': dso.email,
                'status': dso.status
            })
        
        return jsonify({
            'total_clinics': len(clinics_data),
            'total_dsos': len(dsos_data),
            'clinics': clinics_data,
            'dsos': dsos_data
        })
    except Exception as e:
        logger.error(f"Error in debug clinics route: {str(e)}")
        return jsonify({'error': str(e)}), 500

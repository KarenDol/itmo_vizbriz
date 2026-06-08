"""
Consultation and Device Management Routes

This module handles:
- Consultation scheduling (CRUD operations)
- Device orders and delivery management
- Patient stage actions for consultations and devices
- Consultation requests from quiz submissions
"""

import logging
from datetime import datetime

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import ConsultationRequest, Patient, PatientConsultSchedule, PatientDeviceOrder

logger = logging.getLogger(__name__)


def register_consultation_device_routes(main):
    """Register consultation and device management routes onto the main blueprint."""
    
    # Appliance details
    main.add_url_rule(
        '/api/patient/<int:patient_id>/appliance-details',
        'get_patient_appliance_details',
        login_required(get_patient_appliance_details),
        methods=['GET']
    )
    
    # Consultation schedule routes
    main.add_url_rule(
        '/api/patient/<int:patient_id>/consult-schedule',
        'list_consult_schedule',
        login_required(list_consult_schedule),
        methods=['GET']
    )
    main.add_url_rule(
        '/api/patient/<int:patient_id>/consult-schedule',
        'create_consult_schedule',
        login_required(create_consult_schedule),
        methods=['POST']
    )
    main.add_url_rule(
        '/api/consult-schedule/<int:schedule_id>',
        'get_consult_schedule',
        login_required(get_consult_schedule),
        methods=['GET']
    )
    main.add_url_rule(
        '/api/consult-schedule/<int:schedule_id>',
        'update_consult_schedule',
        login_required(update_consult_schedule),
        methods=['PUT', 'PATCH']
    )
    main.add_url_rule(
        '/api/consult-schedule/<int:schedule_id>',
        'delete_consult_schedule',
        login_required(delete_consult_schedule),
        methods=['DELETE']
    )
    main.add_url_rule(
        '/api/consult-schedule/options',
        'get_consult_schedule_options',
        login_required(get_consult_schedule_options),
        methods=['GET']
    )
    
    # Device order/delivery routes
    main.add_url_rule(
        '/api/patient/<int:patient_id>/device-orders',
        'patient_device_orders',
        login_required(patient_device_orders),
        methods=['GET']
    )
    main.add_url_rule(
        '/api/patient/<int:patient_id>/device-delivery',
        'add_device_delivery',
        login_required(add_device_delivery),
        methods=['POST']
    )
    main.add_url_rule(
        '/api/patient/<int:patient_id>/device-delivery/<int:order_id>',
        'update_device_delivery',
        login_required(update_device_delivery),
        methods=['PUT']
    )
    main.add_url_rule(
        '/api/patient/<int:patient_id>/device-delivery/<int:order_id>',
        'delete_device_delivery',
        login_required(delete_device_delivery),
        methods=['DELETE']
    )
    
    # Patient stage routes for consultations and devices
    main.add_url_rule(
        '/patient_stage/<int:patient_id>/consultation_schedule',
        'schedule_consultation',
        login_required(schedule_consultation),
        methods=['POST']
    )
    main.add_url_rule(
        '/patient_stage/<int:patient_id>/consultation_validate',
        'validate_consultation',
        login_required(validate_consultation),
        methods=['POST']
    )
    main.add_url_rule(
        '/patient_stage/<int:patient_id>/order_appliance',
        'order_oral_appliance',
        login_required(order_oral_appliance),
        methods=['POST']
    )
    main.add_url_rule(
        '/patient_stage/<int:patient_id>/update_device_status',
        'update_device_status',
        login_required(update_device_status),
        methods=['POST']
    )
    main.add_url_rule(
        '/patient_stage/<int:patient_id>/schedule_appliance_delivery',
        'schedule_appliance_delivery',
        login_required(schedule_appliance_delivery),
        methods=['POST']
    )
    
    # Consultation request route (public, no login required)
    main.add_url_rule(
        '/api/consultation-request',
        'api_consultation_request',
        api_consultation_request,
        methods=['POST']
    )


# Route handlers

@login_required
def get_patient_appliance_details(patient_id):
    """Get the latest appliance details for a patient"""
    try:
        # Get the most recent device order for the patient
        latest_device = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id
        ).order_by(PatientDeviceOrder.created_at.desc()).first()
        
        if not latest_device:
            return jsonify({
                'success': True,
                'device': None,
                'message': 'No devices found'
            })
        
        # Format the device data using correct field names
        device_data = {
            'device_type': latest_device.device_type,
            'model': latest_device.device_name,  # Use device_name as model
            'fit_date': latest_device.fitting_date.strftime('%Y-%m-%d') if latest_device.fitting_date else None,
            'morning_aligner_used': getattr(latest_device, 'morning_aligner_used', False),
            'morning_aligner_type': getattr(latest_device, 'morning_aligner_type', None),
            'advancement': float(getattr(latest_device, 'advancement', 0)) if getattr(latest_device, 'advancement', None) else None,
            'delivery_date': latest_device.arrival_date.strftime('%Y-%m-%d') if latest_device.arrival_date else None,
            'notes': latest_device.notes,
            'status': latest_device.status
        }
        
        return jsonify({
            'success': True,
            'device': device_data
        })
        
    except Exception as e:
        logger.error(f"Error getting appliance details for patient {patient_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@login_required
def list_consult_schedule(patient_id):
    try:
        rows = PatientConsultSchedule.query.filter_by(patient_id=patient_id).order_by(PatientConsultSchedule.scheduled_datetime.desc()).all()
        data = []
        for r in rows:
            data.append({
                'id': r.id,
                'patient_id': r.patient_id,
                'consult_type': r.consult_type,
                'scheduled_datetime': r.scheduled_datetime.isoformat() if r.scheduled_datetime else None,
                'status': r.status,
                'doctor_name': r.doctor_name,
                'notes': r.notes,
                'completed_datetime': r.completed_datetime.isoformat() if r.completed_datetime else None,
                'created_at': r.created_at.isoformat() if r.created_at else None,
                'updated_at': r.updated_at.isoformat() if r.updated_at else None
            })
        return jsonify({'success': True, 'items': data})
    except Exception as e:
        current_app.logger.error(f"Error listing consult schedule for {patient_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@login_required
def create_consult_schedule(patient_id):
    try:
        payload = request.get_json() or {}
        consult_type = (payload.get('consult_type') or '').strip()
        status = (payload.get('status') or 'scheduled').strip()
        doctor_name = (payload.get('doctor_name') or '').strip()
        notes = payload.get('notes')
        scheduled_dt_raw = payload.get('scheduled_datetime')
        completed_dt_raw = payload.get('completed_datetime')

        def parse_dt(v):
            if not v:
                return None
            try:
                # Support both ISO and 'YYYY-MM-DD HH:MM'
                return datetime.fromisoformat(v.replace('Z', ''))
            except Exception:
                try:
                    return datetime.strptime(v, '%Y-%m-%d %H:%M')
                except Exception:
                    return None

        scheduled_dt = parse_dt(scheduled_dt_raw)
        completed_dt = parse_dt(completed_dt_raw)

        row = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type=consult_type or 'sleep_expert',
            scheduled_datetime=scheduled_dt or datetime.utcnow(),
            status=status or 'scheduled',
            doctor_name=doctor_name or None,
            notes=notes,
            completed_datetime=completed_dt
        )
        db.session.add(row)
        db.session.commit()
        return jsonify({'success': True, 'id': row.id}), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating consult schedule for {patient_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@login_required
def get_consult_schedule(schedule_id):
    """Get a single consult schedule entry by ID."""
    try:
        schedule = PatientConsultSchedule.query.get(schedule_id)
        if not schedule:
            return jsonify({'success': False, 'message': 'Schedule entry not found'}), 404
        
        return jsonify({
            'success': True,
            'item': {
                'id': schedule.id,
                'patient_id': schedule.patient_id,
                'consult_type': schedule.consult_type,
                'scheduled_datetime': schedule.scheduled_datetime.isoformat() if schedule.scheduled_datetime else None,
                'status': schedule.status,
                'doctor_name': schedule.doctor_name,
                'notes': schedule.notes,
                'created_at': schedule.created_at.isoformat() if schedule.created_at else None,
                'updated_at': schedule.updated_at.isoformat() if schedule.updated_at else None
            }
        })
    except Exception as e:
        current_app.logger.error(f"Error getting consult schedule {schedule_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@login_required
def update_consult_schedule(schedule_id):
    try:
        row = PatientConsultSchedule.query.get_or_404(schedule_id)
        payload = request.get_json() or {}

        def parse_dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(v.replace('Z', ''))
            except Exception:
                try:
                    return datetime.strptime(v, '%Y-%m-%d %H:%M')
                except Exception:
                    return None

        if 'consult_type' in payload:
            row.consult_type = (payload.get('consult_type') or row.consult_type)
        if 'status' in payload:
            row.status = (payload.get('status') or row.status)
        if 'doctor_name' in payload:
            row.doctor_name = (payload.get('doctor_name') or None)
        if 'notes' in payload:
            row.notes = payload.get('notes')
        if 'scheduled_datetime' in payload:
            parsed = parse_dt(payload.get('scheduled_datetime'))
            if parsed:
                row.scheduled_datetime = parsed
        if 'completed_datetime' in payload:
            parsed = parse_dt(payload.get('completed_datetime'))
            row.completed_datetime = parsed

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating consult schedule {schedule_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@login_required
def delete_consult_schedule(schedule_id):
    try:
        row = PatientConsultSchedule.query.get_or_404(schedule_id)
        db.session.delete(row)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting consult schedule {schedule_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@login_required
def get_consult_schedule_options():
    """
    Return allowed consult types and statuses based on the action manifest semantics.
    
    FINAL CONSOLIDATED LIST (4 types - status field handles workflow):
    - 'sleep_doctor' -> Stages 3, 6, 7: All sleep consultations (merged from sleep_expert, ep_doctor)
    - 'dental_sleep_doctor' -> Stages 8, 12: All dental consultations (merged from dental_sleep_doctor_consult)
    - 'follow_up_meeting' -> General follow-up meetings
    - 'oral_appliance_delivery' -> Oral appliance delivery
    
    Note: Validation accepts legacy values (sleep_expert, ep_doctor, dental_sleep_doctor_consult) for backward compatibility.
    Values are case-insensitive in validation (uses LOWER()).
    """
    try:
        # Simplified, consolidated list - status field (scheduled/completed/cancelled) handles workflow
        consult_types = [
            'sleep_doctor',                    # Stages 3, 4: Initial sleep consultations
            'dental_sleep_doctor',             # Stages 8, 12: All dental consultations (merged from dental_sleep_doctor_consult)
            'follow_up_meeting',               # General follow-up meetings
            'followup_sleep_doctor',           # Stages 6, 7: Sleep doctor follow-up consultations
            'oral_appliance_delivery',         # Oral appliance delivery appointment
            'sleep_test',                      # Sleep test consultation
            'imaging'                          # Imaging consultation
        ]
        statuses = ['scheduled', 'completed', 'cancelled']
        return jsonify({'success': True, 'consult_types': consult_types, 'statuses': statuses})
    except Exception as e:
        current_app.logger.error(f"Error building consult schedule options: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@login_required
def patient_device_orders(patient_id):
    """
    API endpoint to fetch device orders for a patient.
    """
    current_app.logger.debug(f"Fetching device orders for patient ID: {patient_id}")
    
    try:
        # Ensure the patient exists
        patient = Patient.query.get_or_404(patient_id)
        
        # Fetch device orders for the patient
        device_orders = PatientDeviceOrder.query.filter_by(patient_id=patient_id).order_by(PatientDeviceOrder.order_date.desc()).all()
        
        orders_data = [
            {
                'id': order.id,
                'device_type': order.device_type,
                'device_name': order.device_name,
                'order_date': order.order_date.strftime('%Y-%m-%d %H:%M:%S') if order.order_date else None,
                'arrival_date': order.arrival_date.strftime('%Y-%m-%d %H:%M:%S') if order.arrival_date else None,
                'status': order.status,
                'notes': order.notes,
                'fitting_date': order.fitting_date.strftime('%Y-%m-%d %H:%M:%S') if order.fitting_date else None,
                'fitting_comment': order.fitting_comment,
                'morning_aligner_used': order.morning_aligner_used,
                'morning_aligner_type': order.morning_aligner_type,
                'advancement': float(order.advancement) if order.advancement else None
            }
            for order in device_orders
        ]
        
        current_app.logger.debug(f"Fetched {len(device_orders)} device orders for patient ID {patient_id}")
        return jsonify({'success': True, 'orders': orders_data})
        
    except Exception as e:
        current_app.logger.error(f"Error fetching device orders for patient ID {patient_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Error fetching device orders: {str(e)}'}), 500


@login_required
def add_device_delivery(patient_id):
    """
    API endpoint to add a new device delivery for a patient.
    """
    current_app.logger.debug(f"Adding device delivery for patient ID: {patient_id}")
    
    try:
        # Ensure the patient exists
        patient = Patient.query.get_or_404(patient_id)
        
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        # Extract form data
        device_type = data.get('device_type')
        device_name = data.get('device_name', '')
        delivery_date_str = data.get('delivery_date')
        notes = data.get('notes', '')
        
        if not device_type or not delivery_date_str:
            return jsonify({'success': False, 'message': 'Device type and delivery date are required'}), 400
        
        # Parse delivery date
        try:
            delivery_date = datetime.fromisoformat(delivery_date_str.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid delivery date format'}), 400
        
        # Extract new fields
        morning_aligner_used = data.get('morning_repositioner_used', False)
        morning_aligner_type = data.get('morning_repositioner_type')
        advancement = data.get('advancement')
        
        # Debug logging
        current_app.logger.debug(f"Morning repositioner data: used={morning_aligner_used}, type={morning_aligner_type}, advancement={advancement}")
        
        # Create new device order record (representing delivery)
        new_device_order = PatientDeviceOrder(
            patient_id=patient_id,
            device_type=device_type,
            device_name=device_name,
            order_date=delivery_date,  # Using delivery date as order date
            arrival_date=delivery_date,
            status='delivered',
            notes=notes,
            fitting_date=delivery_date,
            fitting_comment=notes,
            morning_aligner_used=morning_aligner_used,
            morning_aligner_type=morning_aligner_type,
            advancement=advancement
        )
        
        db.session.add(new_device_order)
        db.session.commit()
        
        current_app.logger.info(f"Device delivery added successfully for patient ID {patient_id}: {device_type}")
        return jsonify({'success': True, 'message': 'Device delivery added successfully'})
        
    except Exception as e:
        current_app.logger.error(f"Error adding device delivery for patient ID {patient_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error adding device delivery: {str(e)}'}), 500


@login_required
def update_device_delivery(patient_id, order_id):
    """
    API endpoint to update an existing device delivery for a patient.
    """
    current_app.logger.debug(f"Updating device delivery {order_id} for patient ID: {patient_id}")
    
    try:
        # Ensure the patient exists
        patient = Patient.query.get_or_404(patient_id)
        
        # Find the device order
        device_order = PatientDeviceOrder.query.filter_by(id=order_id, patient_id=patient_id).first()
        if not device_order:
            return jsonify({'success': False, 'message': 'Device order not found'}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        # Extract form data
        device_type = data.get('device_type')
        device_name = data.get('device_name', '')
        delivery_date_str = data.get('delivery_date')
        notes = data.get('notes', '')
        
        # Extract new fields
        morning_aligner_used = data.get('morning_repositioner_used', False)
        morning_aligner_type = data.get('morning_repositioner_type')
        advancement = data.get('advancement')
        
        if not device_type or not delivery_date_str:
            return jsonify({'success': False, 'message': 'Device type and delivery date are required'}), 400
        
        # Parse delivery date
        try:
            delivery_date = datetime.fromisoformat(delivery_date_str.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid delivery date format'}), 400
        
        # Debug logging
        current_app.logger.debug(f"Updating device delivery with: morning_aligner_used={morning_aligner_used}, morning_aligner_type={morning_aligner_type}, advancement={advancement}")
        
        # Update the device order record
        device_order.device_type = device_type
        device_order.device_name = device_name
        device_order.order_date = delivery_date
        device_order.arrival_date = delivery_date
        device_order.status = 'delivered'
        device_order.notes = notes
        device_order.fitting_date = delivery_date
        device_order.fitting_comment = notes
        device_order.morning_aligner_used = morning_aligner_used
        device_order.morning_aligner_type = morning_aligner_type
        device_order.advancement = advancement
        device_order.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        current_app.logger.info(f"Device delivery {order_id} updated successfully for patient ID {patient_id}: {device_type}")
        return jsonify({'success': True, 'message': 'Device delivery updated successfully'})
        
    except Exception as e:
        current_app.logger.error(f"Error updating device delivery {order_id} for patient ID {patient_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error updating device delivery: {str(e)}'}), 500


@login_required
def delete_device_delivery(patient_id, order_id):
    """
    API endpoint to delete a device delivery for a patient.
    """
    current_app.logger.debug(f"Deleting device delivery {order_id} for patient ID: {patient_id}")
    
    try:
        # Ensure the patient exists
        patient = Patient.query.get_or_404(patient_id)
        
        # Find the device order
        device_order = PatientDeviceOrder.query.filter_by(id=order_id, patient_id=patient_id).first()
        if not device_order:
            return jsonify({'success': False, 'message': 'Device order not found'}), 404
        
        # Delete the device order
        db.session.delete(device_order)
        db.session.commit()
        
        current_app.logger.info(f"Device delivery {order_id} deleted successfully for patient ID {patient_id}")
        return jsonify({'success': True, 'message': 'Device delivery deleted successfully'})
        
    except Exception as e:
        current_app.logger.error(f"Error deleting device delivery {order_id} for patient ID {patient_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error deleting device delivery: {str(e)}'}), 500


@login_required
def schedule_consultation(patient_id):
    """Handle consultation scheduling from AI Workflow"""
    try:
        data = request.get_json()
        consult_type = data.get('consult_type')
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        notes = data.get('notes', '')
        
        if not all([consult_type, scheduled_date, scheduled_time]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Combine date and time
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        # Create or update consultation schedule
        existing_schedule = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type=consult_type
        ).first()
        
        if existing_schedule:
            existing_schedule.scheduled_datetime = scheduled_datetime
            existing_schedule.notes = notes
            existing_schedule.updated_at = datetime.utcnow()
        else:
            new_schedule = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type=consult_type,
                scheduled_datetime=scheduled_datetime,
                notes=notes,
                status='scheduled'
            )
            db.session.add(new_schedule)
        
        db.session.commit()
        return jsonify({"success": True, "message": "Consultation scheduled successfully"})
        
    except Exception as e:
        logger.error(f"Error scheduling consultation: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@login_required
def validate_consultation(patient_id):
    """Handle consultation validation from AI Workflow"""
    try:
        data = request.get_json()
        consult_type = data.get('consult_type')
        completed_date = data.get('completed_date')
        comment = data.get('comment', '')
        
        if not all([consult_type, completed_date]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Update consultation schedule
        schedule = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type=consult_type
        ).first()
        
        if schedule:
            schedule.status = 'completed'
            schedule.completed_datetime = datetime.strptime(completed_date, "%Y-%m-%d")
            schedule.comment = comment
            schedule.updated_at = datetime.utcnow()
            db.session.commit()
            
            return jsonify({"success": True, "message": "Consultation validated successfully"})
        else:
            return jsonify({"success": False, "message": "Consultation schedule not found"}), 404
        
    except Exception as e:
        logger.error(f"Error validating consultation: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@login_required
def order_oral_appliance(patient_id):
    """Handle oral appliance ordering from AI Workflow"""
    try:
        data = request.get_json()
        device_name = data.get('device_name', 'Custom Mandibular Advancement Device')
        notes = data.get('notes', 'Oral appliance ordered based on OSA diagnosis and dental approval.')
        
        # Check if order already exists
        existing_order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if existing_order:
            return jsonify({"success": False, "message": "Oral appliance order already exists"}), 400
        
        # Create new order
        new_order = PatientDeviceOrder(
            patient_id=patient_id,
            device_type='oral_appliance',
            device_name=device_name,
            order_date=datetime.utcnow(),
            status='ordered',
            notes=notes
        )
        
        db.session.add(new_order)
        db.session.commit()
        
        return jsonify({"success": True, "message": "Oral appliance ordered successfully"})
        
    except Exception as e:
        logger.error(f"Error ordering oral appliance: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@login_required
def update_device_status(patient_id):
    """Update device delivery status"""
    try:
        data = request.get_json()
        new_status = data.get('status')  # 'shipped', 'delivered', etc.
        arrival_date = data.get('arrival_date')
        notes = data.get('notes', '')
        
        # Find existing order
        order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if not order:
            return jsonify({"success": False, "message": "No oral appliance order found"}), 404
        
        # Update status
        order.status = new_status
        if arrival_date:
            order.arrival_date = datetime.strptime(arrival_date, "%Y-%m-%d")
        if notes:
            order.notes = notes
        order.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({"success": True, "message": f"Device status updated to {new_status}"})
        
    except Exception as e:
        logger.error(f"Error updating device status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@login_required
def schedule_appliance_delivery(patient_id):
    """Schedule oral appliance delivery appointment"""
    try:
        data = request.get_json()
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        notes = data.get('notes', '')
        
        if not all([scheduled_date, scheduled_time]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Combine date and time
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        # Create or update consultation schedule
        existing_schedule = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='oral_appliance_delivery'
        ).first()
        
        if existing_schedule:
            existing_schedule.scheduled_datetime = scheduled_datetime
            existing_schedule.notes = notes
            existing_schedule.updated_at = datetime.utcnow()
        else:
            new_schedule = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='oral_appliance_delivery',
                scheduled_datetime=scheduled_datetime,
                notes=notes,
                status='scheduled'
            )
            db.session.add(new_schedule)
        
        db.session.commit()
        return jsonify({"success": True, "message": "Appliance delivery scheduled successfully"})
        
    except Exception as e:
        logger.error(f"Error scheduling appliance delivery: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


def api_consultation_request():
    """Create a new consultation request from quiz submission"""
    try:
        data = request.get_json()
        
        # Extract required fields
        email = data.get('email')
        name = data.get('name', 'Quiz Participant')
        comment = data.get('comment', '')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        # Create consultation request
        consultation_request = ConsultationRequest(
            patient_email=email,
            patient_name=name,
            status='New',
            comment=comment,
            created_at=datetime.utcnow(),
            source='VizBriz Quiz'
        )
        
        db.session.add(consultation_request)
        db.session.commit()
        
        current_app.logger.info(f"Consultation request created for {email}")
        
        return jsonify({
            'success': True,
            'message': 'Consultation request submitted successfully',
            'request_id': consultation_request.id
        })
        
    except Exception as e:
        current_app.logger.error(f"Error creating consultation request: {e}")
        db.session.rollback()
        return jsonify({'error': 'Failed to create consultation request'}), 500

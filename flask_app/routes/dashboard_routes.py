from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_app.extensions import db
from flask_login import login_required, current_user
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from datetime import datetime, timedelta
import logging

# Blueprint for dashboard routes
dashboard = Blueprint('dashboard', __name__)
logger = logging.getLogger(__name__)
#s3_client = boto3.client('s3', region_name='us-east-2', config=Config(signature_version='s3v4'))

@dashboard.route('/dashboard')
@login_required
def dashboard_view():
    logger.info('Fetching claims from the database')
    
    # Define the date ranges
    last_30_days = datetime.utcnow() - timedelta(days=30)
    previous_30_days = datetime.utcnow() - timedelta(days=60)
    end_of_previous_30_days = datetime.utcnow() - timedelta(days=31)

    # Filter claims based on user's role
    if current_user.role == 'admin':
        # Admin can access all claims
        claims_query = Claim.query
    else:
        # Non-admin users can only see claims for patients in their DSO
        claims_query = Claim.query.join(Patient).join(Dentist).filter(Dentist.DSO == current_user.DSO)

    try:
        # Calculate turnover for the last 30 days
        turnover_last_30 = claims_query.with_entities(func.sum(Claim.claim_amount)).filter(
            Claim.last_update >= last_30_days
        ).scalar() or 0
        turnover_last_30 = round(turnover_last_30, 2)

        # Calculate turnover for the previous 30 days
        turnover_previous_30 = claims_query.with_entities(func.sum(Claim.claim_amount)).filter(
            Claim.last_update.between(previous_30_days, end_of_previous_30_days)
        ).scalar() or 0
        turnover_previous_30 = round(turnover_previous_30, 2)

        # Calculate turnover change percentage
        if turnover_previous_30 > 0:
            turnover_change_percentage = round(((turnover_last_30 - turnover_previous_30) / turnover_previous_30) * 100, 2)
        else:
            turnover_change_percentage = None

        # Calculate claims waiting in the last 30 days
        claims_waiting_last_30 = claims_query.with_entities(func.sum(Claim.claim_amount)).filter(
            Claim.status == 'Claim Approved', Claim.last_update >= last_30_days
        ).scalar() or 0
        claims_waiting_last_30 = round(claims_waiting_last_30, 2)

        # Calculate claims waiting in the previous 30 days
        claims_waiting_previous_30 = claims_query.with_entities(func.sum(Claim.claim_amount)).filter(
            Claim.status == 'Claim Approved', Claim.last_update.between(previous_30_days, end_of_previous_30_days)
        ).scalar() or 0
        claims_waiting_previous_30 = round(claims_waiting_previous_30, 2)

        # Calculate claims waiting change percentage
        if claims_waiting_previous_30 > 0:
            claims_waiting_change_percentage = round(((claims_waiting_last_30 - claims_waiting_previous_30) / claims_waiting_previous_30) * 100, 2)
        else:
            claims_waiting_change_percentage = None

        # Calculate new patients in the last 30 days
        new_patients_last_30 = claims_query.with_entities(func.count(func.distinct(Claim.patient_id))).filter(
            Claim.status == 'New', Claim.last_update >= last_30_days
        ).scalar() or 0

        # Calculate new patients in the previous 30 days
        new_patients_previous_30 = claims_query.with_entities(func.count(func.distinct(Claim.patient_id))).filter(
            Claim.status == 'New', Claim.last_update.between(previous_30_days, end_of_previous_30_days)
        ).scalar() or 0

        # Calculate new patients change percentage
        if new_patients_previous_30 > 0:
            new_patients_change_percentage = round(((new_patients_last_30 - new_patients_previous_30) / new_patients_previous_30) * 100, 2)
        else:
            new_patients_change_percentage = None

        # Treatment data for pie chart within the last 30 days
        treatment_data = claims_query.with_entities(Claim.treatment_recommendations, func.count(Claim.treatment_recommendations)).filter(
            Claim.last_update >= last_30_days
        ).group_by(Claim.treatment_recommendations).all()

        treatment_labels = [treatment for treatment, _ in treatment_data]
        treatment_counts = [count for _, count in treatment_data]

        # Fetch the list of claims for display
        claims = claims_query.order_by(Claim.last_update.desc()).all()

        return render_template(
            'dashboard.html',
            turnover_last_30=turnover_last_30,
            turnover_change_percentage=turnover_change_percentage,
            claims_waiting_last_30=claims_waiting_last_30,
            claims_waiting_change_percentage=claims_waiting_change_percentage,
            new_patients_last_30=new_patients_last_30,
            new_patients_change_percentage=new_patients_change_percentage,
            claims=claims,
            treatment_labels=treatment_labels,
            treatment_counts=treatment_counts
        )

    except Exception as e:
        logger.error(f"Error fetching claims: {str(e)}")
        flash(f"Error fetching claims: {str(e)}", 'error')
        return redirect(url_for('main.index'))


    except Exception as e:
        logger.error(f"Error fetching claims: {str(e)}")
        flash(f"Error fetching claims: {str(e)}", 'error')
        return redirect(url_for('main.upload_new'))

@dashboard.route('/edit_claim/<int:claim_id>', methods=['GET', 'POST'])
@login_required
def edit_claim(claim_id):
    # Log the claim_id to confirm it has a valid value
    logger.debug(f'Received claim_id: {claim_id}')
    
    # Check if claim_id is valid
    if not isinstance(claim_id, int) or claim_id <= 0:
        logger.error(f"Invalid claim_id received: {claim_id}")
        flash("Invalid claim ID.", "error")
        return redirect(url_for('dashboard.dashboard_view'))

    # Attempt to retrieve the claim
    claim = Claim.query.get_or_404(claim_id)
    logger.debug(f'Accessing edit claim page for claim ID: {claim_id}')

    # Retrieve the associated patient and dentist
    patient = Patient.query.get_or_404(claim.patient_id)
    dentist = Dentist.query.get_or_404(patient.dentist_id)

    # Check if the user has permission based on their DSO
    if not current_user.can_access_patient(patient):
        logger.warning(f"Unauthorized access attempt by user {current_user.email} for claim {claim_id}")
        flash('You do not have permission to edit this claim.', 'error')
        return redirect(url_for('dashboard.dashboard_view'))

    if request.method == 'POST':
        # Process form inputs to update the claim fields
        claim.diagnosis = request.form.get('diagnosis')
        treatment_recommendations = request.form.get('treatment_recommendations')
        claim.treatment_recommendations = request.form.get('other_treatment') if treatment_recommendations == 'Other' else treatment_recommendations
        
        # Convert claim_amount and deductible to appropriate types
        claim_amount = request.form.get('claim_amount')
        try:
            claim.claim_amount = float(claim_amount) if claim_amount else None
        except ValueError:
            flash("Please enter a valid claim amount.", "error")
            return redirect(url_for('dashboard.edit_claim', claim_id=claim_id))
        
        deductible = request.form.get('deductible')
        try:
            claim.deductible = float(deductible) if deductible else None
        except ValueError:
            flash("Please enter a valid deductible amount.", "error")
            return redirect(url_for('dashboard.edit_claim', claim_id=claim_id))
        
        # Update other fields
        claim.status = request.form.get('status')
        claim.last_update = datetime.utcnow()

        # Add a new comment, if provided
        new_comment_text = request.form.get('new_comment')
        if new_comment_text:
            new_comment = Comment(claim_id=claim.id, content=new_comment_text, created_date=datetime.utcnow())
            db.session.add(new_comment)

        # Handle new file uploads
        uploaded_files = request.files.getlist('claim_files[]')
        for file in uploaded_files:
            if file:
                filename = secure_filename(file.filename)
                s3_key = f'claims/{claim.id}/{filename}'
                try:
                    s3_client.upload_fileobj(file, os.getenv('S3_BUCKET_NAME'), s3_key)
                    new_file = File(
                        name=filename,
                        patient_id=claim.patient_id,
                        upload_date=datetime.utcnow(),
                        file_type=file.mimetype,
                        file_size=file.content_length if file.content_length else 0,
                        s3_key=s3_key,
                        category='Claim',
                        subcategory='Claim Documents'
                    )
                    db.session.add(new_file)
                    logger.debug(f"Uploaded file '{filename}' to S3 and added to DB for claim {claim_id}")
                except Exception as e:
                    logger.error(f"Failed to upload file {filename} to S3: {str(e)}")
                    flash(f"Error uploading file {filename}: {str(e)}", 'error')

        # Commit changes to the claim and files
        try:
            db.session.commit()
            flash('Claim updated successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f'Error updating claim: {str(e)}')
            flash(f'Error updating claim: {str(e)}', 'error')
        return redirect(url_for('dashboard.dashboard_view'))

    # For GET request, render the edit claim form with data
    comments = Comment.query.filter_by(claim_id=claim.id).order_by(Comment.created_date.desc()).all()

    # Retrieve files related to the claim and convert to dictionaries for template
    existing_files = File.query.filter_by(patient_id=claim.patient_id, category='Claim').all()
    files_serializable = [
        {'id': file.id, 'name': file.name, 's3_key': file.s3_key, 'size': file.file_size}
        for file in existing_files
    ]
    logger.debug(f"Number of files found for claim {claim_id}: {len(files_serializable)}")

    return render_template(
        'edit_claim.html',
        claim=claim,
        comments=comments,
        existing_files=files_serializable
    )

    # Log the claim_id to confirm it has a valid value
    logger.debug(f'Received claim_id: {claim_id}')
    
    # Check if claim_id is valid
    if not isinstance(claim_id, int) or claim_id <= 0:
        logger.error(f"Invalid claim_id received: {claim_id}")
        flash("Invalid claim ID.", "error")
        return redirect(url_for('dashboard.dashboard_view'))

    # Attempt to retrieve the claim
    claim = Claim.query.get_or_404(claim_id)
    logger.debug(f'Accessing edit claim page for claim ID: {claim_id}')

    # Retrieve the associated patient and dentist
    patient = Patient.query.get_or_404(claim.patient_id)
    dentist = Dentist.query.get_or_404(patient.dentist_id)

    # Check if the user has permission based on their DSO
    if not current_user.can_access_patient(patient):
        logger.warning(f"Unauthorized access attempt by user {current_user.email} for claim {claim_id}")
        flash('You do not have permission to edit this claim.', 'error')
        return redirect(url_for('dashboard.dashboard_view'))

    if request.method == 'POST':
        # Process form inputs to update the claim fields
        claim.diagnosis = request.form.get('diagnosis')
        treatment_recommendations = request.form.get('treatment_recommendations')
        claim.treatment_recommendations = request.form.get('other_treatment') if treatment_recommendations == 'Other' else treatment_recommendations
        
        # Convert claim_amount and deductible to appropriate types
        claim_amount = request.form.get('claim_amount')
        try:
            claim.claim_amount = float(claim_amount) if claim_amount else None
        except ValueError:
            flash("Please enter a valid claim amount.", "error")
            return redirect(url_for('dashboard.edit_claim', claim_id=claim_id))
        
        deductible = request.form.get('deductible')
        try:
            claim.deductible = float(deductible) if deductible else None
        except ValueError:
            flash("Please enter a valid deductible amount.", "error")
            return redirect(url_for('dashboard.edit_claim', claim_id=claim_id))
        
        # Update other fields
        claim.status = request.form.get('status')
        claim.last_update = datetime.utcnow()

        # Add a new comment, if provided
        new_comment_text = request.form.get('new_comment')
        if new_comment_text:
            new_comment = Comment(claim_id=claim.id, content=new_comment_text, created_date=datetime.utcnow())
            db.session.add(new_comment)

        # Handle new file uploads
        uploaded_files = request.files.getlist('claim_files[]')
        for file in uploaded_files:
            if file:
                filename = secure_filename(file.filename)
                s3_key = f'claims/{claim.id}/{filename}'
                try:
                    s3_client.upload_fileobj(file, os.getenv('S3_BUCKET_NAME'), s3_key)
                    new_file = File(
                        name=filename,
                        patient_id=claim.patient_id,
                        upload_date=datetime.utcnow(),
                        file_type=file.mimetype,
                        file_size=file.content_length if file.content_length else 0,
                        s3_key=s3_key,
                        category='Claim',
                        subcategory='Claim Documents'
                    )
                    db.session.add(new_file)
                    logger.debug(f"Uploaded file '{filename}' to S3 and added to DB for claim {claim_id}")
                except Exception as e:
                    logger.error(f"Failed to upload file {filename} to S3: {str(e)}")
                    flash(f"Error uploading file {filename}: {str(e)}", 'error')

        # Commit changes to the claim and files
        try:
            db.session.commit()
            flash('Claim updated successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f'Error updating claim: {str(e)}')
            flash(f'Error updating claim: {str(e)}', 'error')
        return redirect(url_for('dashboard.dashboard_view'))

    # For GET request, render the edit claim form with data
    comments = Comment.query.filter_by(claim_id=claim.id).order_by(Comment.created_date.desc()).all()

    # Retrieve files related to the claim and log the count and details
    existing_files = File.query.filter_by(patient_id=claim.patient_id, category='Claim').all()
    logger.debug(f"Number of files found for claim {claim_id}: {len(existing_files)}")
    if existing_files:
        for file in existing_files:
            logger.debug(f"File: {file.name}, ID: {file.id}, S3 Key: {file.s3_key}, Size: {file.file_size} bytes")
    else:
        logger.warning(f"No files found for claim {claim_id}")

    return render_template(
        'edit_claim.html',
        claim=claim,
        comments=comments,
        existing_files=existing_files
    )


@dashboard.route('/download_all_claim_files/<int:claim_id>', methods=['GET'])
@login_required
def download_all_claim_files(claim_id):
    logger.debug(f"Preparing to download all files for claim ID: {claim_id}")
    claim = Claim.query.get_or_404(claim_id)
    patient = Patient.query.get_or_404(claim.patient_id)

    # Check if the user has permission
    if current_user.role != 'admin' and patient.dentist_id != current_user.id:
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    zip_buffer = BytesIO()

    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Query all files for the claim
            claim_files = File.query.filter_by(patient_id=patient.id, category='Claim').all()

            if not claim_files:
                logger.warning(f"No files found for claim ID: {claim_id}")
                return jsonify({'success': False, 'message': 'No files found for this claim.'}), 404

            # Download each file from S3 and add to the ZIP
            for file in claim_files:
                try:
                    file_data = BytesIO()
                    logger.debug(f"Downloading file: {file.name} from S3 with key: {file.s3_key}")

                    # Download the file from S3
                    s3_client.download_fileobj(os.getenv('S3_BUCKET_NAME'), file.s3_key, file_data)
                    file_data.seek(0)  # Reset buffer position to the start

                    # Write the file into the ZIP
                    zip_file.writestr(file.name, file_data.read())

                except Exception as e:
                    logger.error(f"Error downloading file {file.name} from S3: {str(e)}")
                    continue  # Skip the file and move to the next one

        # Finalize the ZIP
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name=f"claim_{claim_id}_files.zip", mimetype='application/zip')

    except Exception as e:
        logger.error(f"Error creating ZIP for claim ID {claim_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error downloading claim files.'}), 500




@dashboard.route('/create_claim', methods=['GET', 'POST'])
@login_required
def create_claim():
    logger.debug('Accessing create claim page')

    if request.method == 'POST':
        # Get form data
        patient_id = request.form.get('patient_id')
        dentist_id = request.form.get('dentist_id')
        insurer = request.form.get('insurer')
        treatment_recommendations = request.form.get('treatment_recommendations')
        other_treatment = request.form.get('other_treatment') if treatment_recommendations == 'Other' else None
        claim_amount = request.form.get('claim_amount')
        deductible = request.form.get('deductible')
        status = request.form.get('status')
        diagnosis = request.form.get('diagnosis')
        comment_text = request.form.get('comments')
        created_date = datetime.utcnow()
        last_update = datetime.utcnow()

        logger.debug(f"Received POST data: "
                     f"patient_id={patient_id}, dentist_id={dentist_id}, insurer={insurer}, "
                     f"treatment_recommendations={treatment_recommendations}, other_treatment={other_treatment}, "
                     f"claim_amount={claim_amount}, deductible={deductible}, status={status}, "
                     f"diagnosis={diagnosis}, comment_text={comment_text}")

        # Validate that patient_id and dentist_id are provided
        if not patient_id or not dentist_id:
            flash("Patient and Dentist fields are required and must be selected from the autocomplete suggestions.", 'red')
            logger.error("Patient ID or Dentist ID is missing; cannot proceed with claim creation.")
            return redirect(url_for('dashboard.create_claim'))

        try:
            # Create and save the new claim in the database
            new_claim = Claim(
                patient_id=patient_id,
                dentist_id=dentist_id,
                insurer=insurer,
                treatment_recommendations=other_treatment if other_treatment else treatment_recommendations,
                claim_amount=claim_amount,
                deductible=deductible,
                status=status,
                diagnosis=diagnosis,
                created_date=created_date,
                last_update=last_update
            )
            db.session.add(new_claim)
            db.session.flush()  # Flush to get new_claim.id before committing
            logger.debug(f"Created claim with ID {new_claim.id}")

            # Save the comment to the Comment table, associated with the claim
            if comment_text:
                new_comment = Comment(
                    claim_id=new_claim.id,
                    content=comment_text,
                    created_date=datetime.utcnow()
                )
                db.session.add(new_comment)
                logger.debug(f"Added comment for claim ID {new_claim.id}")

            # Handle file uploads directly to S3
            uploaded_files = request.files.getlist('claim_files[]')
            logger.debug(f"Number of files uploaded: {len(uploaded_files)}")

            for file in uploaded_files:
                if file:
                    filename = secure_filename(file.filename)
                    s3_key = f'claims/{new_claim.id}/{filename}'

                    # Read the file to determine its size
                    file_stream = file.read()
                    file_size = len(file_stream)
                    file.seek(0)  # Reset file pointer for S3 upload
                    logger.debug(f"Attempting to upload file '{filename}' of size {file_size} bytes to S3 at '{s3_key}'")

                    try:
                        # Upload the file to S3
                        s3_client.upload_fileobj(file, os.getenv('S3_BUCKET_NAME'), s3_key)
                        logger.debug(f"Uploaded {filename} to S3 at {s3_key}")

                        # Save file info in the database
                        new_file = File(
                            name=filename,
                            patient_id=patient_id,
                            upload_date=datetime.utcnow(),
                            file_type=file.mimetype,
                            file_size=file_size,
                            s3_key=s3_key,
                            category='Claim',
                            subcategory='Claim Documents'
                        )
                        db.session.add(new_file)
                        logger.debug(f"File '{filename}' added to DB with claim ID {new_claim.id}")
                    except Exception as e:
                        logger.error(f"Failed to upload file {filename} to S3: {str(e)}")
                        flash(f"Error uploading file {filename}: {str(e)}", 'red')

            # Commit the claim, comment, and file records to the database
            db.session.commit()
            flash('Claim created successfully!', 'green')
            logger.debug("Claim and associated records committed successfully")
            return redirect(url_for('dashboard.dashboard_view'))

        except Exception as e:
            db.session.rollback()
            logger.error(f'Error creating claim: {str(e)}')
            flash(f'Error creating claim: {str(e)}', 'red')
            return redirect(url_for('dashboard.dashboard_view'))

    # For GET request, render the create claim form
    patients = Patient.query.all()  # Fetch patients for selection
    dentists = Dentist.query.all()  # Fetch dentists for selection
    return render_template('create_claim.html', patients=patients, dentists=dentists)
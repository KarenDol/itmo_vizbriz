#!/usr/bin/env python3
"""
Audio Script Testing Routes
Simple UI for testing audio generation from sleep study reports
"""

import json
import logging
from flask import Blueprint, request, jsonify, render_template, send_file
from flask_app.services.audio_script_service import AudioScriptService
from flask_app.services.audio_guardrails_service import AudioGuardrailsService
from flask_app.services.audio_polly_service import AudioPollyService
from flask_app.services.audio_google_tts_service import AudioGoogleTTSService
from flask_app.services.document_parser_service import DocumentParserService
from flask_app.models import AdminFile, Patient
from flask_app import db
import boto3
import io
import os
from datetime import datetime

logger = logging.getLogger(__name__)

audio_test_bp = Blueprint('audio_test', __name__, url_prefix='/audio-test')


@audio_test_bp.route('/', methods=['GET'])
def test_ui():
    """Render the testing UI"""
    return render_template('audio_test.html')


@audio_test_bp.route('/generate', methods=['POST'])
def generate_audio():
    """
    Generate audio from uploaded report
    
    Accepts:
    - report_data: JSON, PDF, or Word (DOCX) file
    """
    try:
        # Get report data
        report_data = None
        file_bytes = None
        filename = None
        
        if 'report_data' in request.files:
            # File upload
            file = request.files['report_data']
            if file.filename:
                filename = file.filename
                file_bytes = file.read()
                filename_lower = filename.lower()
                
                # Check file type
                if filename_lower.endswith('.json'):
                    # JSON file
                    try:
                        report_data = json.loads(file_bytes.decode('utf-8'))
                    except json.JSONDecodeError:
                        return jsonify({
                            "success": False,
                            "error": "Invalid JSON file"
                        }), 400
                elif filename_lower.endswith('.pdf') or filename_lower.endswith('.docx') or filename_lower.endswith('.doc'):
                    # PDF or Word document - parse it
                    parser_service = DocumentParserService()
                    parse_result = parser_service.parse_document(file_bytes, filename)
                    
                    if "error" in parse_result:
                        return jsonify({
                            "success": False,
                            "error": f"Failed to parse document: {parse_result['error']}"
                        }), 400
                    
                    report_data = parse_result.get("structured_data", {})
                    if not report_data:
                        return jsonify({
                            "success": False,
                            "error": "Document parsed but no structured data extracted"
                        }), 400
                else:
                    return jsonify({
                        "success": False,
                        "error": f"Unsupported file type: {filename}. Supported: JSON, PDF, DOCX"
                    }), 400
        elif request.is_json:
            # JSON in body
            report_data = request.get_json()
        elif 'report_data' in request.form:
            # JSON string in form
            try:
                report_data = json.loads(request.form['report_data'])
            except json.JSONDecodeError:
                return jsonify({
                    "success": False,
                    "error": "Invalid JSON in form data"
                }), 400
        else:
            return jsonify({
                "success": False,
                "error": "No report data provided"
            }), 400
        
        if not report_data:
            return jsonify({
                "success": False,
                "error": "Empty report data"
            }), 400
        
        logger.info("Starting audio generation pipeline")
        
        # Get prompt settings from request
        prompt_settings = {
            'language': request.form.get('language', 'en'),
            'length': request.form.get('length', 'medium'),
            'tone': request.form.get('tone', 'warm'),
            'detail': request.form.get('detail', 'moderate'),
            'focus': request.form.get('focus', 'balanced')
        }
        
        # Step 1: Generate script (2-pass)
        script_service = AudioScriptService()
        script_result = script_service.generate_script(report_data, prompt_settings=prompt_settings)
        
        if "error" in script_result:
            return jsonify({
                "success": False,
                "error": script_result["error"]
            }), 500
        
        dialogue = script_result.get("dialogue", {})
        ssml = dialogue.get("ssml", "")
        
        if not ssml:
            return jsonify({
                "success": False,
                "error": "No SSML generated"
            }), 500
        
        # Step 2: Apply guardrails
        guardrails_service = AudioGuardrailsService()
        guardrails_result = guardrails_service.filter_ssml(ssml)
        
        if not guardrails_result.get("success"):
            logger.warning(f"Guardrails failed, using original SSML: {guardrails_result.get('error')}")
            filtered_ssml = ssml
        else:
            filtered_ssml = guardrails_result.get("filtered_ssml", ssml)
        
        # Step 3: Generate audio with appropriate service
        language = prompt_settings.get('language', 'en')
        
        # Use Google TTS for Hebrew, Polly for other languages
        if language == 'he':
            google_tts_service = AudioGoogleTTSService()
            audio_result = google_tts_service.synthesize_dialogue(filtered_ssml, language=language)
        else:
            polly_service = AudioPollyService()
            audio_result = polly_service.synthesize_dialogue(filtered_ssml, language=language)
        
        if not audio_result.get("success"):
            error_msg = audio_result.get("error", "Audio generation failed")
            error_code = audio_result.get("error_code")
            
            # Provide more helpful error for permission issues
            if error_code == 'AccessDeniedException':
                return jsonify({
                    "success": False,
                    "error": error_msg,
                    "error_type": "permissions",
                    "help": "See POLLY_PERMISSIONS_SETUP.md for instructions on adding Polly permissions to your IAM user"
                }), 403  # 403 Forbidden is more appropriate for permission errors
            else:
                return jsonify({
                    "success": False,
                    "error": error_msg,
                    "error_code": error_code
                }), 500
        
        audio_data = audio_result.get("audio_data")
        
        # Validate audio data
        if not audio_data:
            logger.error("Audio generation returned no audio data")
            return jsonify({
                "success": False,
                "error": "Audio generation completed but no audio data was returned"
            }), 500
        
        if len(audio_data) == 0:
            logger.error("Audio generation returned empty audio data (0 bytes)")
            return jsonify({
                "success": False,
                "error": "Audio generation returned empty file (0 bytes). This may indicate a voice synthesis issue."
            }), 500
        
        logger.info(f"Audio generation successful: {len(audio_data)} bytes")
        
        # Create BytesIO object and ensure it's at the beginning
        audio_stream = io.BytesIO(audio_data)
        audio_stream.seek(0)
        
        # Return audio file directly (for testing - not saving to system)
        response = send_file(
            audio_stream,
            mimetype='audio/mpeg',
            as_attachment=True,
            download_name=f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
        )
        
        # Set content length header to ensure full file is sent
        response.headers['Content-Length'] = str(len(audio_data))
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating audio: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@audio_test_bp.route('/preview-script', methods=['POST'])
def preview_script():
    """
    Generate script without audio (for preview)
    
    Accepts:
    - report_data: JSON, PDF, or Word (DOCX) file
    """
    try:
        report_data = None
        file_bytes = None
        filename = None
        
        if 'report_data' in request.files:
            file = request.files['report_data']
            if file.filename:
                filename = file.filename
                file_bytes = file.read()
                filename_lower = filename.lower()
                
                # Check file type
                if filename_lower.endswith('.json'):
                    # JSON file
                    try:
                        report_data = json.loads(file_bytes.decode('utf-8'))
                    except json.JSONDecodeError:
                        return jsonify({
                            "success": False,
                            "error": "Invalid JSON file"
                        }), 400
                elif filename_lower.endswith('.pdf') or filename_lower.endswith('.docx') or filename_lower.endswith('.doc'):
                    # PDF or Word document - parse it
                    parser_service = DocumentParserService()
                    parse_result = parser_service.parse_document(file_bytes, filename)
                    
                    if "error" in parse_result:
                        return jsonify({
                            "success": False,
                            "error": f"Failed to parse document: {parse_result['error']}"
                        }), 400
                    
                    report_data = parse_result.get("structured_data", {})
                    if not report_data:
                        return jsonify({
                            "success": False,
                            "error": "Document parsed but no structured data extracted"
                        }), 400
                else:
                    return jsonify({
                        "success": False,
                        "error": f"Unsupported file type: {filename}. Supported: JSON, PDF, DOCX"
                    }), 400
        elif request.is_json:
            report_data = request.get_json()
        elif 'report_data' in request.form:
            try:
                report_data = json.loads(request.form['report_data'])
            except json.JSONDecodeError:
                return jsonify({
                    "success": False,
                    "error": "Invalid JSON in form data"
                }), 400
        
        if not report_data:
            return jsonify({
                "success": False,
                "error": "No report data provided"
            }), 400
        
        # Get prompt settings from request
        prompt_settings = {
            'language': request.form.get('language', 'en'),
            'length': request.form.get('length', 'medium'),
            'tone': request.form.get('tone', 'warm'),
            'detail': request.form.get('detail', 'moderate'),
            'focus': request.form.get('focus', 'balanced')
        }
        
        logger.info(f"Generating script with settings: {prompt_settings}")
        
        script_service = AudioScriptService()
        script_result = script_service.generate_script(report_data, prompt_settings=prompt_settings)
        
        if "error" in script_result:
            return jsonify({
                "success": False,
                "error": script_result["error"]
            }), 500
        
        # Apply guardrails
        guardrails_service = AudioGuardrailsService()
        dialogue = script_result.get("dialogue", {})
        ssml = dialogue.get("ssml", "")
        
        guardrails_result = guardrails_service.filter_ssml(ssml)
        if guardrails_result.get("success"):
            dialogue["ssml"] = guardrails_result.get("filtered_ssml", ssml)
        
        return jsonify({
            "success": True,
            "script": script_result
        })
        
    except Exception as e:
        logger.error(f"Error previewing script: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@audio_test_bp.route('/upload-to-patient', methods=['POST'])
def upload_to_patient():
    """
    Upload generated audio file to patient's reports (adminfiles table)
    
    Expects:
    - audio_file: MP3 file
    - patient_id: Patient ID
    """
    try:
        from flask_login import current_user
        from flask import current_app
        
        # Check if user is authenticated and is admin
        if not current_user.is_authenticated or current_user.role != 'admin':
            return jsonify({
                "success": False,
                "error": "Unauthorized - Admin access required"
            }), 403
        
        # Get patient ID
        patient_id = request.form.get('patient_id')
        if not patient_id:
            return jsonify({
                "success": False,
                "error": "Patient ID is required"
            }), 400
        
        try:
            patient_id = int(patient_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": "Invalid patient ID"
            }), 400
        
        # Verify patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                "success": False,
                "error": f"Patient with ID {patient_id} not found"
            }), 404
        
        # Get audio file
        if 'audio_file' not in request.files:
            return jsonify({
                "success": False,
                "error": "No audio file provided"
            }), 400
        
        audio_file = request.files['audio_file']
        if not audio_file.filename:
            return jsonify({
                "success": False,
                "error": "No audio file selected"
            }), 400
        
        audio_bytes = audio_file.read()
        if not audio_bytes:
            return jsonify({
                "success": False,
                "error": "Audio file is empty"
            }), 400
        
        logger.info(f"Uploading audio to patient {patient_id} ({patient.name})")
        
        # Upload to S3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        
        bucket_name = os.getenv('S3_BUCKET_NAME')
        if not bucket_name:
            return jsonify({
                "success": False,
                "error": "S3 bucket not configured"
            }), 500
        
        # Generate S3 key
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"audio_report_{timestamp}.mp3"
        s3_key = f"patients/{patient_id}/reports/admin-files/{filename}"
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=audio_bytes,
            ContentType='audio/mpeg'
        )
        
        logger.info(f"Audio uploaded to S3: {s3_key}")
        
        # Save to adminfiles table
        try:
            new_admin_file = AdminFile(
                name=filename,
                patient_id=patient_id,
                file_type='audio/mpeg',
                file_size=len(audio_bytes),
                s3_key=s3_key,
                upload_date=datetime.utcnow(),
                file_category='Audio Report',
                is_public=False,
                analyzed=False
            )
            db.session.add(new_admin_file)
            db.session.commit()
            
            logger.info(f"Audio saved to adminfiles table for patient {patient_id}")
            
            return jsonify({
                "success": True,
                "message": f"Audio uploaded successfully to {patient.name}'s reports",
                "patient_id": patient_id,
                "patient_name": patient.name,
                "s3_key": s3_key,
                "filename": filename
            })
            
        except Exception as db_err:
            logger.error(f"Failed to save audio to adminfiles: {db_err}", exc_info=True)
            db.session.rollback()
            return jsonify({
                "success": False,
                "error": f"Failed to save to database: {str(db_err)}"
            }), 500
        
    except Exception as e:
        logger.error(f"Error uploading audio to patient: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

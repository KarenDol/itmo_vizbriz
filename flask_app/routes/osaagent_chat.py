"""Chat-related routes and helpers for the OSA agent blueprint."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from io import BytesIO
from typing import Any, Dict

import mysql.connector
import openai
import pdfplumber
import pytesseract
from anthropic import Anthropic
from flask import jsonify, redirect, render_template, request, url_for, flash
from flask_login import login_required
from langchain.chains import LLMChain
from langchain.llms import OpenAI
from langchain.prompts import PromptTemplate
from pdf2image import convert_from_bytes
from PIL import Image
from PyPDF2 import PdfReader

from flask_app.models import Patient
from flask_app.routes.osaagent_helpers import query_bedrock_claude_enhanced


logger = logging.getLogger(__name__)

s3_client = None
_dr_briz_instance = None


def register_chat_routes(osaagent, shared_s3_client):
    """Attach chat-related routes to the given blueprint and store shared dependencies."""

    global s3_client, _dr_briz_instance
    s3_client = shared_s3_client
    _dr_briz_instance = DrBriz()

    osaagent.route('/agent/chat/<int:patient_id>', methods=['POST'])(login_required(chat_with_agent))
    osaagent.route('/agent_interface/<int:patient_id>', methods=['GET'])(login_required(agent_interface))
    osaagent.route('/agent_chat')(agent_chat)
    osaagent.route('/agent/initial_analysis/<int:patient_id>', methods=['GET'])(login_required(agent_initial_analysis))

    return {
        "chat_with_agent": chat_with_agent,
        "agent_interface": agent_interface,
        "agent_chat": agent_chat,
        "agent_initial_analysis": agent_initial_analysis,
        "analyze_all_patient_medical_files": analyze_all_patient_medical_files,
        "extract_text_from_file": extract_text_from_file,
        "extract_observations_from_text": extract_observations_from_text,
        "get_extraction_prompt": get_extraction_prompt,
        "get_general_observations_prompt": get_general_observations_prompt,
        "extract_observations_openai": extract_observations_openai,
        "extract_observations_claude": extract_observations_claude,
        "standardize_ai_response": standardize_ai_response,
        "summarize_confidence": summarize_confidence,
        "dr_briz": _dr_briz_instance,
    }


class DrBriz:
    def __init__(self):
        self.name = "Dr. Briz"
        self.role = "AI dental assistant"

    def generate_response(self, patient_id, user_message):
        """Generate a response using OpenAI API."""
        try:
            patient = Patient.query.get(patient_id)
            if not patient:
                logger.error("Patient with ID %s not found", patient_id)
                return {"success": False, "message": "Patient not found"}

            system_prompt = f"""
            You are {self.name}, an AI dental assistant specializing in sleep apnea treatment.

            PATIENT INFORMATION:
            Name: {patient.name}
            ID: {patient.id}

            YOUR ROLE:
            - Help patients understand their OSA treatment journey
            - Provide information about sleep apnea treatment
            - Be warm, professional, and informative
            """

            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
            )

            assistant_response = response.choices[0].message.content
            return {"success": True, "message": assistant_response}

        except Exception as exc:
            logger.error("Error generating response: %s", exc)
            return {"success": False, "message": f"Error: {str(exc)}"}

    def analyze_all_patient_data(self, patient_id):
        return analyze_all_patient_medical_files(patient_id)


def chat_with_agent(patient_id):
    """Endpoint for chatting with the agent using standardized canonical data."""
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({"success": False, "message": "Message is required"}), 400

        user_message = data['message']

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"success": False, "message": "Patient not found"}), 404

        canonical_data = None
        try:
            from flask_app.models import PatientCaseEnvelope

            canonical_envelope = PatientCaseEnvelope.query.filter_by(
                patient_id=patient_id,
                report_id='canonical',
            ).first()

            if canonical_envelope and canonical_envelope.case_json:
                canonical_data = canonical_envelope.case_json
                logger.info("Loaded canonical data for patient %s", patient_id)
            else:
                logger.info("No canonical data found for patient %s", patient_id)
        except Exception as exc:
            logger.error("Error loading canonical data for patient %s: %s", patient_id, exc)
            canonical_data = None

        observation_data = []
        try:
            conn = mysql.connector.connect(
                host='vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
                user='admin',
                password='Vizbriz2025!',
                database='vizbriz',
                port=3306,
            )
            cursor = conn.cursor(dictionary=True)
            query = (
                """
                SELECT source_type, source_text, extracted_observations, created_at
                FROM observation_store
                WHERE patient_id = %s
                ORDER BY created_at DESC
                """
            )
            cursor.execute(query, (patient_id,))
            db_observations = cursor.fetchall()

            logger.info(
                "Found %s observations in observation store for patient %s",
                len(db_observations),
                patient_id,
            )

            if db_observations:
                for obs in db_observations:
                    try:
                        obs_data = (
                            json.loads(obs['extracted_observations'])
                            if obs['extracted_observations']
                            else {}
                        )
                        observation_data.append(
                            {
                                'source_type': obs['source_type'],
                                'observation': obs_data.get('observation', ''),
                                'value': obs_data.get('value', ''),
                                'evidence': obs_data.get('evidence', ''),
                                'confidence': obs_data.get('confidence', 0),
                                'created_at': obs['created_at'].isoformat()
                                if obs['created_at']
                                else None,
                            }
                        )
                    except Exception as exc:
                        logger.warning(
                            "Error parsing observation for patient %s: %s",
                            patient_id,
                            exc,
                        )
                        continue

            cursor.close()
            conn.close()

        except Exception as exc:
            logger.error(
                "Error loading observation store data for patient %s: %s",
                patient_id,
                exc,
            )
            observation_data = []

        from flask_app.routes.cursor_routes import get_execution_manifest

        execution_manifest_response = get_execution_manifest(patient_id)
        if hasattr(execution_manifest_response, 'get_json'):
            execution_manifest = execution_manifest_response.get_json()
        else:
            execution_manifest = execution_manifest_response

        packet = {
            "patient": {
                "id": str(patient.id),
                "name": patient.name or "Unknown",
                "age": None,
            },
            "canonical_clinical_data": canonical_data
            if canonical_data
            else {
                "demographics": {"sex": patient.gender, "age_years": None},
                "sleep_study": {
                    "study_type": "unknown",
                    "ahi": None,
                    "odi": None,
                    "o2_nadir_pct": None,
                },
                "observations": {"summary": [], "anatomy_imaging": {}},
                "treatment_considerations": {
                    "primary_pathway": [],
                    "adjuncts": [],
                    "cautions": [],
                },
                "device_design": {
                    "mandibular_advancement_mm": None,
                    "vertical_opening_mm": None,
                },
            },
            "observation_store_data": observation_data,
            "operational_data": (
                {
                    "workflow_progress": {
                        "current_stage": execution_manifest.get('current_stage', 'Unknown'),
                        "completion_pct": execution_manifest.get('progress_percentage', 0),
                        "total_stages": len(execution_manifest.get('stage_manifest', [])),
                        "current_stage_index": sum(
                            1
                            for stage in execution_manifest.get('stage_manifest', [])
                            if stage.get('value') == 'yes'
                        ),
                    },
                    "pending_actions": [
                        {
                            "action": action.get('label', 'Unknown action'),
                            "priority": "normal",
                        }
                        for action in execution_manifest.get('eligible_actions', [])[:3]
                    ],
                    "alerts": [],
                }
                if execution_manifest
                else {
                    "workflow_progress": {
                        "current_stage": "Unknown",
                        "completion_pct": 0,
                        "total_stages": 0,
                        "current_stage_index": 0,
                    },
                    "pending_actions": [],
                    "alerts": [],
                }
            ),
        }

        logger.info("Chatbot data for patient %s:", patient.id)
        logger.info("  - Canonical data exists: %s", canonical_data is not None)
        logger.info("  - Execution manifest exists: %s", execution_manifest is not None)
        logger.info("  - Packet data: %s", json.dumps(packet, indent=2))

        prompt = f"""
        You are Dr. Briz, an expert sleep medicine AI assistant specializing in OSA treatment and dental sleep therapy.

        CRITICAL: Only use the data provided below. Do NOT make up, assume, or hallucinate any patient information that is not explicitly provided.

        PATIENT INFORMATION:
        Name: {patient.name}
        ID: {patient.id}

        STANDARDIZED CLINICAL DATA:
        {json.dumps(packet['canonical_clinical_data'], indent=2)}

        OBSERVATION STORE DATA (Quiz responses, extracted observations, etc.):
        {json.dumps(packet['observation_store_data'], indent=2)}

        OPERATIONAL WORKFLOW DATA:
        {json.dumps(packet['operational_data'], indent=2)}

        USER QUESTION: {user_message}

        IMPORTANT RULES:
        1. ONLY use the data provided above - do not invent or assume any patient details
        2. If data shows "null", "Unknown", or empty values, acknowledge the limited information available
        3. If no canonical clinical data exists, state that comprehensive clinical assessment requires additional data
        4. Base your response ONLY on the actual data provided, not on general OSA knowledge
        5. Use observation store data (quiz responses, extracted observations) as primary source for patient symptoms and clinical findings

        Please provide a helpful, professional response as Dr. Briz. Consider:
        1. The patient's actual clinical data from observation store (quiz responses, extracted observations)
        2. Their current stage in the treatment workflow (only what's provided)
        3. What steps have been completed and what's next (only what's provided)
        4. Any specific recommendations based on their actual clinical profile (only what's provided)
        5. How you can assist with their OSA treatment journey

        Keep your response conversational, informative, and actionable for the dental team.
        Use the observation store data as your primary source for patient symptoms and clinical findings.
        """

        messages = [{"role": "user", "content": prompt}]

        result = query_bedrock_claude_enhanced(
            messages,
            max_tokens=600,
            temperature=0.3,
            patient_id=patient_id,
        )

        if result.get('success'):
            assistant_response = result.get(
                'response',
                "I'm here to help with your patient's OSA treatment journey.",
            )
            return jsonify(
                {
                    "success": True,
                    "message": assistant_response,
                    "patient_id": patient_id,
                    "patient_name": patient.name,
                    "data_source": "Canonical Schema"
                    if canonical_data
                    else "Fallback Data",
                }
            )

        fallback_response = (
            "Hello! I'm Dr. Briz, your AI assistant. I can see that {name} is currently "
            "in the OSA treatment workflow. I can help you with treatment planning, "
            "progress tracking, and answering questions about their case. What "
            "specific information would you like to know?"
        ).format(name=patient.name)

        return jsonify(
            {
                "success": True,
                "message": fallback_response,
                "patient_id": patient_id,
                "patient_name": patient.name,
                "data_source": "Fallback Response",
            }
        )

    except Exception as exc:
        logger.error("Error in enhanced chat endpoint: %s", exc)
        return jsonify({"success": False, "message": f"Error: {str(exc)}"}), 500


def agent_interface(patient_id):
    """Render the agent chat interface."""
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            flash("Patient not found", "error")
            return redirect(url_for('main.patient_list'))

        return render_template('agent_chat.html', patient=patient)

    except Exception as exc:
        logger.error("Error rendering agent interface: %s", exc)
        flash(f"Error: {str(exc)}", "error")
        return redirect(url_for('main.patient_list'))


def agent_chat():
    return render_template('agent_chat.html')


def agent_initial_analysis(patient_id):
    result = _dr_briz_instance.analyze_all_patient_data(patient_id)
    return jsonify(result)


def analyze_all_patient_medical_files(patient_id):
    s3_prefix = f"patients/{patient_id}/medical/"
    files_list = []
    paginator = s3_client.get_paginator('list_objects_v2')
    operation_parameters = {
        'Bucket': os.getenv('S3_BUCKET_NAME'),
        'Prefix': s3_prefix,
    }
    page_iterator = paginator.paginate(**operation_parameters)
    for page in page_iterator:
        if 'Contents' in page:
            for obj in page['Contents']:
                file_key = obj['Key']
                file_name = os.path.basename(file_key)
                if not file_name.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
                    continue
                files_list.append(file_key)

    all_observations = []
    for file_key in files_list:
        obj = s3_client.get_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=file_key)
        file_content = obj['Body'].read()
        try:
            text = extract_text_from_file(file_content)
        except Exception as exc:
            logger.error("Failed to extract text from %s: %s", file_key, exc)
            continue

        file_key_lower = file_key.lower()
        if '/questionnaire/' in file_key_lower:
            datasource_id = 2
        elif '/sleep_tests/' in file_key_lower:
            datasource_id = 1
        else:
            datasource_id = 3

        result = extract_observations_from_text(text, datasource_id)
        if result and result.get('success'):
            all_observations.extend(
                result['data'].get('datasource_observations', [])
            )
            all_observations.extend(result['data'].get('general_observations', []))

    obs_text = "\n".join(
        [
            f"- {obs.get('observation', '')}: {obs.get('value', '')} {obs.get('unit', '')} "
            f"(evidence: {obs.get('evidence', '')})"
            for obs in all_observations
        ]
    )
    ai_prompt = (
        "You are an expert sleep medicine doctor. "
        "Given the following patient observations from medical records, "
        "provide a concise diagnosis and summary of the patient's OSA status. "
        "List any key findings and suggest next diagnostic steps if needed.\n\n"
        f"Patient Observations:\n{obs_text}"
    )

    ai_response = openai.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "system", "content": ai_prompt}],
        temperature=0.3,
    )
    diagnosis = ai_response.choices[0].message.content

    return {
        "observations": all_observations,
        "diagnosis": diagnosis,
        "openai_prompt": ai_prompt,
    }


def extract_text_from_file(file_content):
    """Extract text from PDF or image file content."""
    logger.debug("=== Starting text extraction process ===")
    try:
        try:
            logger.debug("Attempting PDF extraction with PdfReader...")
            pdf_reader = PdfReader(BytesIO(file_content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"

            if text.strip():
                logger.debug("Successfully extracted text using PdfReader")
                return text

            logger.debug("No text found with PdfReader, trying pdfplumber...")
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or "" + "\n"
                if text.strip():
                    logger.debug("Successfully extracted text using pdfplumber")
                    return text

            logger.debug("No text found with pdfplumber, attempting OCR...")
            images = convert_from_bytes(file_content)
            text = ""
            for image in images:
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='PNG')
                img_byte_arr = img_byte_arr.getvalue()
                pil_image = Image.open(BytesIO(img_byte_arr))
                text += pytesseract.image_to_string(pil_image)

            if text.strip():
                logger.debug("Successfully extracted text using OCR")
                return text

        except Exception as exc:
            logger.warning("PDF extraction failed, error: %s", exc)

        return ""

    except Exception as exc:
        logger.error("Text extraction failed: %s", exc)
        raise


def extract_observations_from_text(extracted_text: str, datasource_id: int) -> dict:
    """Use AI models to extract observations from text."""
    try:
        datasource_prompt, prompt_supported = get_extraction_prompt(datasource_id)
        datasource_prompt.format(text=extracted_text)

        if prompt_supported:
            return extract_observations_claude(extracted_text, datasource_id)

        return extract_observations_openai(extracted_text, datasource_id)

    except Exception as exc:
        logger.error("Error extracting observations: %s", exc)
        return {"success": False, "message": str(exc)}


def get_extraction_prompt(datasource_id: int) -> tuple[str, bool]:
    test_prompt = ""
    is_supported = False
    datasource_map = {
        1: "Sleep Study Report",
        2: "Patient Questionnaire",
        3: "Medical Records",
    }

    datasource_name = datasource_map.get(datasource_id, "Medical Records")

    prompt_template = (
        "You are an AI medical assistant specializing in obstructive sleep apnea. "
        "Extract clinically relevant observations from the following {datasource_name}."
    )

    prompt_template += (
        "\nThis should include sleep study metrics, symptoms, risk factors, and treatment history."
    )

    test_prompt = prompt_template.format(datasource_name=datasource_name)

    if datasource_id in {1, 2, 3}:
        is_supported = True

    return test_prompt, is_supported


def get_general_observations_prompt() -> str:
    return (
        "You are an expert medical assistant. Extract all relevant observations "
        "and measurements from the text below."
    )


def extract_observations_openai(extracted_text: str, datasource_id: int) -> dict:
    try:
        prompt = get_general_observations_prompt()
        llm = OpenAI(temperature=0, model_name="text-davinci-003")
        ai_prompt = PromptTemplate(
            input_variables=["text"],
            template=prompt + "\nText:\n{text}\nObservations:",
        )
        chain = LLMChain(prompt=ai_prompt, llm=llm)
        ai_response = chain.run(text=extracted_text)
        standardized_response = standardize_ai_response(
            extracted_text, json.loads(ai_response), provider="openai"
        )
        return {"success": True, "data": standardized_response}

    except Exception as exc:
        logger.error("OpenAI observation extraction failed: %s", exc)
        return {"success": False, "message": str(exc)}


def extract_observations_claude(extracted_text: str, datasource_id: int) -> dict:
    try:
        anthropic_client = Anthropic()
        prompt = get_general_observations_prompt()
        response = anthropic_client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=500,
            temperature=0.2,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt + f"\nText:\n{extracted_text}\nObservations:",
                        }
                    ],
                }
            ],
        )
        parsed_output = json.loads(response.content[0].text)
        standardized = standardize_ai_response(
            extracted_text, parsed_output, provider="claude"
        )
        return {"success": True, "data": standardized}

    except Exception as exc:
        logger.error("Claude observation extraction failed: %s", exc)
        return {"success": False, "message": str(exc)}


def standardize_ai_response(document_text: str, response_data: dict, provider: str) -> dict:
    datasource_observations = []
    general_observations = []

    if response_data and isinstance(response_data, dict):
        datasource_observations = response_data.get('datasource_observations', [])
        general_observations = response_data.get('general_observations', [])

    summary = summarize_confidence(datasource_observations)
    return {
        "datasource_observations": datasource_observations,
        "general_observations": general_observations,
        "summary": summary,
        "provider": provider,
        "document_preview": document_text[:500],
    }


def summarize_confidence(observations: list) -> dict:
    if not observations:
        return {
            "average_confidence": 0,
            "total_observations": 0,
            "high_confidence_count": 0,
        }

    confidence_scores = [obs.get('confidence', 0) for obs in observations]
    average_confidence = sum(confidence_scores) / len(confidence_scores)
    high_confidence_count = sum(1 for score in confidence_scores if score >= 0.7)

    return {
        "average_confidence": average_confidence,
        "total_observations": len(observations),
        "high_confidence_count": high_confidence_count,
    }

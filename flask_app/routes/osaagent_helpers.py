import json
import logging
import os
import re
import traceback

import boto3
from botocore.config import Config
from datetime import datetime

from flask_app.models import Patient
from flask_app.services.bedrock_service import BedrockService


logger = logging.getLogger(__name__)

_AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
S3_CLIENT = boto3.client("s3", region_name=_AWS_REGION, config=Config(signature_version="s3v4"))


def get_bedrock_client():
    """Initialize and return the centralized Bedrock client."""
    try:
        from flask_app.config.bedrock_config import get_bedrock_client as get_centralized_client

        return get_centralized_client()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Error initializing Bedrock client: %s", exc)
        return None


def query_bedrock_claude(manifest, patient_state, question, max_tokens=300, temperature=0.2, top_p=0.9):
    """Query Claude 3.5 Sonnet via Bedrock and return a success flag with the response text."""
    try:
        from flask_app.config.bedrock_config import get_bedrock_client

        bedrock_client = get_bedrock_client()
        if not bedrock_client or not bedrock_client.client:
            raise Exception("Bedrock client not available")

        bedrock = bedrock_client.client
        system_prompt = """You are Dr. Briz, an expert sleep medicine AI assistant specializing in Obstructive Sleep Apnea (OSA) treatment and dental sleep therapy. You have extensive knowledge in:

MEDICAL EXPERTISE:
- Sleep medicine and sleep disorders
- OSA diagnosis, severity assessment, and treatment options
- Dental sleep therapy and oral appliance therapy
- Sleep study interpretation and AHI scoring
- CPAP therapy and alternatives
- Sleep hygiene and lifestyle modifications
- Medical device regulations and insurance considerations

TREATMENT WORKFLOW KNOWLEDGE:
- OSA screening and risk assessment
- Sleep test types (home sleep tests vs. in-lab polysomnography)
- Consultation scheduling and patient education
- Treatment planning and device selection
- Follow-up protocols and titration
- Compliance monitoring and outcome assessment
- Referral coordination between dental and medical providers

CLINICAL GUIDELINES:
- AASM (American Academy of Sleep Medicine) guidelines
- ADA (American Dental Association) sleep medicine standards
- Insurance coverage requirements for OSA treatment
- HIPAA compliance and patient privacy
- Medical device safety and efficacy standards

PATIENT CARE APPROACH:
- Patient education and counseling
- Treatment adherence strategies
- Side effect management and troubleshooting
- Long-term follow-up and maintenance
- Emergency protocols and when to refer to specialists

RESPONSE STYLE:
- Keep responses concise and direct (2-4 sentences maximum)
- Focus on practical, actionable information
- Be warm and professional but avoid lengthy medical disclaimers
- Provide specific, relevant answers without unnecessary warnings
- Use bullet points for multiple items when helpful
- Avoid repetitive phrases like "However, I must emphasize" or "preliminary recommendations"

You provide evidence-based, professional guidance while being warm and supportive. You can answer questions about OSA treatment beyond just the patient's current workflow stage, drawing on your comprehensive medical knowledge."""

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": f"""
PATIENT CONTEXT:
Manifest (Treatment Stages):
{json.dumps(manifest, indent=2)}

Patient Current Status:
{json.dumps(patient_state, indent=2)}

USER QUESTION: {question}

Please provide a concise, direct response as Dr. Briz (2-4 sentences maximum). Focus on:
1. Direct answer to the specific question
2. Practical, actionable information
3. Relevant medical insights
4. Next steps if applicable

Keep it brief, professional, and helpful without lengthy disclaimers.
""",
            },
        ]

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        }

        bedrock_service = BedrockService()
        model_id = bedrock_service.MODELS[bedrock_service.DEFAULT_MODEL]

        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload),
        )
        result = json.loads(response["body"].read())
        answer = (
            result["content"][0]["text"]
            if result.get("content") and len(result["content"]) > 0
            else "No response from Claude."
        )
        return {"success": True, "response": answer}
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Error querying Claude 3.5 Sonnet: %s\n%s", exc, traceback.format_exc())
        return {"success": False, "message": f"Error querying Claude 3.5 Sonnet: {str(exc)}"}


def query_bedrock_claude_enhanced(
    messages,
    max_tokens=300,
    temperature=0.2,
    top_p=0.9,
    patient_id=None,
    endpoint="osaagent_routes",
    use_knowledge_base=False,
    knowledge_base_query=None,
):
    """Delegate to the centralized enhanced Bedrock query helper."""

    from flask_app.config.bedrock_config import (
        query_bedrock_claude_enhanced as enhanced_query,
    )

    return enhanced_query(
        messages,
        max_tokens,
        temperature,
        top_p,
        patient_id,
        endpoint,
        use_knowledge_base,
        knowledge_base_query,
    )


def get_patient_status_from_bedrock(
    patient_id,
    manifest_content=None,
    patient_file_content=None,
    patient_name=None,
):
    """Compute patient status and next steps using Bedrock Claude."""

    try:
        name = patient_name
        if name is None:
            patient = Patient.query.get(patient_id)
            if not patient:
                return {"success": False, "message": "Patient not found"}
            name = patient.name

        manifest_stages = []
        if manifest_content:
            try:
                manifest_stages = json.loads(manifest_content)
            except json.JSONDecodeError:
                logger.warning("Failed to parse manifest content as JSON")

        patient_data = {}
        if patient_file_content:
            try:
                patient_data = json.loads(patient_file_content)
            except json.JSONDecodeError:
                logger.warning("Failed to parse patient file content as JSON")

        policy_json = None
        try:
            bucket = os.getenv("S3_BUCKET_NAME")
            if bucket:
                policy_key = f"patients/{patient_id}/manifests/osa_policy_v2.json"
                obj = S3_CLIENT.get_object(Bucket=bucket, Key=policy_key)
                policy_str = obj["Body"].read().decode("utf-8")
                policy_json = json.loads(policy_str)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.info(
                "No policy JSON found for patient %s or failed to parse: %s",
                patient_id,
                exc,
            )

        if policy_json is None:
            try:
                config_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "config"
                )
                local_policy_path = os.path.join(
                    config_dir, f"osa_policy_v2_patient_{patient_id}_generic"
                )
                if os.path.exists(local_policy_path):
                    with open(local_policy_path, "r", encoding="utf-8") as file_obj:
                        policy_json = json.load(file_obj)
                        logger.info(
                            "Loaded local patient policy from %s", local_policy_path
                        )
            except Exception as exc:  # pragma: no cover
                logger.info(
                    "No local patient policy found or failed to parse: %s", exc
                )

        def _load_base_policy() -> dict:
            try:
                base_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    "config",
                    "osa_policy_base_v2.json",
                )
                with open(base_path, "r", encoding="utf-8") as base_file:
                    return json.load(base_file)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to load base policy: %s", exc)
                return {}

        def _build_phenotype_from_patient_data(data: dict) -> dict:
            phenotype = {}
            try:
                diag = (data or {}).get("diagnosis") or {}
                if "osa_severity" in diag:
                    phenotype["severity"] = diag.get("osa_severity")
                if "ahi_score" in diag:
                    phenotype["AHI"] = diag.get("ahi_score")
                if "odi" in diag:
                    phenotype["ODI"] = diag.get("odi")
                if "spo2_nadir" in diag:
                    phenotype["SpO2_nadir_percent"] = diag.get("spo2_nadir")
                if "cpap_intolerance" in data:
                    phenotype["cpap_intolerance"] = bool(data.get("cpap_intolerance"))
                if "primary_narrowing_site" in data:
                    phenotype["primary_narrowing_site"] = data.get(
                        "primary_narrowing_site"
                    )
                if "tmj_findings_present" in data:
                    phenotype["tmj_findings_present"] = bool(
                        data.get("tmj_findings_present")
                    )
                if "nasal_obstruction_present" in data:
                    phenotype["nasal_obstruction_present"] = bool(
                        data.get("nasal_obstruction_present")
                    )
            except Exception:  # pragma: no cover - best effort
                pass
            return phenotype

        def _build_phenotype_from_observations(obs: dict) -> dict:
            if not isinstance(obs, dict):
                return {}
            phenotype = {}
            try:
                for source, items in (obs or {}).items():
                    for item in (items or []):
                        name_lower = (item.get("observation") or "").lower()
                        value = item.get("value")

                        if "ahi" in name_lower and "central" not in name_lower:
                            try:
                                value_str = str(value).strip()
                                if isinstance(value, (int, float)):
                                    ahi_value = float(value)
                                else:
                                    numbers = re.findall(r"\d+(?:\.\d+)?", value_str)
                                    ahi_value = float(numbers[0]) if numbers else None

                                if ahi_value is not None:
                                    phenotype["AHI"] = ahi_value
                                    if ahi_value < 5:
                                        phenotype["AHI_category"] = "normal"
                                        phenotype["osa_severity"] = "normal"
                                    elif ahi_value < 15:
                                        phenotype["AHI_category"] = "mild"
                                        phenotype["osa_severity"] = "mild"
                                    elif ahi_value < 30:
                                        phenotype["AHI_category"] = "moderate"
                                        phenotype["osa_severity"] = "moderate"
                                    else:
                                        phenotype["AHI_category"] = "severe"
                                        phenotype["osa_severity"] = "severe"
                                else:
                                    phenotype["AHI"] = value_str
                                    phenotype.setdefault("osa_severity", "unknown")
                            except Exception as exc:  # pragma: no cover
                                logger.error("Error parsing AHI value '%s': %s", value, exc)
                                phenotype["AHI"] = value
                                phenotype.setdefault("osa_severity", "unknown")

                        elif "spo2" in name_lower and (
                            "nadir" in name_lower or "lowest" in name_lower
                        ):
                            try:
                                spo2_value = float(str(value).replace("%", "").split()[0])
                                phenotype["SpO2_nadir_percent"] = spo2_value
                                if spo2_value < 88:
                                    phenotype["hypoxia_severity"] = "severe"
                                elif spo2_value < 92:
                                    phenotype["hypoxia_severity"] = "moderate"
                                elif spo2_value < 95:
                                    phenotype["hypoxia_severity"] = "mild"
                                else:
                                    phenotype["hypoxia_severity"] = "normal"
                            except Exception:  # pragma: no cover
                                phenotype["SpO2_nadir_percent"] = value

                        elif "cpap" in name_lower and (
                            "intoler" in name_lower or "refuse" in name_lower
                        ):
                            phenotype["cpap_intolerance"] = (
                                str(value).strip().lower() in ["true", "yes", "y", "1"]
                            ) or bool(value)

                        elif (
                            "primary" in name_lower
                            and "narrow" in name_lower
                            and "site" in name_lower
                        ):
                            phenotype["primary_narrowing_site"] = str(value)

                        elif "tmj" in name_lower:
                            phenotype["tmj_findings_present"] = True
                            phenotype.setdefault("tmj_findings", {})
                            if "pain" in name_lower or "vas" in name_lower:
                                try:
                                    pain_value = float(str(value).split()[0])
                                    phenotype["tmj_findings"]["pain_vas"] = pain_value
                                except Exception:
                                    pass
                            elif "click" in name_lower:
                                phenotype["tmj_findings"]["clicking"] = (
                                    str(value).strip().lower() in ["true", "yes", "y", "1"]
                                ) or bool(value)
                            elif "lock" in name_lower:
                                phenotype["tmj_findings"]["locking"] = (
                                    str(value).strip().lower() in ["true", "yes", "y", "1"]
                                ) or bool(value)

                        elif "nasal" in name_lower and (
                            "obstruction" in name_lower or "valve" in name_lower
                        ):
                            phenotype["nasal_obstruction_present"] = True
                            source_lower = source.lower()
                            if "cbct" in source_lower or "imaging" in source_lower:
                                phenotype["nasal_obstruction_source"] = "cbct"
                            elif "clinical" in source_lower or "exam" in source_lower:
                                phenotype["nasal_obstruction_source"] = "clinical"
                            elif "pnif" in name_lower:
                                phenotype["nasal_obstruction_source"] = "pnif"
                            else:
                                phenotype["nasal_obstruction_source"] = "clinical"

                        elif "rera" in name_lower and "index" in name_lower:
                            try:
                                phenotype["RERA_index"] = float(str(value).split()[0])
                            except Exception:
                                phenotype["RERA_index"] = value

                        elif "airflow" in name_lower and "limitation" in name_lower:
                            try:
                                phenotype["airflow_limitation_pct_TST"] = float(
                                    str(value).replace("%", "").split()[0]
                                )
                            except Exception:
                                phenotype["airflow_limitation_pct_TST"] = value

                        elif "insomnia" in name_lower or "isi" in name_lower:
                            try:
                                phenotype["ISI_score"] = float(str(value).split()[0])
                            except Exception:
                                phenotype["ISI_score"] = value
            except Exception:  # pragma: no cover
                pass
            return phenotype

        def _merge_policy_with_patient(base: dict, phenotype: dict, pid: int) -> dict:
            if not base:
                return {}
            merged = json.loads(json.dumps(base))
            merged["applies_to"] = {
                "patient_id": str(pid),
                "phenotype_summary": phenotype,
            }
            try:
                scoring = merged.get("scoring_modifiers", [])
                if phenotype.get("osa_severity") == "severe":
                    scoring.insert(
                        0,
                        {
                            "when": "osa_severity==severe",
                            "boost": {
                                "CPAP_primary": 0.2,
                                "oral_appliance_adjunct": 0.1,
                            },
                        },
                    )
                elif phenotype.get("osa_severity") == "moderate":
                    scoring.insert(
                        0,
                        {
                            "when": "osa_severity==moderate",
                            "boost": {"oral_appliance_primary": 0.15},
                        },
                    )
                elif phenotype.get("osa_severity") == "mild":
                    scoring.insert(
                        0,
                        {
                            "when": "osa_severity==mild",
                            "boost": {
                                "oral_appliance_primary": 0.1,
                                "positional_therapy": 0.05,
                            },
                        },
                    )

                if phenotype.get("hypoxia_severity") == "severe":
                    scoring.insert(
                        0,
                        {
                            "when": "hypoxia_severity==severe",
                            "boost": {"CPAP_primary": 0.15},
                        },
                    )

                if phenotype.get("tmj_findings_present") is True:
                    tmj_penalty = 0.1
                    if phenotype.get("tmj_findings", {}).get("pain_vas", 0) > 5:
                        tmj_penalty = 0.2
                    scoring.insert(
                        0,
                        {
                            "when": "tmj_findings_present==true",
                            "penalty": {"aggressive_MA": tmj_penalty},
                        },
                    )

                if phenotype.get("primary_narrowing_site", "").lower() == "velopharyngeal":
                    scoring.insert(
                        0,
                        {
                            "when": "primary_narrowing_site==velopharyngeal",
                            "boost": {"mandibular_advancement_appliance": 0.05},
                        },
                    )

                if phenotype.get("cpap_intolerance") is True:
                    scoring.insert(
                        0,
                        {
                            "when": "cpap_intolerance==true",
                            "boost": {"oral_appliance_primary": 0.2},
                        },
                    )

                merged["scoring_modifiers"] = scoring

                adjuncts = merged.get("adjunct_therapies", [])
                for adj in adjuncts:
                    if (
                        adj.get("name") == "nasal_airflow_optimization"
                        and phenotype.get("nasal_obstruction_present") is True
                    ):
                        adj["priority"] = "high"
                        if phenotype.get("nasal_obstruction_source") == "cbct":
                            adj["urgency"] = "immediate"
                    elif (
                        adj.get("name") == "myofunctional_therapy"
                        and phenotype.get("tmj_findings_present") is True
                    ):
                        adj["priority"] = "high"
                    elif (
                        adj.get("name") == "positional_therapy"
                        and phenotype.get("osa_severity") == "mild"
                    ):
                        adj["priority"] = "moderate"

                merged["adjunct_therapies"] = adjuncts
            except Exception:  # pragma: no cover
                pass
            return merged

        def _deep_merge_dicts(base: dict, override: dict) -> dict:
            if not isinstance(base, dict):
                return json.loads(json.dumps(override))
            result = json.loads(json.dumps(base))
            for key, value in (override or {}).items():
                if (
                    key in result
                    and isinstance(result[key], dict)
                    and isinstance(value, dict)
                ):
                    result[key] = _deep_merge_dicts(result[key], value)
                else:
                    result[key] = json.loads(json.dumps(value))
            return result

        def _to_generic_device(name: str) -> str:
            lowered = (name or "").lower()
            if "herbst" in lowered:
                return "Herbst family mandibular advancement appliance"
            if "tap" in lowered:
                return "TAP family mandibular advancement appliance"
            if "tongue" in lowered and (
                "retaining" in lowered or "retainer" in lowered
            ):
                return "tongue retaining device"
            return name

        def _make_vendor_neutral(policy: dict) -> dict:
            try:
                sanitized = json.loads(json.dumps(policy))
                prefs = sanitized.get("clinic_preferences", {})
                devices = prefs.get("preferred_devices", []) or []
                generic_devices = []
                seen = set()
                for device in devices:
                    generic = _to_generic_device(device)
                    if generic and generic not in seen:
                        seen.add(generic)
                        generic_devices.append(generic)
                prefs["preferred_devices"] = generic_devices
                sanitized["clinic_preferences"] = prefs
                return sanitized
            except Exception:  # pragma: no cover
                return policy

        base_policy = _load_base_policy()
        phenotype_summary = _build_phenotype_from_patient_data(patient_data)

        clinical_observations = {}
        clinical_observations_transformed = {}
        try:
            from flask_app.routes.main_routes import load_document_observations

            clinical_observations = load_document_observations(patient_id) or {}
            obs_pheno = _build_phenotype_from_observations(clinical_observations)
            for key, value in obs_pheno.items():
                if key not in phenotype_summary or phenotype_summary.get(key) in [
                    None,
                    "",
                    0,
                ]:
                    phenotype_summary[key] = value

            def _transform_observations_for_snapshot(obs_by_source: dict) -> dict:
                transformed = {}
                if not isinstance(obs_by_source, dict):
                    return transformed
                for source, items in (obs_by_source or {}).items():
                    transformed[source] = [
                        {
                            "name": item.get("observation"),
                            "value": item.get("value"),
                            "unit": item.get("unit"),
                            "evidence": item.get("evidence"),
                            "confidence": item.get("confidence"),
                        }
                        for item in (items or [])
                    ]
                return transformed

            clinical_observations_transformed = _transform_observations_for_snapshot(
                clinical_observations
            )
        except Exception as exc:  # pragma: no cover
            logger.info(
                "Clinical observations unavailable or failed to parse: %s", exc
            )

        policy_with_overrides = _deep_merge_dicts(base_policy, policy_json or {})
        merged_policy = _merge_policy_with_patient(
            policy_with_overrides, phenotype_summary, patient_id
        )
        merged_policy = _make_vendor_neutral(merged_policy)

        system_prompt = """You are Dr. Briz, an expert sleep medicine AI assistant specializing in Obstructive Sleep Apnea (OSA) treatment and dental sleep therapy. You have extensive knowledge in:

MEDICAL EXPERTISE:
- Sleep medicine and sleep disorders
- OSA diagnosis, severity assessment, and treatment options
- Dental sleep therapy and oral appliance therapy
- Sleep study interpretation and AHI scoring
- CPAP therapy and alternatives
- Sleep hygiene and lifestyle modifications
- Medical device regulations and insurance considerations

TREATMENT WORKFLOW KNOWLEDGE:
- OSA screening and risk assessment
- Sleep test types (home sleep tests vs. in-lab polysomnography)
- Consultation scheduling and patient education
- Treatment planning and device selection
- Follow-up protocols and titration
- Compliance monitoring and outcome assessment
- Referral coordination between dental and medical providers

CLINICAL GUIDELINES:
- AASM (American Academy of Sleep Medicine) guidelines
- ADA (American Dental Association) sleep medicine standards
- Insurance coverage requirements for OSA treatment
- HIPAA compliance and patient privacy
- Medical device safety and efficacy standards

PATIENT CARE APPROACH:
- Patient education and counseling
- Treatment adherence strategies
- Side effect management and troubleshooting
- Long-term follow-up and maintenance
- Emergency protocols and when to refer to specialists

You provide evidence-based, professional guidance while being warm and supportive. You can answer questions about OSA treatment beyond just the patient's current workflow stage, drawing on your comprehensive medical knowledge.

STRICT OUTPUT RULES:
- Use vendor-neutral language; never mention brand or manufacturer names.
- When recommending therapy, refer only to device families/types or generic modalities (e.g., mandibular advancement device family such as "Herbst family", TAP family, tongue retaining device, positional therapy, nasal airflow optimization).
- If an action is recommended, append a line starting with exactly: ACTION_JSON: followed by a compact JSON object matching the policy action_mapping (or null if no action). Do not add commentary after the JSON.
"""

        allowed_actions_summary = {}
        try:
            from flask_app.config.action_manifest import get_all_actions

            all_actions = get_all_actions()
            for action_key, cfg in (all_actions or {}).items():
                allowed_actions_summary[action_key] = {
                    "parameters": cfg.get("parameters", []),
                    "category": cfg.get("category", ""),
                    "description": cfg.get("description", ""),
                }
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to build action manifest summary: %s", exc)

        example_user_1 = (
            "EXAMPLE 1 INPUT\n"
            "Policy: base policy allows mandibular advancement devices; TMJ symptoms present.\n"
            "Phenotype: severe OSA (AHI 45), CPAP intolerance true, primary narrowing site velopharyngeal, tmj_findings_present true, nasal_obstruction_present false.\n"
            "Allowed actions include request_hipaa_consent and schedule_sleep_test_review.\n"
            "Task: Provide concise, vendor-neutral recommendation and one ACTION_JSON line.\n"
        )

        example_assistant_1 = (
            "CURRENT STATUS: Severe obstructive pattern with CPAP intolerance and TMJ sensitivity.\n"
            "NEXT STEPS: Consider mandibular advancement device family with conservative initial advancement and gradual titration; review nasal airflow as needed.\n"
            "RISKS/FOLLOW-UP: Monitor TMJ symptoms; schedule follow-up sleep study after stabilization.\n"
            "ACTION_JSON: {\"action_key\": \"request_hipaa_consent\", \"parameters\": {\"patient_id\": 12345, \"patient_email\": \"patient@example.com\", \"request_date\": \"2025-08-11\", \"message\": \"Please complete HIPAA consent to proceed with treatment.\"}}"
        )

        example_user_2 = (
            "EXAMPLE 2 INPUT\n"
            "Policy: adjunct imaging and records required before ordering device.\n"
            "Phenotype: moderate OSA (AHI 18), positional false, cpap_intolerance true; missing intraoral scan.\n"
            "Allowed actions include request_intraoral_scan.\n"
            "Task: Be concise and emit one ACTION_JSON line.\n"
        )

        example_assistant_2 = (
            "CURRENT STATUS: Candidate for oral appliance pending records.\n"
            "NEXT STEPS: Proceed with a mandibular advancement device family after obtaining digital impressions/scans.\n"
            "PREREQUISITES: Collect intra-oral scan before ordering.\n"
            "ACTION_JSON: {\"action_key\": \"request_intraoral_scan\", \"parameters\": {\"patient_id\": 12345}}"
        )

        user_prompt = f"""
PATIENT INFORMATION:
Name: {name}
ID: {patient_id}

TREATMENT MANIFEST (Available Stages):
{json.dumps(manifest_stages, indent=2) if manifest_stages else "No manifest file available"}

PATIENT CURRENT STATUS:
{json.dumps(patient_data, indent=2) if patient_data else "No patient file available"}

 CLINIC POLICY (Merged: base + per-patient overrides + phenotype):
 {json.dumps(merged_policy, indent=2) if merged_policy else "No policy JSON available"}

 CLINICAL OBSERVATIONS (by source):
 {json.dumps(clinical_observations_transformed, indent=2) if clinical_observations_transformed else (json.dumps(clinical_observations, indent=2) if clinical_observations else "No observations available")}

 ALLOWED ACTIONS (keys, parameters, category):
 {json.dumps(allowed_actions_summary, indent=2)}

Please provide a comprehensive assessment as Dr. Briz. Include:

1. CURRENT STATUS: What stage is the patient currently in?
2. NEXT STEPS: What specific actions need to be taken next?
3. PREREQUISITES: What requirements still need to be met?
4. PROGRESS: How far along is the patient in their treatment journey?
5. RECOMMENDATIONS: Any specific recommendations for the dental team?
6. MEDICAL INSIGHTS: Additional medical context and best practices
7. TREATMENT OPTIONS: Available treatment modalities and considerations

Draw on your comprehensive medical knowledge to provide actionable insights for the dental team.
At the end, emit one line:
ACTION_JSON: {"action_key": "...", "parameters": {...}} or ACTION_JSON: null
"""

        bedrock_messages = [
            {"role": "assistant", "content": system_prompt},
            {"role": "user", "content": example_user_1},
            {"role": "assistant", "content": example_assistant_1},
            {"role": "user", "content": example_user_2},
            {"role": "assistant", "content": example_assistant_2},
            {"role": "user", "content": user_prompt},
        ]

        merged_policy_s3_key = None
        merged_policy_s3_url = None
        try:
            bucket_name = os.getenv("S3_BUCKET_NAME")
            if bucket_name and merged_policy:
                merged_snapshot = {
                    "generated_at": datetime.utcnow().isoformat(),
                    "patient_id": patient_id,
                    "merged_policy_with_phenotype": merged_policy,
                    "clinical_observations_by_source": (
                        clinical_observations_transformed or clinical_observations
                    ),
                    "allowed_actions_snapshot": allowed_actions_summary,
                }
                s3_key = (
                    f"patients/{patient_id}/computed/merged_policy_with_phenotype.json"
                )
                S3_CLIENT.put_object(
                    Bucket=bucket_name,
                    Key=s3_key,
                    Body=json.dumps(merged_snapshot, indent=2, sort_keys=True),
                    ContentType="application/json",
                    CacheControl="no-cache",
                )
                merged_policy_s3_key = s3_key
                merged_policy_s3_url = (
                    f"https://{bucket_name}.s3.{_AWS_REGION}.amazonaws.com/{s3_key}"
                )
        except Exception as exc:  # pragma: no cover
            logger.info("Optional upload of merged policy snapshot failed: %s", exc)

        result = query_bedrock_claude_enhanced(
            bedrock_messages, max_tokens=800, temperature=0.2, patient_id=patient_id
        )
        if result.get("success"):
            action_json = None
            try:
                match = re.search(
                    r"ACTION_JSON:\s*(\{[\s\S]*?\}|null)",
                    result.get("response", ""),
                    re.IGNORECASE,
                )
                if match:
                    payload = match.group(1).strip()
                    if payload.lower() != "null":
                        action_json = json.loads(payload)
            except Exception:  # pragma: no cover
                action_json = None

            return {
                "success": True,
                "patient_id": patient_id,
                "patient_name": name,
                "status_analysis": result.get("response"),
                "manifest_stages": len(manifest_stages) if manifest_stages else 0,
                "patient_data_available": bool(patient_data),
                "policy_available": bool(merged_policy) or bool(policy_json),
                "merged_policy_s3_key": merged_policy_s3_key,
                "merged_policy_s3_url": merged_policy_s3_url,
                "action_json": action_json,
                "timestamp": datetime.now().isoformat(),
            }

        return result
    except Exception as exc:  # pragma: no cover
        logger.error("Error getting patient status from Bedrock: %s", exc)
        return {"success": False, "message": f"Error: {str(exc)}"}



"""
Stage Summary Service

Generic reusable functions for evaluating stage completion status.
Uses a dispatcher pattern to route stage evaluation to appropriate checkers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta

from flask_app.extensions import db
from sqlalchemy import text
from flask_app.services.llm_service import get_llm_service
from flask_app.models import PatientStageSummaryCache
import json


def check_file_exists(patient_id: int, subcategory: str = None, category: str = None) -> Dict[str, Any]:
    """
    Generic file-based completion check.
    
    Args:
        patient_id: Patient ID
        subcategory: File subcategory to match (optional)
        category: File category to match (optional)
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        query = "SELECT f.id, f.upload_date, f.name FROM files f WHERE f.patient_id = :patient_id"
        params = {"patient_id": patient_id}
        
        if subcategory:
            query += " AND LOWER(f.subcategory) = LOWER(:subcategory)"
            params["subcategory"] = subcategory
        if category:
            query += " AND LOWER(f.category) = LOWER(:category)"
            params["category"] = category
            
        query += " ORDER BY f.upload_date DESC LIMIT 1"
        
        result = db.session.execute(text(query), params).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.upload_date.strftime("%Y-%m-%d") if result.upload_date else None,
                "metadata": {"file_id": result.id, "file_name": result.name}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_file_name_like(patient_id: int, subcategory: str, name_keywords: List[str]) -> Dict[str, Any]:
    """
    File name search (HIPAA consent, etc.).
    
    Args:
        patient_id: Patient ID
        subcategory: File subcategory to match
        name_keywords: List of keywords to search in file name
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        conditions = " OR ".join([f"LOWER(f.name) LIKE LOWER(:kw{i})" for i in range(len(name_keywords))])
        params = {"patient_id": patient_id, "subcategory": subcategory}
        for i, kw in enumerate(name_keywords):
            params[f"kw{i}"] = f"%{kw}%"
        
        query = f"""
            SELECT f.id, f.upload_date, f.name
            FROM files f
            WHERE f.patient_id = :patient_id
                AND LOWER(f.subcategory) = LOWER(:subcategory)
                AND ({conditions})
            ORDER BY f.upload_date DESC
            LIMIT 1
        """
        
        result = db.session.execute(text(query), params).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.upload_date.strftime("%Y-%m-%d") if result.upload_date else None,
                "metadata": {"file_id": result.id, "file_name": result.name}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_adminfile_exists(patient_id: int, file_category: str) -> Dict[str, Any]:
    """
    Adminfile-based completion check.
    
    Args:
        patient_id: Patient ID
        file_category: File category to match
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        query = """
            SELECT af.id, af.upload_date, af.name
            FROM adminfiles af
            WHERE af.patient_id = :patient_id
                AND LOWER(af.file_category) = LOWER(:file_category)
            ORDER BY af.upload_date DESC
            LIMIT 1
        """
        
        result = db.session.execute(
            text(query),
            {"patient_id": patient_id, "file_category": file_category}
        ).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.upload_date.strftime("%Y-%m-%d") if result.upload_date else None,
                "metadata": {"file_id": result.id, "file_name": result.name}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_adminfile_name_like(patient_id: int, name_pattern: str) -> Dict[str, Any]:
    """
    Adminfile name/category pattern check (e.g., Level 3 reports).
    
    This function matches files that contain the specific level number (e.g., "level_4")
    in EITHER the file name OR the file_category field.
    It handles variations like "level_4", "level 4", "level4".
    
    Args:
        patient_id: Patient ID
        name_pattern: Pattern to match in file name or category (e.g., 'level_4')
                      Must be in format 'level_X' where X is the level number
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        # Extract the level number from the pattern (e.g., "level_4" -> "4")
        level_num = name_pattern.replace("level_", "").replace("level", "").strip("_").strip()
        
        if not level_num or not level_num.isdigit():
            # Fallback to original pattern if we can't extract level number
            like_pattern = f"%{name_pattern}%"
            query = """
                SELECT af.id, af.upload_date, af.name, af.file_category
                FROM adminfiles af
                WHERE af.patient_id = :patient_id
                    AND (LOWER(af.name) LIKE :name_pattern OR LOWER(af.file_category) LIKE :name_pattern)
                ORDER BY af.upload_date DESC
                LIMIT 1
            """
            params = {"patient_id": patient_id, "name_pattern": like_pattern.lower()}
        else:
            # Build patterns that match this specific level number
            # Match: level_4, level 4, level4, Level 4, LEVEL_4, etc.
            # Check BOTH name AND file_category fields
            like_pattern_underscore = f"%level_{level_num}%"
            like_pattern_space = f"%level {level_num}%"
            like_pattern_no_sep = f"%level{level_num}%"
            
            # Build exclusion list for all other levels (1-7, excluding current level)
            all_levels = ['1', '2', '3', '4', '5', '6', '7']
            if level_num in all_levels:
                all_levels.remove(level_num)
            
            # Build NOT LIKE conditions for other levels (check both name AND file_category)
            not_like_conditions = []
            for other_level in all_levels:
                not_like_conditions.append(f"LOWER(COALESCE(af.name, '')) NOT LIKE '%level_{other_level}%'")
                not_like_conditions.append(f"LOWER(COALESCE(af.name, '')) NOT LIKE '%level {other_level}%'")
                not_like_conditions.append(f"LOWER(COALESCE(af.file_category, '')) NOT LIKE '%level_{other_level}%'")
                not_like_conditions.append(f"LOWER(COALESCE(af.file_category, '')) NOT LIKE '%level {other_level}%'")
            
            not_like_clause = "\n                AND ".join(not_like_conditions) if not_like_conditions else ""
            
            # Check BOTH name AND file_category for the level pattern
            query = f"""
                SELECT af.id, af.upload_date, af.name, af.file_category
                FROM adminfiles af
                WHERE af.patient_id = :patient_id
                    AND (
                        LOWER(COALESCE(af.name, '')) LIKE :pattern_underscore
                        OR LOWER(COALESCE(af.name, '')) LIKE :pattern_space
                        OR LOWER(COALESCE(af.name, '')) LIKE :pattern_no_sep
                        OR LOWER(COALESCE(af.file_category, '')) LIKE :pattern_underscore
                        OR LOWER(COALESCE(af.file_category, '')) LIKE :pattern_space
                        OR LOWER(COALESCE(af.file_category, '')) LIKE :pattern_no_sep
                    )
                    {f'AND {not_like_clause}' if not_like_clause else ''}
                ORDER BY af.upload_date DESC
                LIMIT 1
            """
            
            params = {
                "patient_id": patient_id,
                "pattern_underscore": like_pattern_underscore.lower(),
                "pattern_space": like_pattern_space.lower(),
                "pattern_no_sep": like_pattern_no_sep.lower()
            }
        
        result = db.session.execute(text(query), params).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.upload_date.strftime("%Y-%m-%d") if result.upload_date else None,
                "metadata": {
                    "file_id": result.id, 
                    "file_name": result.name,
                    "file_category": result.file_category
                }
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        # Log the error for debugging
        import logging
        logging.error(f"Error checking adminfile name_like for patient {patient_id}, pattern '{name_pattern}': {str(e)}")
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_consult(patient_id: int, consult_type: str, status: Optional[str] = None) -> Dict[str, Any]:
    """
    Consultation checks.
    
    Args:
        patient_id: Patient ID
        consult_type: Type of consultation (sleep_expert, ep_doctor, etc.)
        status: Optional status filter (e.g., 'completed')
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        query = """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.completed_datetime, pcs.status
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = :patient_id
                AND LOWER(pcs.consult_type) = LOWER(:consult_type)
        """
        params = {"patient_id": patient_id, "consult_type": consult_type}
        
        if status:
            query += " AND LOWER(pcs.status) = LOWER(:status)"
            params["status"] = status
        
        query += " ORDER BY pcs.scheduled_datetime DESC LIMIT 1"
        
        result = db.session.execute(text(query), params).first()
        
        if not result:
            return {"status": "pending", "completed_on": None, "metadata": {}}
        
        # If status filter was provided, check it matches
        if status and result.status and result.status.lower() != status.lower():
            return {"status": "pending", "completed_on": None, "metadata": {}}
        
        # Use completed_datetime if available, otherwise scheduled_datetime
        completed = result.completed_datetime or result.scheduled_datetime
        
        return {
            "status": "completed",
            "completed_on": completed.strftime("%Y-%m-%d") if completed else None,
            "metadata": {"consult_id": result.id, "consult_type": consult_type}
        }
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_quiz_completion(patient_id: int, quiz_types: List[str] = None) -> Dict[str, Any]:
    """
    Quiz completion check from conversion_quiz, vizbriz_quiz tables, and uploaded questionnaire files.
    
    Checks:
    1. conversion_quiz table: Only checks for 'basic_quiz' or 'advanced_quiz' types
    2. vizbriz_quiz table: Any quiz in this table is considered valid
    3. Files table: Checks for files with category='medical' and subcategory='questionnaire'
    
    Returns the most recent completion from any of these sources.
    
    Args:
        patient_id: Patient ID
        quiz_types: List of quiz types to check (default: ['basic_quiz', 'advanced_quiz'])
                   Note: For vizbriz_quiz, any quiz is accepted regardless of quiz_types parameter
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        if quiz_types is None:
            quiz_types = ['basic_quiz', 'advanced_quiz']
        
        quiz_types_str = "', '".join(quiz_types)
        all_results = []
        
        # Check conversion_quiz table (only basic_quiz and advanced_quiz)
        query1 = f"""
            SELECT cq.id, cq.created_at, cq.quiz_type, 'conversion_quiz' as source
            FROM conversion_quiz cq
            WHERE cq.user_id = :patient_id
                AND cq.quiz_type IN ('{quiz_types_str}')
            ORDER BY cq.created_at DESC
            LIMIT 1
        """
        
        result1 = db.session.execute(text(query1), {"patient_id": patient_id}).first()
        if result1:
            # Access Row object by column name or index
            all_results.append({
                "id": result1.id if hasattr(result1, 'id') else result1[0],
                "created_at": result1.created_at if hasattr(result1, 'created_at') else result1[1],
                "quiz_type": result1.quiz_type if hasattr(result1, 'quiz_type') else (result1[2] if len(result1) > 2 else None),
                "source": result1.source if hasattr(result1, 'source') else (result1[3] if len(result1) > 3 else "conversion_quiz")
            })
        
        # Check vizbriz_quiz table (any quiz is valid)
        query2 = """
            SELECT vq.id, vq.created_at, vq.quiz_type, vq.language, vq.risk_band, 'vizbriz_quiz' as source
            FROM vizbriz_quiz vq
            WHERE vq.user_id = :patient_id
            ORDER BY vq.created_at DESC
            LIMIT 1
        """
        
        result2 = db.session.execute(text(query2), {"patient_id": patient_id}).first()
        if result2:
            # Access Row object by column name or index
            all_results.append({
                "id": result2.id if hasattr(result2, 'id') else result2[0],
                "created_at": result2.created_at if hasattr(result2, 'created_at') else result2[1],
                "quiz_type": result2.quiz_type if hasattr(result2, 'quiz_type') else (result2[2] if len(result2) > 2 else None),
                "source": result2.source if hasattr(result2, 'source') else (result2[5] if len(result2) > 5 else "vizbriz_quiz"),
                "language": result2.language if hasattr(result2, 'language') else (result2[3] if len(result2) > 3 else None),
                "risk_band": result2.risk_band if hasattr(result2, 'risk_band') else (result2[4] if len(result2) > 4 else None)
            })
        
        # Check for uploaded questionnaire files (category='medical', subcategory='questionnaire')
        query3 = """
            SELECT f.id, f.upload_date, f.name, 'questionnaire_file' as source
            FROM files f
            WHERE f.patient_id = :patient_id
                AND LOWER(f.category) = 'medical'
                AND LOWER(f.subcategory) = 'questionnaire'
            ORDER BY f.upload_date DESC
            LIMIT 1
        """
        
        result3 = db.session.execute(text(query3), {"patient_id": patient_id}).first()
        if result3:
            # Access Row object by column name or index
            all_results.append({
                "id": result3.id if hasattr(result3, 'id') else result3[0],
                "created_at": result3.upload_date if hasattr(result3, 'upload_date') else result3[1],
                "source": result3.source if hasattr(result3, 'source') else (result3[3] if len(result3) > 3 else "questionnaire_file"),
                "file_name": result3.name if hasattr(result3, 'name') else (result3[2] if len(result3) > 2 else None)
            })
        
        # Return the most recent quiz from any source
        if all_results:
            # Sort by created_at and get the most recent
            most_recent = max(all_results, key=lambda x: x["created_at"])
            
            metadata = {
                "quiz_id": most_recent["id"],
                "source": most_recent["source"]
            }
            
            if most_recent.get("quiz_type"):
                metadata["quiz_type"] = most_recent["quiz_type"]
            if most_recent.get("language"):
                metadata["language"] = most_recent["language"]
            if most_recent.get("risk_band"):
                metadata["risk_band"] = most_recent["risk_band"]
            if most_recent.get("file_name"):
                metadata["file_name"] = most_recent["file_name"]
            
            return {
                "status": "completed",
                "completed_on": most_recent["created_at"].strftime("%Y-%m-%d") if most_recent["created_at"] else None,
                "metadata": metadata
            }
        
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error checking quiz completion for patient {patient_id}: {str(e)}")
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_manifest_stage(patient_id: int, min_stage: int) -> Dict[str, Any]:
    """
    Manifest stage check.
    
    Args:
        patient_id: Patient ID
        min_stage: Minimum stage number required
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        query = """
            SELECT pm.stage, pm.updated_at
            FROM patient_manifest pm
            WHERE pm.patient_id = :patient_id
                AND pm.stage >= :min_stage
            ORDER BY pm.updated_at DESC
            LIMIT 1
        """
        
        result = db.session.execute(
            text(query),
            {"patient_id": patient_id, "min_stage": min_stage}
        ).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.updated_at.strftime("%Y-%m-%d") if result.updated_at else None,
                "metadata": {"stage": result.stage}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_device_order(patient_id: int, device_type: str = None) -> Dict[str, Any]:
    """
    Check if a device was ordered for the patient.
    
    Args:
        patient_id: Patient ID
        device_type: Optional device type filter (e.g., 'oral_appliance')
    
    Returns:
        Dict with status and completed_on date (order_date)
    """
    try:
        if device_type:
            query = """
                SELECT pdo.id, pdo.order_date, pdo.device_type
                FROM patient_device_order pdo
                WHERE pdo.patient_id = :patient_id
                    AND LOWER(pdo.device_type) = LOWER(:device_type)
                ORDER BY pdo.order_date DESC
                LIMIT 1
            """
            params = {"patient_id": patient_id, "device_type": device_type}
        else:
            query = """
                SELECT pdo.id, pdo.order_date, pdo.device_type
                FROM patient_device_order pdo
                WHERE pdo.patient_id = :patient_id
                ORDER BY pdo.order_date DESC
                LIMIT 1
            """
            params = {"patient_id": patient_id}
        
        result = db.session.execute(text(query), params).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.order_date.strftime("%Y-%m-%d") if result.order_date else None,
                "metadata": {"device_order_id": result.id, "device_type": result.device_type}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_device_delivery(patient_id: int, device_type: str = None) -> Dict[str, Any]:
    """
    Check if a device was delivered (has arrival_date).
    
    Args:
        patient_id: Patient ID
        device_type: Optional device type filter (e.g., 'oral_appliance')
    
    Returns:
        Dict with status and completed_on date (arrival_date)
    """
    try:
        if device_type:
            query = """
                SELECT pdo.id, pdo.arrival_date, pdo.device_type
                FROM patient_device_order pdo
                WHERE pdo.patient_id = :patient_id
                    AND pdo.arrival_date IS NOT NULL
                    AND LOWER(pdo.device_type) = LOWER(:device_type)
                ORDER BY pdo.arrival_date DESC
                LIMIT 1
            """
            params = {"patient_id": patient_id, "device_type": device_type}
        else:
            query = """
                SELECT pdo.id, pdo.arrival_date, pdo.device_type
                FROM patient_device_order pdo
                WHERE pdo.patient_id = :patient_id
                    AND pdo.arrival_date IS NOT NULL
                ORDER BY pdo.arrival_date DESC
                LIMIT 1
            """
            params = {"patient_id": patient_id}
        
        result = db.session.execute(text(query), params).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.arrival_date.strftime("%Y-%m-%d") if result.arrival_date else None,
                "metadata": {"device_order_id": result.id, "device_type": result.device_type}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def check_device_status(patient_id: int, device_type: str, status: str) -> Dict[str, Any]:
    """
    Device status checks.
    
    Args:
        patient_id: Patient ID
        device_type: Type of device
        status: Device status to check
    
    Returns:
        Dict with status and completed_on date
    """
    try:
        query = """
            SELECT pdo.id, pdo.arrival_date
            FROM patient_device_order pdo
            WHERE pdo.patient_id = :patient_id
                AND LOWER(pdo.device_type) = LOWER(:device_type)
                AND LOWER(pdo.status) = LOWER(:status)
            ORDER BY pdo.arrival_date DESC
            LIMIT 1
        """
        
        result = db.session.execute(
            text(query),
            {"patient_id": patient_id, "device_type": device_type, "status": status}
        ).first()
        
        if result:
            return {
                "status": "completed",
                "completed_on": result.arrival_date.strftime("%Y-%m-%d") if result.arrival_date else None,
                "metadata": {"device_order_id": result.id}
            }
        return {"status": "pending", "completed_on": None, "metadata": {}}
    except Exception as e:
        return {"status": "pending", "completed_on": None, "metadata": {"error": str(e)}}


def evaluate_stage_completion(
    patient_id: int, 
    stage: Dict[str, Any],
    all_stages_status: Dict[str, Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Universal dispatcher function for stage completion evaluation.
    
    Args:
        patient_id: Patient ID
        stage: Stage manifest entry with completion_type and completion_args
        all_stages_status: Optional dict of all stages' completion status for skip_if checks
    
    Returns:
        Dict with status, completed_on date, and skip_reason if applicable
    """
    # Check skip_if conditions first
    skip_if = stage.get("skip_if", [])
    if skip_if and all_stages_status:
        for skip_stage_key in skip_if:
            skip_stage_status = all_stages_status.get(skip_stage_key, {})
            if skip_stage_status.get("status") == "completed":
                return {
                    "status": "skipped",
                    "completed_on": None,
                    "skip_reason": f"Skipped because {skip_stage_key} is already completed",
                    "skipped_by": skip_stage_key,
                    "metadata": {"optional": stage.get("optional", False)}
                }
    
    completion_type = stage.get("completion_type")
    completion_args = stage.get("completion_args", {})
    
    if completion_type == "file":
        return check_file_exists(patient_id, **completion_args)
    elif completion_type == "file_name_like":
        return check_file_name_like(patient_id, **completion_args)
    elif completion_type == "adminfile":
        return check_adminfile_exists(patient_id, **completion_args)
    elif completion_type == "adminfile_name_like":
        return check_adminfile_name_like(patient_id, **completion_args)
    elif completion_type == "consult":
        return check_consult(patient_id, **completion_args)
    elif completion_type == "quiz":
        return check_quiz_completion(patient_id, **completion_args)
    elif completion_type == "manifest_stage":
        return check_manifest_stage(patient_id, **completion_args)
    elif completion_type == "device_order":
        return check_device_order(patient_id, **completion_args)
    elif completion_type == "device_delivery":
        return check_device_delivery(patient_id, **completion_args)
    elif completion_type == "device_status":
        return check_device_status(patient_id, **completion_args)
    
    # Default fallback
    return {"status": "pending", "completed_on": None, "metadata": {}}


def generate_stage_ai_guidance(
    patient_id: int,
    stage: Dict[str, Any],
    completion_status: Dict[str, Any],
    all_stages_status: Dict[str, Dict[str, Any]] = None,
    all_stages_manifest: List[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Generate AI guidance/comment for a stage based on manifest and completion status.
    
    Args:
        patient_id: Patient ID
        stage: Stage manifest entry
        completion_status: Result from evaluate_stage_completion
        all_stages_status: Optional dict of all stages' completion status for context
        all_stages_manifest: Optional list of all stage manifest entries for full context
    
    Returns:
        AI-generated guidance string or None if generation fails
    """
    try:
        llm_service = get_llm_service()
        
        # Build context for AI
        stage_key = stage.get("key")
        stage_title = stage.get("title")
        stage_description = stage.get("description")
        stage_status = completion_status.get("status", "pending")
        completed_on = completion_status.get("completed_on")
        prerequisites = stage.get("prerequisites", [])
        llm_context_tag = stage.get("llm_context_tag", "")
        guidance = stage.get("guidance", "")
        
        # Check prerequisite completion if provided
        prerequisites_status = {}
        if all_stages_status and prerequisites:
            for prereq_key in prerequisites:
                prereq_status = all_stages_status.get(prereq_key, {})
                prerequisites_status[prereq_key] = prereq_status.get("status", "pending")
        
        # Build comprehensive workflow context if available
        workflow_summary = ""
        if all_stages_manifest and all_stages_status:
            completed_stages = []
            pending_stages = []
            for manifest_entry in all_stages_manifest:
                entry_key = manifest_entry.get("key")
                entry_status = all_stages_status.get(entry_key, {}).get("status", "pending")
                entry_title = manifest_entry.get("title", "")
                if entry_status == "completed":
                    completed_stages.append(entry_title)
                else:
                    pending_stages.append(entry_title)
            
            workflow_summary = f"""
Completed Stages: {', '.join(completed_stages) if completed_stages else 'None'}
Pending Stages: {', '.join(pending_stages[:5]) if pending_stages else 'None'}{'...' if len(pending_stages) > 5 else ''}
"""
        
        # Build comprehensive prompt
        system_prompt = """You are an AI workflow assistant reviewing the OSA (Obstructive Sleep Apnea) treatment progress for a patient.

Your role is to:
1. Analyze the complete treatment workflow manifest
2. Identify which stages are complete and which are missing
3. Provide per-stage comments that are specific and actionable
4. Consider prerequisite dependencies and workflow progression
5. Use professional medical terminology appropriately

For each stage, provide:
- If completed: Brief acknowledgment and what this enables next
- If pending: What's needed, why it's important, and what blocks it (if prerequisites are missing)

Keep comments concise (1-2 sentences max per stage)."""
        
        # Build workflow context string separately to avoid backslash in f-string
        workflow_context = workflow_summary if workflow_summary else "\n(Full workflow context not available)"
        prerequisites_str = ', '.join(prerequisites) if prerequisites else "None"
        prerequisites_status_str = json.dumps(prerequisites_status) if prerequisites_status else "N/A"
        completed_date_str = completed_on if completed_on else "Not completed"
        
        user_prompt = f"""OSA Treatment Workflow Analysis for Patient {patient_id}

CURRENT STAGE ANALYSIS:
Stage: {stage_title}
Description: {stage_description}
Status: {stage_status}
Completed On: {completed_date_str}
Prerequisites: {prerequisites_str}
Prerequisites Status: {prerequisites_status_str}
Static Guidance: {guidance}
Context Category: {llm_context_tag}

FULL WORKFLOW CONTEXT:{workflow_context}

Provide a brief AI comment for the current stage ({stage_title}). 
Focus on what's needed if pending, or acknowledge completion and next steps if completed.
Consider the overall workflow progression and prerequisite dependencies."""
        
        messages = [{
            "role": "user",
            "content": user_prompt
        }]
        
        # Use the internal method (it's the standard way to make LLM calls in this service)
        result = llm_service._make_bedrock_call(
            messages=messages,
            max_tokens=200,
            temperature=0.3,
            system=system_prompt,
            patient_id=patient_id,
            endpoint="stage_summary_ai_guidance"
        )
        
        if result.get("success"):
            return result.get("response", "").strip()
        else:
            # Fallback to static guidance if AI fails
            return None
            
    except Exception as e:
        # Log error but don't fail the request
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"AI guidance generation failed for stage {stage.get('key')}: {e}")
        return None


def get_cached_ai_summary(patient_id: int, all_stages_status: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Retrieve cached AI summary if available and still valid.
    
    Returns:
        Dict with 'overall_summary', 'stage_comments', 'metadata' if cache is valid, None otherwise
    """
    try:
        cache = PatientStageSummaryCache.query.filter_by(patient_id=patient_id).first()
        if not cache:
            return None
        
        # Check if cache is expired
        if cache.expires_at and cache.expires_at < datetime.utcnow():
            cache.is_valid = False
            db.session.commit()
            return None
        
        # Check if cache is stale (stages have changed)
        if cache.is_stale(all_stages_status):
            # Invalidate cache immediately
            cache.is_valid = False
            db.session.commit()
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Cache invalidated for patient {patient_id} - stages have changed")
            return None
        
        if not cache.is_valid:
            return None
        
        # Return cached data in the same format as new generation
        cached_metadata = cache.overall_summary_metadata or {}
        return {
            "overall_summary": {
                "structured": cached_metadata.get("structured"),
                "raw_text": cache.overall_summary or "",
                "generated_at": cached_metadata.get("generated_at") or (cache.updated_at.strftime("%b %d %Y %H:%M UTC") if cache.updated_at else None),
                "model": cached_metadata.get("model") or "Claude 4 Sonnet"
            },
            "stage_comments": cache.stage_ai_comments or {},
            "cached": True,
            "cached_at": cache.updated_at.isoformat() if cache.updated_at else None
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error retrieving cached summary for patient {patient_id}: {e}")
        return None


def save_cached_ai_summary(
    patient_id: int,
    overall_summary_data: Dict[str, Any],
    stage_comments: Dict[str, Optional[str]],
    all_stages_status: Dict[str, Dict[str, Any]]
) -> None:
    """
    Save AI summary to cache for future retrieval.
    """
    try:
        cache = PatientStageSummaryCache.query.filter_by(patient_id=patient_id).first()
        
        if not cache:
            cache = PatientStageSummaryCache(patient_id=patient_id)
            db.session.add(cache)
        
        # Store summary data (structured format)
        if isinstance(overall_summary_data, dict):
            # New structured format
            cache.overall_summary = overall_summary_data.get("raw_text", "")
            cache.overall_summary_metadata = overall_summary_data  # Stores structured, raw_text, generated_at, model
        else:
            # Legacy string format - convert to structured
            cache.overall_summary = overall_summary_data
            cache.overall_summary_metadata = {
                "structured": None,
                "raw_text": overall_summary_data,
                "generated_at": datetime.utcnow().strftime("%b %d %Y %H:%M UTC"),
                "model": "Claude 4 Sonnet"
            }
        
        cache.stage_ai_comments = stage_comments
        cache.stages_snapshot = all_stages_status
        cache.is_valid = True
        cache.updated_at = datetime.utcnow()
        
        # Set expiration to 24 hours (optional)
        cache.expires_at = datetime.utcnow() + timedelta(hours=24)
        
        db.session.commit()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error saving cached summary for patient {patient_id}: {e}")
        db.session.rollback()


def _parse_text_summary_to_json(text: str, completed: int, total: int, percent: int) -> Dict[str, Any]:
    """
    Fallback parser: Convert text format summary to structured JSON.
    Used when AI doesn't return JSON.
    """
    import re
    
    result = {
        "overall_summary": {
            "status": "Treatment in Progress",
            "progress": f"{completed} / {total} stages complete ({percent}%)",
            "phase": "Treatment Phase",
            "next_action": "Continue treatment workflow"
        },
        "critical_path_analysis": {
            "immediate_priority": "Review current stage and proceed to next step",
            "workflow_dependencies": "Treatment progression depends on completing current stage"
        },
        "recommendations": []
    }
    
    lines = text.split('\n')
    current_section = None
    current_items = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Detect sections
        if re.match(r'^Status:', line, re.IGNORECASE):
            result["overall_summary"]["status"] = re.sub(r'^Status:\s*', '', line, flags=re.IGNORECASE).strip()
        elif re.match(r'^Progress:', line, re.IGNORECASE):
            result["overall_summary"]["progress"] = re.sub(r'^Progress:\s*', '', line, flags=re.IGNORECASE).strip()
        elif re.match(r'^OVERALL SUMMARY|^SUMMARY', line, re.IGNORECASE):
            current_section = "overall"
        elif re.match(r'^CRITICAL PATH|^CRITICAL PATH ANALYSIS', line, re.IGNORECASE):
            current_section = "critical"
        elif re.match(r'^RECOMMENDATIONS?:', line, re.IGNORECASE):
            current_section = "recommendations"
            current_items = []
        elif re.match(r'^IMMEDIATE PRIORITY:', line, re.IGNORECASE):
            result["critical_path_analysis"]["immediate_priority"] = re.sub(r'^IMMEDIATE PRIORITY:\s*', '', line, flags=re.IGNORECASE).strip()
        elif re.match(r'^WORKFLOW DEPENDENCIES:', line, re.IGNORECASE):
            result["critical_path_analysis"]["workflow_dependencies"] = re.sub(r'^WORKFLOW DEPENDENCIES:\s*', '', line, flags=re.IGNORECASE).strip()
        elif current_section == "recommendations":
            # Remove bullet points
            clean_line = re.sub(r'^[-•*]\s+', '', line)
            if clean_line:
                current_items.append(clean_line)
        else:
            # Default to recommendation if no section detected
            if not result["recommendations"]:
                result["recommendations"].append(line)
    
    # Assign collected recommendations
    if current_items:
        result["recommendations"] = current_items
    
    return result


def invalidate_cache(patient_id: int) -> None:
    """Invalidate cache for a patient (e.g., when stages are updated)"""
    try:
        cache = PatientStageSummaryCache.query.filter_by(patient_id=patient_id).first()
        if cache:
            cache.is_valid = False
            db.session.commit()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error invalidating cache for patient {patient_id}: {e}")


def generate_all_stages_ai_guidance_batch(
    patient_id: int,
    all_stages_manifest: List[Dict[str, Any]],
    all_stages_status: Dict[str, Dict[str, Any]]
) -> Dict[str, Optional[str]]:
    """
    Generate AI guidance for ALL stages in a single LLM call (more efficient).
    
    Args:
        patient_id: Patient ID
        all_stages_manifest: List of all stage manifest entries
        all_stages_status: Dict of all stages' completion status
    
    Returns:
        Dict mapping stage_key to AI comment string (or None if generation fails)
    """
    try:
        llm_service = get_llm_service()
        
        # Build comprehensive workflow summary
        completed_stages = []
        pending_stages = []
        blocked_stages = []
        stages_detail = []
        
        for manifest_entry in all_stages_manifest:
            stage_key = manifest_entry.get("key")
            stage_title = manifest_entry.get("title", "")
            stage_description = manifest_entry.get("description", "")
            stage_status = all_stages_status.get(stage_key, {}).get("status", "pending")
            completed_on = all_stages_status.get(stage_key, {}).get("completed_on")
            prerequisites = manifest_entry.get("prerequisites", [])
            guidance = manifest_entry.get("guidance", "")
            llm_context_tag = manifest_entry.get("llm_context_tag", "")
            
            # Check prerequisite completion
            prerequisites_status = {}
            if prerequisites:
                for prereq_key in prerequisites:
                    prereq_status = all_stages_status.get(prereq_key, {}).get("status", "pending")
                    prerequisites_status[prereq_key] = prereq_status
            
            # Check if blocked (skipped counts as satisfied)
            is_blocked = False
            if prerequisites:
                for prereq_key in prerequisites:
                    prereq_status = all_stages_status.get(prereq_key, {}).get("status", "pending")
                    if prereq_status not in ["completed", "skipped"]:
                        is_blocked = True
                        break
            
            if stage_status == "completed":
                completed_stages.append(stage_title)
            elif stage_status == "skipped":
                # Skipped stages count as done for workflow progression
                completed_stages.append(f"{stage_title} (skipped)")
            elif is_blocked:
                blocked_stages.append(stage_title)
            else:
                pending_stages.append(stage_title)
            
            stages_detail.append({
                "key": stage_key,
                "title": stage_title,
                "description": stage_description,
                "status": stage_status,
                "completed_on": completed_on if completed_on else "Not completed",
                "prerequisites": prerequisites,
                "prerequisites_status": prerequisites_status,
                "guidance": guidance,
                "context_tag": llm_context_tag,
                "is_blocked": is_blocked
            })
        
        # Build comprehensive prompt
        system_prompt = """You are an AI workflow assistant reviewing the OSA (Obstructive Sleep Apnea) treatment progress for a patient.

Your role is to:
1. Analyze the complete treatment workflow manifest
2. Identify which stages are complete, skipped, or pending
3. Provide per-stage comments that are specific and actionable
4. Consider prerequisite dependencies and workflow progression
5. Use professional medical terminology appropriately

IMPORTANT: Stages can have three statuses:
- "completed": Stage was actually completed
- "skipped": Stage was auto-skipped because a later milestone (like Level 4 Report) was already achieved
- "pending": Stage still needs to be done

For each stage, provide:
- If completed: Brief acknowledgment and what this enables next
- If skipped: Acknowledge it was auto-skipped (no action needed)
- If pending: What's needed, why it's important, and what blocks it (if prerequisites are missing)

Keep comments concise (1-2 sentences max per stage).

Return your response as a JSON object where each key is the stage key and the value is the AI comment for that stage.
Example format:
{
  "quiz_completion": "Auto-skipped - Level 4 Report indicates patient data was already collected.",
  "osa_report_ready": "Level 4 Report completed. Ready for oral appliance ordering.",
  "order_oral_appliance": "Next step: Order the oral appliance to proceed with treatment.",
  ...
}"""
        
        stages_json = json.dumps(stages_detail, indent=2)
        user_prompt = f"""OSA Treatment Workflow Analysis for Patient {patient_id}

WORKFLOW OVERVIEW:
Completed Stages ({len(completed_stages)}): {', '.join(completed_stages) if completed_stages else 'None'}
Pending Stages ({len(pending_stages)}): {', '.join(pending_stages[:10]) if pending_stages else 'None'}{'...' if len(pending_stages) > 10 else ''}
Blocked Stages ({len(blocked_stages)}): {', '.join(blocked_stages) if blocked_stages else 'None'}

DETAILED STAGE INFORMATION:
{stages_json}

Provide AI comments for ALL stages. Return a JSON object where each key is the stage key (e.g., "quiz_completion") and the value is the AI comment for that stage.
Focus on what's needed if pending, or acknowledge completion and next steps if completed.
Consider the overall workflow progression and prerequisite dependencies."""
        
        messages = [{
            "role": "user",
            "content": user_prompt
        }]
        
        result = llm_service._make_bedrock_call(
            messages=messages,
            max_tokens=2000,  # More tokens for all stages
            temperature=0.3,
            system=system_prompt,
            patient_id=patient_id,
            endpoint="stage_summary_ai_guidance_batch"
        )
        
        if result.get("success"):
            response_text = result.get("response", "").strip()
            try:
                # Try to parse JSON response
                comments_dict = json.loads(response_text)
                return comments_dict
            except json.JSONDecodeError:
                # If not JSON, try to extract JSON from markdown code blocks
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if json_match:
                    comments_dict = json.loads(json_match.group(1))
                    return comments_dict
                else:
                    # Fallback: return empty dict
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Could not parse AI response as JSON: {response_text[:200]}")
                    return {}
        else:
            return {}
            
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Batch AI guidance generation failed for patient {patient_id}: {e}")
        return {}


def generate_overall_workflow_summary(
    patient_id: int,
    all_stages_manifest: List[Dict[str, Any]],
    all_stages_status: Dict[str, Dict[str, Any]]
) -> Optional[str]:
    """
    Generate an overall workflow summary analyzing the complete treatment progress.
    
    Args:
        patient_id: Patient ID
        all_stages_manifest: List of all stage manifest entries
        all_stages_status: Dict of all stages' completion status
    
    Returns:
        AI-generated overall summary string or None if generation fails
    """
    try:
        llm_service = get_llm_service()
        
        # Build workflow summary
        completed_stages = []
        skipped_stages = []
        pending_stages = []
        blocked_stages = []
        
        for manifest_entry in all_stages_manifest:
            stage_key = manifest_entry.get("key")
            stage_title = manifest_entry.get("title", "")
            stage_status = all_stages_status.get(stage_key, {}).get("status", "pending")
            prerequisites = manifest_entry.get("prerequisites", [])
            
            # Check if blocked by incomplete prerequisites
            # Note: "skipped" status counts as satisfied for prerequisite purposes
            is_blocked = False
            if prerequisites:
                for prereq_key in prerequisites:
                    prereq_status = all_stages_status.get(prereq_key, {}).get("status", "pending")
                    if prereq_status not in ["completed", "skipped"]:
                        is_blocked = True
                        break
            
            if stage_status == "completed":
                completed_stages.append(stage_title)
            elif stage_status == "skipped":
                # Skipped stages count as "done" for workflow progression
                skipped_stages.append(stage_title)
            elif is_blocked:
                blocked_stages.append(f"{stage_title} (blocked by incomplete prerequisites)")
            else:
                pending_stages.append(stage_title)
        
        # Count completed + skipped as "done" for progress purposes
        done_count = len(completed_stages) + len(skipped_stages)
        total_count = len(all_stages_manifest)
        completion_percent = round((done_count / total_count * 100)) if total_count > 0 else 0
        
        # Combine completed and skipped for display (both are "done")
        done_stages = completed_stages + [f"{s} (auto-skipped)" for s in skipped_stages]
        
        # Build prompt - Request structured JSON output (simplified to 3 sections only)
        system_prompt = f"""You are an AI workflow assistant reviewing the OSA (Obstructive Sleep Apnea) treatment progress for a patient.

Generate a concise structured treatment summary as a JSON object with ONLY these 3 sections:

{{
  "overall_summary": {{
    "status": "Brief status description — e.g., 'Early Stage', 'Mid-Treatment', 'Near Completion' — MUST include '({done_count} / {total_count} stages)'",
    "progress": "{done_count} / {total_count} stages done ({completion_percent}%)",
    "phase": "Current treatment phase name",
    "next_action": "Single most important next action required"
  }},
  "critical_path_analysis": {{
    "immediate_priority": "Single most urgent action that unblocks the workflow",
    "workflow_dependencies": "Brief explanation of what's blocking progress and why"
  }},
  "recommendations": [
    "First recommendation (most urgent)",
    "Second recommendation", 
    "Third recommendation"
  ]
}}

CRITICAL INSTRUCTIONS:
- The progress field MUST be exactly: "{done_count} / {total_count} stages done ({completion_percent}%)"
- The status field MUST include "({done_count} / {total_count} stages)" in the text
- DO NOT change these numbers - they are pre-calculated and include auto-skipped stages

Return ONLY the JSON object. No markdown, no extra text."""
        
        user_prompt = f"""OSA Treatment Workflow Summary for Patient {patient_id}

PROGRESS: {done_count} / {total_count} stages done ({completion_percent}%)

DONE STAGES ({done_count}): {', '.join(done_stages[:5]) if done_stages else 'None'}{'...' if len(done_stages) > 5 else ''}
PENDING STAGES ({len(pending_stages)}): {', '.join(pending_stages[:3]) if pending_stages else 'None'}
BLOCKED STAGES ({len(blocked_stages)}): {', '.join(blocked_stages[:3]) if blocked_stages else 'None'}

IMPORTANT CONTEXT:
- "auto-skipped" stages are stages that were bypassed because a later report/milestone was already completed
- For example, if a Level 4 Report exists, earlier stages like Quiz Completion are auto-skipped
- Treat auto-skipped stages as DONE - they don't need action

Generate ONLY a JSON object with these 3 sections:
1. overall_summary (status, progress, phase, next_action)
2. critical_path_analysis (immediate_priority, workflow_dependencies)
3. recommendations (array of 2-3 action items)

Focus on PENDING stages that need action. DO NOT recommend actions for completed or skipped stages."""
        
        messages = [{
            "role": "user",
            "content": user_prompt
        }]
        
        result = llm_service._make_bedrock_call(
            messages=messages,
            max_tokens=800,  # Increased for JSON format
            temperature=0.3,
            system=system_prompt,
            patient_id=patient_id,
            endpoint="stage_summary_overall_summary"
        )
        
        if result.get("success"):
            response_text = result.get("response", "").strip()
            
            # Try to parse as JSON
            structured_data = None
            try:
                # Remove markdown code blocks if present
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group(1)
                
                structured_data = json.loads(response_text)
            except json.JSONDecodeError:
                # Fallback: parse text format and convert to structured
                structured_data = _parse_text_summary_to_json(response_text, done_count, total_count, completion_percent)
            
            # FORCE correct progress values - don't trust LLM to get these right
            if structured_data and isinstance(structured_data.get("overall_summary"), dict):
                # Override progress with correct calculated value
                structured_data["overall_summary"]["progress"] = f"{done_count} / {total_count} stages done ({completion_percent}%)"
                
                # Fix status to include correct counts (replace any wrong counts)
                status = structured_data["overall_summary"].get("status", "")
                # Remove ALL parenthetical expressions containing numbers and "stage" 
                # This catches: (1/18), (1 / 18), (1 / 18 stages), (12/18 stages), etc.
                status = re.sub(r'\s*\([^)]*\d+[^)]*(?:stage|stages)[^)]*\)', '', status, flags=re.IGNORECASE)
                status = re.sub(r'\s*\(\d+\s*/\s*\d+[^)]*\)', '', status)  # Catch any remaining X/Y patterns
                status = re.sub(r'\s+', ' ', status).strip()  # Clean up extra spaces
                # Add correct count at the end
                structured_data["overall_summary"]["status"] = f"{status} ({done_count} / {total_count} stages)"
            
            # Add metadata
            from datetime import datetime
            timestamp = datetime.utcnow().strftime("%b %d %Y %H:%M UTC")
            
            summary_with_metadata = {
                "structured": structured_data,
                "raw_text": response_text,  # Keep raw for fallback
                "generated_at": timestamp,
                "model": "Claude 4 Sonnet"
            }
            return summary_with_metadata
        else:
            return None
            
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"AI overall summary generation failed for patient {patient_id}: {e}")
        return None

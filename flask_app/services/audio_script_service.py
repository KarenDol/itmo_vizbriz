#!/usr/bin/env python3
"""
Audio Script Generation Service
2-pass approach: Extract facts, then generate dialogue
"""

import json
import logging
import re
from typing import Dict, Any, Optional
from flask_app.services.bedrock_service import BedrockService

logger = logging.getLogger(__name__)


def clean_json_text(text: str) -> str:
    """
    Clean JSON text by removing control characters and extracting JSON from markdown
    
    Args:
        text: Raw text that may contain JSON
        
    Returns:
        Cleaned JSON string
    """
    if not text:
        return ""
    
    # First, try to extract JSON from markdown code blocks
    json_text = text.strip()
    
    # Remove markdown code blocks
    if "```json" in json_text:
        json_text = json_text.split("```json")[1].split("```")[0].strip()
    elif "```" in json_text:
        # Find first ``` and last ```
        parts = json_text.split("```")
        if len(parts) >= 3:
            # Take the middle part (between first and last ```)
            json_text = "```".join(parts[1:-1]).strip()
    
    # More aggressive control character removal
    # JSON only allows: space (32+), \n (10), \r (13), \t (9)
    # Remove ALL other control characters (0x00-0x1F except 9, 10, 13)
    cleaned = ""
    for char in json_text:
        code = ord(char)
        # Keep only: printable ASCII (32-126), newline (10), tab (9), carriage return (13)
        # Also allow extended ASCII and Unicode for content, but escape properly
        if code == 9 or code == 10 or code == 13:  # \t, \n, \r
            cleaned += char
        elif code >= 32:  # All printable characters
            # For string content, we need to be careful with quotes and backslashes
            # But we'll let json.loads handle proper escaping
            cleaned += char
        # All other control chars (0x00-0x1F except 9, 10, 13) are removed
    
    # Remove any remaining problematic characters that might cause issues
    # Remove zero-width spaces and other invisible Unicode characters
    cleaned = re.sub(r'[\u200B-\u200D\uFEFF]', '', cleaned)  # Zero-width spaces
    
    # Remove trailing commas before closing braces/brackets (common JSON error)
    cleaned = re.sub(r',\s*}', '}', cleaned)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    
    # Try to find JSON object boundaries if text is mixed
    # Look for first { and last }
    first_brace = cleaned.find('{')
    last_brace = cleaned.rfind('}')
    if first_brace >= 0 and last_brace > first_brace:
        cleaned = cleaned[first_brace:last_brace + 1]
    
    # Final cleanup: remove any leading/trailing whitespace and ensure it starts with {
    cleaned = cleaned.strip()
    if not cleaned.startswith('{'):
        first_brace = cleaned.find('{')
        if first_brace >= 0:
            cleaned = cleaned[first_brace:]
    
    return cleaned


class AudioScriptService:
    """Service for generating patient-friendly audio scripts from sleep study reports"""
    
    def __init__(self):
        self.bedrock = BedrockService()
    
    def extract_facts(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Pass A: Extract patient-safe facts from sleep study report
        
        Args:
            report_data: Sleep study report JSON or normalized data
            
        Returns:
            Dict with extracted facts in structured format
        """
        try:
            # Convert report_data to JSON string for the prompt
            report_json = json.dumps(report_data, indent=2)
            
            system_prompt = """You are a medical data extraction assistant. Extract only patient-safe, relevant facts from sleep study reports.
Output a clean JSON structure with no PII (names, addresses, phone numbers, identifiers).
Only include metrics and findings that are present in the report - do not invent anything."""

            user_prompt = f"""Extract key facts from this sleep study report:

{report_json}

Output a JSON object with this exact structure:
{{
  "key_findings": ["finding1", "finding2", ...],
  "metrics": {{
    "AHI": <number or null>,
    "ODI": <number or null>,
    "SpO2_nadir": <number or null>,
    "SpO2_mean": <number or null>
  }},
  "symptoms": ["symptom1", "symptom2", ...],
  "risk_flags": ["flag1", "flag2", ...],
  "recommended_next_steps": ["step1", "step2", ...],
  "what_this_means_in_plain_english": ["explanation1", "explanation2", ...]
}}

Rules:
- Only include metrics that are actually in the report
- Use null for missing metrics
- Keep explanations simple and patient-friendly
- No medical jargon unless necessary
- No PII or identifying information"""

            messages = [
                {"role": "user", "content": system_prompt + "\n\n" + user_prompt}
            ]
            
            result = self.bedrock.invoke_model(
                messages=messages,
                model="claude_35_sonnet_v2",
                max_tokens=2000,
                temperature=0.1,
                endpoint="audio_script_extract_facts"
            )
            
            if not result.get("success"):
                logger.error(f"Failed to extract facts: {result.get('error')}")
                return {"error": result.get("error", "Unknown error")}
            
            response_text = result.get("response", "")
            
            # Clean and extract JSON
            json_text = clean_json_text(response_text)
            
            # Helper function to fix common JSON issues
            def fix_json_common_issues(text):
                """Fix common JSON formatting issues"""
                fixed = text
                # Remove trailing commas
                fixed = re.sub(r',\s*}', '}', fixed)
                fixed = re.sub(r',\s*]', ']', fixed)
                # Fix unquoted keys (basic attempt - be careful not to break string values)
                # Only fix keys that are clearly at the start of a line or after {
                fixed = re.sub(r'(\{|\n)\s*(\w+):', r'\1"\2":', fixed)
                # Remove comments
                fixed = re.sub(r'//.*?$', '', fixed, flags=re.MULTILINE)
                fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)
                return fixed
            
            # Try multiple parsing strategies
            parsing_strategies = [
                ("standard", lambda: json.loads(json_text)),
                ("extract_braces", lambda: json.loads(json_text[json_text.find('{'):json_text.rfind('}')+1])),
                ("remove_escaped_newlines", lambda: json.loads(re.sub(r'\\n', ' ', json_text))),
                ("fix_common_issues", lambda: json.loads(fix_json_common_issues(json_text))),
                ("remove_all_newlines", lambda: json.loads(re.sub(r'\n', ' ', json_text))),
            ]
            
            for strategy_name, parse_func in parsing_strategies:
                try:
                    facts = parse_func()
                    logger.info(f"Successfully extracted facts using {strategy_name} strategy")
                    return {"success": True, "facts": facts}
                except (json.JSONDecodeError, ValueError) as e:
                    logger.debug(f"Strategy {strategy_name} failed: {e}")
                    continue
                except Exception as e:
                    logger.debug(f"Strategy {strategy_name} failed with unexpected error: {e}")
                    continue
            
            # If all strategies fail, try brace counting
            logger.error(f"All JSON parsing strategies failed for facts extraction")
            logger.error(f"Cleaned JSON text (first 2000 chars): {json_text[:2000]}")
            logger.error(f"Cleaned JSON text (last 500 chars): {json_text[-500:]}")
            
            try:
                # Find the JSON object more carefully using brace counting
                start_idx = json_text.find('{')
                if start_idx >= 0:
                    brace_count = 0
                    end_idx = start_idx
                    for i in range(start_idx, len(json_text)):
                        if json_text[i] == '{':
                            brace_count += 1
                        elif json_text[i] == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    
                    if end_idx > start_idx:
                        json_snippet = json_text[start_idx:end_idx]
                        # Try with fixes
                        for fix_strategy in [json_snippet, fix_json_common_issues(json_snippet)]:
                            try:
                                facts = json.loads(fix_strategy)
                                logger.info("Successfully parsed JSON using brace counting with fixes")
                                return {"success": True, "facts": facts}
                            except:
                                continue
            except Exception as e:
                logger.error(f"Brace counting strategy also failed: {e}")
            
            return {"error": f"Failed to parse JSON after all strategies. Response length: {len(response_text)} chars"}
                
        except Exception as e:
            logger.error(f"Error extracting facts: {e}", exc_info=True)
            return {"error": str(e)}
    
    def generate_dialogue(self, facts: Dict[str, Any], prompt_settings: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Pass B: Generate doctor-patient dialogue from extracted facts
        
        Args:
            facts: Extracted facts from Pass A
            prompt_settings: Optional dict with 'language', 'length', 'tone', 'detail', 'focus' settings
            
        Returns:
            Dict with title and SSML-formatted dialogue
        """
        try:
            facts_json = json.dumps(facts, indent=2)
            
            # Default settings
            if prompt_settings is None:
                prompt_settings = {
                    'language': 'en',
                    'length': 'medium',
                    'tone': 'warm',
                    'detail': 'moderate',
                    'focus': 'balanced'
                }
            
            # Language settings
            language = prompt_settings.get('language', 'en')
            language_names = {
                'en': 'English',
                'he': 'Hebrew',
                'ru': 'Russian'
            }
            target_language = language_names.get(language, 'English')
            
            # Length settings
            length_configs = {
                'very_short': {'words': '100–150', 'turns': '4–6', 'time': '~45 seconds'},
                'short': {'words': '180–220', 'turns': '5–7', 'time': '~1.5 minutes'},
                'medium': {'words': '230–300', 'turns': '6–10', 'time': '~2 minutes'},
                'long': {'words': '350–450', 'turns': '10–15', 'time': '~3 minutes'}
            }
            length_cfg = length_configs.get(prompt_settings.get('length', 'medium'), length_configs['medium'])
            
            # Tone settings
            tone_descriptions = {
                'warm': 'warm, reassuring, human, non-judgmental',
                'casual': 'casual, friendly, relaxed, conversational',
                'professional': 'professional, clear, informative, confident'
            }
            tone_desc = tone_descriptions.get(prompt_settings.get('tone', 'warm'), tone_descriptions['warm'])
            
            # Detail level settings
            detail_instructions = {
                'simple': 'Keep explanations very simple and brief. Focus on the basics only.',
                'moderate': 'Provide balanced explanations with moderate detail. Include key points without overwhelming.',
                'detailed': 'Provide comprehensive explanations with more detail. Include context and background information.'
            }
            detail_inst = detail_instructions.get(prompt_settings.get('detail', 'moderate'), detail_instructions['moderate'])
            
            # Focus area settings
            focus_instructions = {
                'balanced': 'Cover all topics: findings, health impact, treatment options, and daily life effects.',
                'treatment': 'Focus primarily on treatment options and practical benefits. Keep other topics brief.',
                'health': 'Focus primarily on health implications and long-term effects. Keep other topics brief.',
                'daily': 'Focus primarily on daily life effects and real-world impact. Keep other topics brief.'
            }
            focus_inst = focus_instructions.get(prompt_settings.get('focus', 'balanced'), focus_instructions['balanced'])
            
            # Language-specific voice instructions
            voice_instructions = {
                'en': {
                    'doctor': 'Joanna (female voice)',
                    'patient': 'Matthew (male voice)'
                },
                'he': {
                    'doctor': 'he-IL-Chirp3-HD-Achernar (female voice, best quality)',
                    'patient': 'he-IL-Chirp3-HD-Achird (male voice, best quality)'
                },
                'ru': {
                    'doctor': 'Tatyana (female voice)',
                    'patient': 'Maxim (male voice)'
                }
            }
            voices = voice_instructions.get(language, voice_instructions['en'])
            
            system_prompt = f"""You are writing a short, {prompt_settings.get('tone', 'warm')}, engaging doctor–patient conversation intended to be listened to as audio.

The conversation must be written entirely in {target_language}.

The doctor (female voice) is friendly, calm, and empathetic. She explains things clearly using everyday language.

The patient (male voice) is curious, slightly concerned, and asks natural follow-up questions a real patient would ask.

This must feel like a genuine conversation between two people, NOT a medical lecture or scripted explainer.

IMPORTANT - Opening style:
- Start casually and directly: Just "Hi," then immediately begin discussing the report
- NO patient names (they are often mispronounced or invented)
- NO formal greetings like "Hello and thank you for coming in today"
- NO lengthy introductions or pleasantries
- Get straight to the point about the report findings
- Keep it natural and conversational from the very first sentence"""

            user_prompt = f"""Use ONLY the facts provided below. Do NOT invent findings, metrics, diagnoses, or recommendations.

Facts to use:
{facts_json}

Conversation requirements:
- Language: Write the ENTIRE conversation in {target_language}. All dialogue, explanations, and text must be in {target_language}.
- Length: {length_cfg['time']} total ({length_cfg['words']} spoken words)
- Turns: {length_cfg['turns']} total turns, doctor speaks ~70% of the time
- Tone: {tone_desc}
- Language style: Use plain, everyday {target_language} - avoid medical jargon. Use natural {target_language} expressions.
- Detail level: {detail_inst}
- Focus: {focus_inst}
- End with clear, simple next steps (spoken, not bullet points)

Writing style:
- Use contractions (it's, that's, you're) to sound natural
- Use simple analogies and metaphors when explaining medical concepts
- Show emotion and empathy
- Use phrases like "Here's what's happening..." instead of "The results indicate..."
- Make it conversational, not scripted
- Opening: Start with just "Hi," then immediately discuss the report (NO patient names)
- NO formal greetings or lengthy introductions - get straight to the findings
- Opening: Start with just "Hi," then immediately discuss the report (NO patient names)
- NO formal greetings or lengthy introductions - get straight to the findings

Audio / SSML rules:
- Output valid JSON with exactly two fields: "title" and "ssml"
- The "ssml" field must contain valid SSML wrapped in <speak>...</speak>
- Use <voice> tags to distinguish speakers:
    - Doctor: <voice name="{voices['doctor'].split(' ')[0]}"> (female voice)
- For Hebrew: Use proper SSML structure with <s> (sentence) tags for better pacing
- Add <break time="0.3s"/> between speaker turns for natural pauses
- Structure longer explanations with <p> (paragraph) tags when appropriate
    - Patient: <voice name="{voices['patient'].split(' ')[0]}"> (male voice)
- Use <break time="0.4s"/> sparingly to improve natural pacing between speaker changes
- Do NOT include markdown, commentary, or explanations outside the JSON
- IMPORTANT: The title field must also be in {target_language}

Example SSML structure (for {target_language}):
<speak>
  <voice name="{voices['doctor'].split(' ')[0]}">
    Hi, I wanted to go over your report with you. [Start discussing findings immediately - NO patient names]
    <break time="0.4s"/>
  </voice>

  <voice name="{voices['patient'].split(' ')[0]}">
    [Patient's dialogue in {target_language}]
  </voice>

  <voice name="{voices['doctor'].split(' ')[0]}">
    [Doctor's response in {target_language}]
  </voice>
</speak>

IMPORTANT - Opening style:
- Start with just "Hi," or equivalent in {target_language} (NO patient names)
- Immediately discuss the report findings - no formal greetings or "thank you for coming" phrases
- Get straight to the point about what the report shows

Output as JSON:
{{
  "title": "[Title in {target_language}]",
  "ssml": "<speak>...</speak>"
}}

Now generate the conversation in {target_language}."""

            messages = [
                {"role": "user", "content": system_prompt + "\n\n" + user_prompt}
            ]
            
            result = self.bedrock.invoke_model(
                messages=messages,
                model="claude_35_sonnet_v2",
                max_tokens=2000,
                temperature=0.7,
                endpoint="audio_script_generate_dialogue"
            )
            
            if not result.get("success"):
                logger.error(f"Failed to generate dialogue: {result.get('error')}")
                return {"error": result.get("error", "Unknown error")}
            
            response_text = result.get("response", "")
            
            # Clean and extract JSON
            json_text = clean_json_text(response_text)
            
            # Helper function to fix common JSON issues
            def fix_json_common_issues(text):
                """Fix common JSON formatting issues"""
                fixed = text
                # Remove trailing commas
                fixed = re.sub(r',\s*}', '}', fixed)
                fixed = re.sub(r',\s*]', ']', fixed)
                # Fix unquoted keys (basic attempt - be careful not to break string values)
                # Only fix keys that are clearly at the start of a line or after {
                fixed = re.sub(r'(\{|\n)\s*(\w+):', r'\1"\2":', fixed)
                # Remove comments
                fixed = re.sub(r'//.*?$', '', fixed, flags=re.MULTILINE)
                fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)
                return fixed
            
            # Try multiple parsing strategies
            parsing_strategies = [
                ("standard", lambda: json.loads(json_text)),
                ("extract_braces", lambda: json.loads(json_text[json_text.find('{'):json_text.rfind('}')+1])),
                ("remove_escaped_newlines", lambda: json.loads(re.sub(r'\\n', ' ', json_text))),
                ("fix_common_issues", lambda: json.loads(fix_json_common_issues(json_text))),
                ("remove_all_newlines", lambda: json.loads(re.sub(r'\n', ' ', json_text))),
            ]
            
            for strategy_name, parse_func in parsing_strategies:
                try:
                    dialogue = parse_func()
                    
                    # Validate required fields
                    if "title" not in dialogue or "ssml" not in dialogue:
                        continue  # Try next strategy
                    
                    # Ensure SSML is properly wrapped
                    ssml = dialogue["ssml"]
                    if not ssml.startswith("<speak>"):
                        ssml = f"<speak>{ssml}</speak>"
                    if not ssml.endswith("</speak>"):
                        ssml = ssml.rstrip("</speak>") + "</speak>"
                    dialogue["ssml"] = ssml
                    
                    logger.info(f"Successfully generated dialogue script using {strategy_name} strategy")
                    return {"success": True, "dialogue": dialogue}
                    
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.debug(f"Strategy {strategy_name} failed: {e}")
                    continue
                except Exception as e:
                    logger.debug(f"Strategy {strategy_name} failed with unexpected error: {e}")
                    continue
            
            # If all strategies fail, try brace counting
            logger.error(f"All JSON parsing strategies failed for dialogue generation")
            logger.error(f"Cleaned JSON text (first 2000 chars): {json_text[:2000]}")
            logger.error(f"Cleaned JSON text (last 500 chars): {json_text[-500:]}")
            
            try:
                # Find the JSON object more carefully using brace counting
                start_idx = json_text.find('{')
                if start_idx >= 0:
                    brace_count = 0
                    end_idx = start_idx
                    for i in range(start_idx, len(json_text)):
                        if json_text[i] == '{':
                            brace_count += 1
                        elif json_text[i] == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    
                    if end_idx > start_idx:
                        json_snippet = json_text[start_idx:end_idx]
                        # Try with fixes
                        for fix_strategy in [json_snippet, fix_json_common_issues(json_snippet)]:
                            try:
                                dialogue = json.loads(fix_strategy)
                                
                                # Validate required fields
                                if "title" not in dialogue or "ssml" not in dialogue:
                                    continue  # Try next fix strategy
                                
                                # Ensure SSML is properly wrapped
                                ssml = dialogue["ssml"]
                                if not ssml.startswith("<speak>"):
                                    ssml = f"<speak>{ssml}</speak>"
                                if not ssml.endswith("</speak>"):
                                    ssml = ssml.rstrip("</speak>") + "</speak>"
                                dialogue["ssml"] = ssml
                                
                                logger.info("Successfully parsed JSON using brace counting with fixes")
                                return {"success": True, "dialogue": dialogue}
                            except:
                                continue
            except Exception as e:
                logger.error(f"Brace counting strategy also failed: {e}")
            
            return {"error": f"Failed to parse JSON after all strategies. Response length: {len(response_text)} chars"}
                
        except Exception as e:
            logger.error(f"Error generating dialogue: {e}", exc_info=True)
            return {"error": str(e)}
    
    def generate_script(self, report_data: Dict[str, Any], prompt_settings: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Complete 2-pass script generation
        
        Args:
            report_data: Sleep study report JSON or normalized data
            prompt_settings: Optional dict with 'language', 'length', 'tone', 'detail', 'focus' settings
            
        Returns:
            Dict with success status and dialogue script or error
        """
        # Pass A: Extract facts
        extract_result = self.extract_facts(report_data)
        if "error" in extract_result:
            return extract_result
        
        facts = extract_result.get("facts", {})
        if not facts:
            return {"error": "No facts extracted from report"}
        
        # Pass B: Generate dialogue with settings
        dialogue_result = self.generate_dialogue(facts, prompt_settings=prompt_settings)
        if "error" in dialogue_result:
            return dialogue_result
        
        return {
            "success": True,
            "facts": facts,
            "dialogue": dialogue_result.get("dialogue", {})
        }

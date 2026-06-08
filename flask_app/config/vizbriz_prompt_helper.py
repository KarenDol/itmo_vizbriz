"""
vizbriz_prompt_helper.py
Builds a single-call prompt, validates packets & responses, and enforces the JSON contract.
"""
import json, hashlib, re
from typing import Dict, Any

RESPONSE_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": ["clinical", "operational", "meta"], "properties": {"clinical": {"type": "object", "required": ["diagnosis", "rules_fired", "next_clinical_action", "risks_and_monitoring"], "properties": {"diagnosis": {"type": "string", "minLength": 3, "maxLength": 300}, "rules_fired": {"type": "array", "maxItems": 6, "items": {"type": "string"}}, "next_clinical_action": {"type": "string", "minLength": 3, "maxLength": 300}, "risks_and_monitoring": {"type": "array", "maxItems": 3, "items": {"type": "string"}}}, "additionalProperties": False}, "operational": {"type": ["object", "null"], "required": ["stage", "completion_pct", "next_actions", "alerts"], "properties": {"stage": {"type": "string"}, "completion_pct": {"type": "number", "minimum": 0, "maximum": 100}, "next_actions": {"type": "array", "maxItems": 3, "items": {"type": "object", "required": ["action", "due", "blocking"], "properties": {"action": {"type": "string"}, "due": {"type": "string"}, "blocking": {"type": "boolean"}}, "additionalProperties": False}}, "alerts": {"type": "array", "maxItems": 3, "items": {"type": "string"}}}, "additionalProperties": False}, "meta": {"type": "object", "required": ["policy_version", "schema_version", "packet_hash"], "properties": {"policy_version": {"type": "string"}, "schema_version": {"type": "integer", "const": 2}, "packet_hash": {"type": "string"}}, "additionalProperties": False}}, "additionalProperties": False}
PACKET_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": ["patient", "policy_context", "sleep_study", "policy_features", "stage_context", "meta"], "properties": {"patient": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}, "sex": {"type": "string"}, "age": {"type": ["integer", "null"]}}, "additionalProperties": False}, "policy_context": {"type": "object", "required": ["policy_version"], "properties": {"policy_version": {"type": "string"}}, "additionalProperties": False}, "sleep_study": {"type": "object", "required": ["type", "date", "AHI", "SpO2_nadir", "severity"], "properties": {"type": {"type": "string", "enum": ["HST", "PSG", "unknown"]}, "date": {"type": "string"}, "AHI": {"type": ["number", "null"], "minimum": 0, "maximum": 200}, "SpO2_nadir": {"type": ["integer", "null"], "minimum": 50, "maximum": 100}, "ODI": {"type": ["number", "null"]}, "severity": {"type": "string", "enum": ["normal", "mild", "moderate", "severe", "unknown"]}}, "additionalProperties": False}, "phenotype_highlights": {"type": "object"}, "policy_features": {"type": "object"}, "stage_context": {"type": "object", "required": ["stage", "completion_pct"], "properties": {"stage": {"type": "string"}, "completion_pct": {"type": "number"}}, "additionalProperties": True}, "meta": {"type": "object", "required": ["schema_version", "packet_hash"], "properties": {"schema_version": {"type": "integer", "const": 2}, "packet_hash": {"type": "string"}}, "additionalProperties": False}}, "additionalProperties": True}

def compute_packet_hash(packet: Dict[str, Any]) -> str:
    def scrub(obj):
        if isinstance(obj, dict):
            return {k: scrub(v) for k,v in sorted(obj.items()) if k not in ('timestamp','generated_at')}
        if isinstance(obj, list):
            return [scrub(x) for x in obj]
        return obj
    data = json.dumps(scrub(packet), separators=(',',':'), ensure_ascii=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def render_single_prompt(packet: Dict[str, Any], template: str) -> str:
    packet = dict(packet)
    meta = dict(packet.get('meta', {}))
    meta['packet_hash'] = compute_packet_hash(packet)
    meta['schema_version'] = 2
    packet['meta'] = meta
    return template.replace("<<<PACKET_JSON>>>", json.dumps(packet, ensure_ascii=False))

def parse_llm_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    
    # First try to find JSON in the response
    m = re.search(r'\{.*\}\s*$', text, flags=re.S)
    if m:
        raw = m.group(0)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    
    # If no JSON found, try to extract from markdown format
    # Look for JSON after markdown headers
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, flags=re.S)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # If still no JSON, try to find any JSON-like structure (operational may be an object or null)
    json_match = re.search(r'\{[^{}]*"clinical"[^{}]*\{[^{}]*\}[^{}]*"operational"[^{}]*(\{[^{}]*\}|null)[^{}]*\}', text, flags=re.S)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    
    # If all else fails, try to parse the entire text as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse LLM response as JSON. Response was: {text[:200]}...") from e

def basic_validate_response(resp: Dict[str, Any]) -> None:
    assert isinstance(resp, dict), "Response must be JSON object"
    for key in ('clinical','operational','meta'):
        assert key in resp, f"Missing section: {key}"
    assert 'diagnosis' in resp['clinical']
    # operational may be null when no operational state was present in the packet
    if resp.get('operational') is None:
        return
    assert isinstance(resp['operational'].get('completion_pct', 0), (int, float))

__all__ = ["render_single_prompt","compute_packet_hash","parse_llm_json","basic_validate_response","RESPONSE_SCHEMA","PACKET_SCHEMA"]

#!/usr/bin/env python3
"""
Google Cloud Text-to-Speech Service
Converts text/SSML to audio files using Google Cloud TTS
Primarily used for Hebrew, which is not supported by Amazon Polly
"""

import logging
import json
import os
import re
from typing import Dict, Any, Optional
from google.cloud import texttospeech
from google.oauth2 import service_account

logger = logging.getLogger(__name__)


class AudioGoogleTTSService:
    """Service for converting text/SSML to audio using Google Cloud Text-to-Speech"""
    
    # Voice configuration by language
    # Using Chirp3-HD voices (newest, highest quality) when available, fallback to Wavenet
    VOICES = {
        'he': {
            'doctor': 'he-IL-Chirp3-HD-Achernar',  # Best quality female Hebrew voice (Chirp3-HD)
            'patient': 'he-IL-Chirp3-HD-Achird',  # Best quality male Hebrew voice (Chirp3-HD)
            'doctor_fallback': 'he-IL-Wavenet-B',  # Fallback female voice
            'patient_fallback': 'he-IL-Wavenet-A'  # Fallback male voice
        }
    }
    
    SUPPORTED_LANGUAGES = ['he']  # Currently only Hebrew
    
    # Medical term pronunciation dictionary for Hebrew
    # Format: {term: pronunciation} - helps with acronyms and medical terms
    MEDICAL_PRONUNCIATIONS = {
        'AHI': 'אה-אי-אי',  # Apnea-Hypopnea Index
        'CPAP': 'סי-פאפ',  # Continuous Positive Airway Pressure
        'BMI': 'בי-אם-אי',  # Body Mass Index
        'OSA': 'או-אס-אה',  # Obstructive Sleep Apnea
        'PSG': 'פי-אס-גי',  # Polysomnography
        'REM': 'רם',  # Rapid Eye Movement
        'NREM': 'אן-רם',  # Non-REM
        'SpO2': 'אס-פי-או-שתיים',  # Oxygen saturation
        'O2': 'או-שתיים',  # Oxygen
    }
    
    def __init__(self):
        self.client = None
        self.available_voices_cache = {}  # Cache for available voices
        self._initialize_client()
        self._detect_available_voices()
    
    def _initialize_client(self):
        """Initialize Google Cloud TTS client using service account credentials"""
        try:
            # Get credentials from environment variable
            google_tts_json = os.getenv('GOOGLE_TTS_JSON')
            
            if not google_tts_json:
                logger.warning("GOOGLE_TTS_JSON environment variable not set")
                self.client = None
                return
            
            # Parse JSON credentials
            try:
                creds_dict = json.loads(google_tts_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GOOGLE_TTS_JSON: {e}")
                self.client = None
                return
            
            # Create credentials object
            credentials = service_account.Credentials.from_service_account_info(creds_dict)
            
            # Initialize client
            self.client = texttospeech.TextToSpeechClient(credentials=credentials)
            
            logger.info("Google Cloud TTS client initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google TTS client: {e}", exc_info=True)
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Google TTS service is available"""
        return self.client is not None
    
    def _detect_available_voices(self):
        """Detect available Hebrew voices and update configuration"""
        if not self.is_available():
            return
        
        try:
            voices = self.client.list_voices(language_code='he-IL')
            hebrew_voices = [v.name for v in voices.voices if 'he-IL' in v.language_codes[0]]
            
            # Prefer Chirp3-HD voices (newest, best quality)
            chirp3_hd_female = [v for v in hebrew_voices if 'Chirp3-HD' in v and ('Achernar' in v or 'Achird' in v or 'FEMALE' in v)]
            chirp3_hd_male = [v for v in hebrew_voices if 'Chirp3-HD' in v and ('Achird' in v or 'MALE' in v)]
            
            # Update voice configuration if better voices are available
            if chirp3_hd_female:
                # Use first available Chirp3-HD female voice
                self.VOICES['he']['doctor'] = chirp3_hd_female[0]
                logger.info(f"Using best available Hebrew female voice: {chirp3_hd_female[0]}")
            
            if chirp3_hd_male:
                # Use first available Chirp3-HD male voice
                self.VOICES['he']['patient'] = chirp3_hd_male[0]
                logger.info(f"Using best available Hebrew male voice: {chirp3_hd_male[0]}")
            
            self.available_voices_cache['he-IL'] = hebrew_voices
            
        except Exception as e:
            logger.warning(f"Could not detect available voices, using defaults: {e}")
    
    def _apply_medical_pronunciations(self, text: str) -> str:
        """
        Apply medical term pronunciations to text
        Replaces medical acronyms with their Hebrew pronunciations
        """
        result = text
        for term, pronunciation in self.MEDICAL_PRONUNCIATIONS.items():
            # Replace term with pronunciation (case-insensitive, whole word)
            pattern = r'\b' + re.escape(term) + r'\b'
            result = re.sub(pattern, pronunciation, result, flags=re.IGNORECASE)
        return result
    
    def _enhance_ssml_for_google(self, ssml: str) -> str:
        """
        Enhance SSML for better Google TTS quality
        - Adds paragraph/sentence structure
        - Adds breaks for pacing
        - Applies medical pronunciations
        """
        # First, apply medical pronunciations
        enhanced = self._apply_medical_pronunciations(ssml)
        
        # Ensure proper SSML structure
        if not enhanced.strip().startswith('<speak>'):
            enhanced = f'<speak>{enhanced}</speak>'
        
        # Add paragraph breaks for better pacing
        # Replace multiple periods/exclamation/question marks with paragraph breaks
        enhanced = re.sub(r'([.!?])\s+([א-ת])', r'\1</s><s>\2', enhanced)
        
        # Add breaks after voice tags for natural pauses
        enhanced = re.sub(r'(</voice>)(\s*)(<voice)', r'\1<break time="0.3s"/>\3', enhanced)
        
        # Ensure proper sentence structure
        if '<s>' not in enhanced and '</s>' not in enhanced:
            # Wrap content in sentences if not already structured
            enhanced = re.sub(r'(<voice[^>]*>)([^<]+)(</voice>)', 
                            r'\1<s>\2</s>\3', enhanced, flags=re.DOTALL)
        
        return enhanced
    
    def synthesize_speech(self, 
                          text: str, 
                          language_code: str = 'he-IL',
                          voice_name: str = None,
                          ssml: bool = False,
                          use_ssml: bool = True) -> Dict[str, Any]:
        """
        Synthesize speech from text or SSML with enhanced quality
        
        Args:
            text: Text or SSML content
            language_code: Language code (e.g., 'he-IL' for Hebrew)
            voice_name: Voice to use (defaults to doctor voice for language)
            ssml: Whether the input is SSML
            use_ssml: Whether to use SSML for synthesis (better quality)
            
        Returns:
            Dict with audio data and metadata
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "Google TTS service not available. Check GOOGLE_TTS_JSON environment variable."
            }
        
        try:
            # Determine voice (try best quality first, fallback if needed)
            if voice_name is None:
                lang = language_code.split('-')[0]  # Extract 'he' from 'he-IL'
                voices = self.VOICES.get(lang, {})
                voice_name = voices.get('doctor', 'he-IL-Wavenet-B')
            
            # Try to use the best quality voice, fallback if it doesn't exist
            original_voice = voice_name
            try_voices = [voice_name]
            lang = language_code.split('-')[0]
            voices_config = self.VOICES.get(lang, {})
            
            # Add fallback voices if using Chirp3-HD
            if 'Chirp3-HD' in voice_name:
                if 'doctor' in voice_name or voice_name == voices_config.get('doctor'):
                    try_voices.append(voices_config.get('doctor_fallback', 'he-IL-Wavenet-B'))
                elif 'patient' in voice_name or voice_name == voices_config.get('patient'):
                    try_voices.append(voices_config.get('patient_fallback', 'he-IL-Wavenet-A'))
            
            # Process text/SSML
            if ssml and use_ssml:
                # Enhance SSML for better quality
                enhanced_ssml = self._enhance_ssml_for_google(text)
                synthesis_input = texttospeech.SynthesisInput(ssml=enhanced_ssml)
            else:
                # Apply medical pronunciations to plain text
                processed_text = self._apply_medical_pronunciations(text)
                if not processed_text or not processed_text.strip():
                    return {
                        "success": False,
                        "error": "Empty text content"
                    }
                synthesis_input = texttospeech.SynthesisInput(text=processed_text)
            
            # Configure voice
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )
            
            # Configure audio with better quality settings
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.0,  # Normal speed (can adjust 0.25-4.0)
                pitch=0.0,  # Normal pitch (can adjust -20.0 to 20.0)
                volume_gain_db=0.0  # Normal volume
            )
            
            # Try synthesis with primary voice, fallback if needed
            last_error = None
            for try_voice in try_voices:
                try:
                    voice.name = try_voice
                    response = self.client.synthesize_speech(
                        input=synthesis_input,
                        voice=voice,
                        audio_config=audio_config
                    )
                    
                    audio_data = response.audio_content
                    
                    if try_voice != original_voice:
                        logger.info(f"Used fallback voice {try_voice} (original: {original_voice})")
                    
                    logger.info(f"Successfully synthesized speech: {len(audio_data)} bytes, voice={try_voice}, language={language_code}")
                    
                    return {
                        "success": True,
                        "audio_data": audio_data,
                        "content_type": "audio/mpeg",
                        "voice_id": try_voice,
                        "format": "mp3",
                        "language_code": language_code
                    }
                except Exception as e:
                    last_error = e
                    if 'not found' in str(e).lower() or 'invalid' in str(e).lower():
                        logger.warning(f"Voice {try_voice} not available, trying fallback...")
                        continue
                    else:
                        raise
            
            # If all voices failed, raise the last error
            raise last_error if last_error else Exception("All voice attempts failed")
            
        except Exception as e:
            logger.error(f"Error synthesizing speech with Google TTS: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Google TTS error: {str(e)}"
            }
    
    def synthesize_dialogue(self, ssml: str, language: str = 'he') -> Dict[str, Any]:
        """
        Synthesize a dialogue with multiple voices
        Parses SSML to identify doctor (female) vs patient (male) segments
        
        Args:
            ssml: SSML with dialogue (contains voice tags for different speakers)
            language: Language code ('he' for Hebrew)
            
        Returns:
            Dict with audio data
        """
        try:
            import io
            
            if language != 'he':
                return {
                    "success": False,
                    "error": f"Google TTS service currently only supports Hebrew (he). Language '{language}' not supported."
                }
            
            # Get voices for Hebrew (use best quality Chirp3-HD voices)
            voices = self.VOICES.get('he', {})
            doctor_voice = voices.get('doctor', 'he-IL-Wavenet-B')  # Best quality female
            patient_voice = voices.get('patient', 'he-IL-Wavenet-A')  # Best quality male
            
            # Extract doctor and patient segments from SSML
            # Look for voice tags (case-insensitive)
            # Try exact voice name match first
            doctor_pattern = rf'<voice[^>]*name=["\']?{re.escape(doctor_voice)}["\']?>(.*?)</voice>'
            patient_pattern = rf'<voice[^>]*name=["\']?{re.escape(patient_voice)}["\']?>(.*?)</voice>'
            
            # Also try partial matches for Chirp3-HD and Wavenet voices
            doctor_pattern_alt = r'<voice[^>]*name=["\']?[^"\']*(?:[Cc]hirp3-[Hh][Dd]-[Aa]chernar|[Ww]avenet-[Bb])[^"\']*["\']?>(.*?)</voice>'
            patient_pattern_alt = r'<voice[^>]*name=["\']?[^"\']*(?:[Cc]hirp3-[Hh][Dd]-[Aa]chird|[Ww]avenet-[Aa])[^"\']*["\']?>(.*?)</voice>'
            
            doctor_matches = list(re.finditer(doctor_pattern, ssml, re.DOTALL | re.IGNORECASE))
            patient_matches = list(re.finditer(patient_pattern, ssml, re.DOTALL | re.IGNORECASE))
            
            # If no matches with exact pattern, try alternative patterns
            if not doctor_matches and not patient_matches:
                doctor_matches = list(re.finditer(doctor_pattern_alt, ssml, re.DOTALL | re.IGNORECASE))
                patient_matches = list(re.finditer(patient_pattern_alt, ssml, re.DOTALL | re.IGNORECASE))
            
            # If still no matches, try to find any voice tags
            if not doctor_matches and not patient_matches:
                all_voice_tags = list(re.finditer(r'<voice[^>]*>(.*?)</voice>', ssml, re.DOTALL | re.IGNORECASE))
                # Try to determine speaker based on content or order (alternating)
                for i, match in enumerate(all_voice_tags):
                    # Alternate between doctor and patient if we can't determine
                    speaker = 'doctor' if i % 2 == 0 else 'patient'
                    if speaker == 'doctor':
                        doctor_matches.append(match)
                    else:
                        patient_matches.append(match)
            
            # If we have multiple segments, synthesize separately and combine
            if len(doctor_matches) > 0 or len(patient_matches) > 0:
                # Combine all matches in order
                all_segments = []
                for match in sorted(doctor_matches + patient_matches, key=lambda m: m.start()):
                    # Determine if this is a doctor or patient segment
                    match_text = match.group(0).lower()
                    if (doctor_voice.lower() in match_text or 
                        'wavenet-b' in match_text or 
                        'chirp3-hd-achernar' in match_text):
                        all_segments.append(('doctor', match.group(1)))
                    elif (patient_voice.lower() in match_text or 
                          'wavenet-a' in match_text or 
                          'chirp3-hd-achird' in match_text):
                        all_segments.append(('patient', match.group(1)))
                    else:
                        # Default to doctor if unclear
                        all_segments.append(('doctor', match.group(1)))
                
                if len(all_segments) > 0:
                    # Synthesize each segment separately
                    audio_parts = []
                    for i, (speaker, text) in enumerate(all_segments):
                        # Clean up the text (remove nested tags, keep content)
                        clean_text = re.sub(r'<[^>]+>', '', text).strip()
                        if not clean_text:
                            logger.warning(f"Empty text segment {i+1}/{len(all_segments)} for {speaker}, skipping")
                            continue
                        
                        # Determine voice based on speaker
                        voice_id = doctor_voice if speaker == 'doctor' else patient_voice
                        
                        logger.info(f"Synthesizing segment {i+1}/{len(all_segments)} ({speaker}) with voice {voice_id} ({len(clean_text)} chars)")
                        
                        # Enhance text with SSML structure for better quality
                        # Wrap in SSML with sentence structure and breaks
                        enhanced_text = f'<speak><s>{clean_text}</s></speak>'
                        
                        # Synthesize this segment with SSML for better quality
                        result = self.synthesize_speech(
                            text=enhanced_text,
                            language_code='he-IL',
                            voice_name=voice_id,
                            ssml=True,
                            use_ssml=True
                        )
                        
                        if result.get("success"):
                            audio_data = result.get("audio_data")
                            if audio_data and len(audio_data) > 0:
                                audio_parts.append(audio_data)
                                logger.info(f"✓ Successfully synthesized segment {i+1}: {len(audio_data)} bytes")
                            else:
                                logger.error(f"✗ Empty audio data returned for segment {i+1} ({speaker})")
                        else:
                            error_msg = result.get('error', 'Unknown error')
                            logger.error(f"✗ Failed to synthesize segment {i+1} ({speaker}): {error_msg}")
                    
                    if audio_parts:
                        # Combine all audio parts (simple concatenation for MP3)
                        combined_audio = b''.join(audio_parts)
                        logger.info(f"✓ Successfully synthesized dialogue: {len(all_segments)} segments parsed, {len(audio_parts)} audio parts created, total {len(combined_audio)} bytes")
                        return {
                            "success": True,
                            "audio_data": combined_audio,
                            "content_type": "audio/mpeg",
                            "voice_id": f"{doctor_voice}/{patient_voice}",
                            "format": "mp3",
                            "language": language
                        }
                    else:
                        logger.error(f"✗ No audio parts were successfully synthesized from {len(all_segments)} segments")
                        return {
                            "success": False,
                            "error": f"Failed to synthesize any audio segments. {len(all_segments)} segments were found but none produced audio data."
                        }
            
            # Fallback: synthesize entire SSML as single piece (extract text)
            logger.info("No voice segments found, synthesizing entire dialogue as single piece")
            clean_text = re.sub(r'<[^>]+>', '', ssml)
            clean_text = ' '.join(clean_text.split())
            
            if not clean_text or not clean_text.strip():
                return {
                    "success": False,
                    "error": "No text content found in SSML"
                }
            
            # Enhance text with SSML structure
            enhanced_text = f'<speak><s>{clean_text}</s></speak>'
            
            result = self.synthesize_speech(
                text=enhanced_text,
                language_code='he-IL',
                voice_name=doctor_voice,
                ssml=True,
                use_ssml=True
            )
            
            if result.get("success") and result.get("audio_data"):
                logger.info(f"Fallback synthesis successful: {len(result.get('audio_data'))} bytes")
            
            return result
                
        except Exception as e:
            logger.error(f"Error parsing dialogue SSML: {e}", exc_info=True)
            # Fallback to simple synthesis
            clean_text = re.sub(r'<[^>]+>', '', ssml)
            clean_text = ' '.join(clean_text.split())
            
            voices = self.VOICES.get('he', {})
            fallback_voice = voices.get('doctor', 'he-IL-Wavenet-B')
            
            logger.warning(f"Using fallback voice ({fallback_voice}) for entire dialogue")
            # Enhance text with SSML structure
            enhanced_text = f'<speak><s>{clean_text}</s></speak>'
            
            result = self.synthesize_speech(
                text=enhanced_text,
                language_code='he-IL',
                voice_name=fallback_voice,
                ssml=True,
                use_ssml=True
            )
            
            if result.get("success") and result.get("audio_data"):
                logger.info(f"Fallback synthesis successful: {len(result.get('audio_data'))} bytes")
            
            return result

#!/usr/bin/env python3
"""
Amazon Polly Text-to-Speech Service
Converts SSML scripts to audio files
"""

import logging
import boto3
import io
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class AudioPollyService:
    """Service for converting SSML scripts to audio using Amazon Polly"""
    
    # Voice configuration by language
    # NOTE: Amazon Polly does NOT support Hebrew (he-IL). Hebrew option is disabled.
    # Zeina is for Arabic (arb), not Hebrew. Use English or Russian instead.
    VOICES = {
        'en': {
            'doctor': 'Joanna',  # Neural voice, female, steady, professional
            'patient': 'Matthew'  # Neural voice, male, friendly, conversational
        },
        'he': {
            'doctor': 'Zeina',  # WARNING: Zeina is Arabic, not Hebrew. Hebrew is NOT supported by Polly.
            'patient': 'Zeina'  # This will produce incorrect/empty audio for Hebrew text.
        },
        'ru': {
            'doctor': 'Tatyana',  # Neural voice, female, Russian
            'patient': 'Maxim'  # Neural voice, male, Russian
        }
    }
    
    # Languages actually supported by Amazon Polly
    SUPPORTED_LANGUAGES = ['en', 'ru']  # Hebrew (he) is NOT supported
    
    # Default voices (English)
    DOCTOR_VOICE = "Joanna"
    PATIENT_VOICE = "Matthew"
    
    def __init__(self):
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize Amazon Polly client
        Uses EC2 instance role when running on EC2 (preferred), falls back to explicit credentials
        """
        try:
            import os
            region = os.getenv('AWS_REGION', 'us-west-2')
            
            # On EC2, try to use instance role first (more secure, no credentials needed)
            # Create a session with no credentials to force use of instance role
            # This bypasses environment variables and uses the EC2 instance role
            try:
                # Create session without credentials to use instance role
                session = boto3.Session()
                # Test if instance role is available
                sts_client = session.client('sts', region_name=region)
                identity = sts_client.get_caller_identity()
                identity_arn = identity.get('Arn', 'Unknown')
                
                # If we got here, instance role works - use it for Polly
                # Use the session to create Polly client (will use instance role)
                self.client = session.client('polly', region_name=region)
                logger.info(f"Amazon Polly client initialized in region {region}")
                logger.info(f"✓ Using EC2 instance role: {identity_arn}")
                return
            except Exception as instance_role_error:
                logger.warning(f"EC2 instance role not available: {instance_role_error}")
                logger.info("Falling back to explicit IAM user credentials...")
        
            # Fallback: Use explicit credentials if instance role doesn't work
            access_key = os.getenv('AWS_ACCESS_KEY_ID')
            secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
            
            if access_key and secret_key:
                self.client = boto3.client(
                    'polly',
                    region_name=region,
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key
                )
                # Try to get caller identity to verify which user is being used
                try:
                    sts_client = boto3.client(
                        'sts',
                        region_name=region,
                        aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key
                    )
                    identity = sts_client.get_caller_identity()
                    user_arn = identity.get('Arn', 'Unknown')
                    logger.info(f"Amazon Polly client initialized in region {region}")
                    logger.info(f"Using IAM user credentials: {user_arn}")
                except Exception as sts_error:
                    logger.warning(f"Could not verify IAM identity: {sts_error}")
                    logger.info(f"Amazon Polly client initialized in region {region} with explicit credentials")
            else:
                raise Exception("No AWS credentials available (neither instance role nor explicit credentials)")
                
        except Exception as e:
            logger.error(f"Failed to initialize Polly client: {e}")
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Polly service is available"""
        return self.client is not None
    
    def synthesize_speech(self, 
                          ssml: str, 
                          voice_id: str = None,
                          output_format: str = "mp3") -> Dict[str, Any]:
        """
        Synthesize speech from SSML
        
        Args:
            ssml: SSML-formatted text
            voice_id: Voice to use (defaults to DOCTOR_VOICE)
            output_format: Audio format (mp3, ogg_vorbis, pcm)
            
        Returns:
            Dict with audio data and metadata
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "Polly service not available"
            }
        
        if voice_id is None:
            voice_id = self.DOCTOR_VOICE
        
        try:
            # Ensure SSML is properly formatted
            if not ssml.strip().startswith("<speak>"):
                ssml = f"<speak>{ssml}</speak>"
            if not ssml.strip().endswith("</speak>"):
                ssml = ssml.rstrip("</speak>") + "</speak>"
            
            # Check SSML length (Polly has limits)
            # Remove SSML tags for length check
            import re
            text_only = re.sub(r'<[^>]+>', '', ssml)
            logger.debug(f"SSML text length: {len(text_only)} characters (Polly limit: 3000)")
            if len(text_only) > 3000:  # Polly limit is ~3000 characters
                logger.warning(f"SSML text too long ({len(text_only)} chars), will attempt synthesis but may fail")
                # Don't truncate - let Polly handle it or return an error
                # Truncation would break the dialogue structure
            
            # Try neural engine first for better quality, fall back to standard if not supported
            engine = 'neural'
            try:
                response = self.client.synthesize_speech(
                    Text=ssml,
                    TextType='ssml',
                    OutputFormat=output_format,
                    VoiceId=voice_id,
                    Engine=engine
                )
            except ClientError as e:
                # If neural fails with ValidationException, try standard as fallback
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'ValidationException':
                    logger.warning(f"Voice {voice_id} does not support neural engine, falling back to standard")
                    engine = 'standard'
                    response = self.client.synthesize_speech(
                        Text=ssml,
                        TextType='ssml',
                        OutputFormat=output_format,
                        VoiceId=voice_id,
                        Engine=engine
                    )
                else:
                    # Re-raise if it's a different error
                    raise
            
            # Read audio stream
            audio_data = response['AudioStream'].read()
            
            logger.info(f"Successfully synthesized speech: {len(audio_data)} bytes, voice={voice_id}")
            
            return {
                "success": True,
                "audio_data": audio_data,
                "content_type": response.get('ContentType', f'audio/{output_format}'),
                "request_id": response.get('ResponseMetadata', {}).get('RequestId'),
                "voice_id": voice_id,
                "format": output_format
            }
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Polly ClientError: {error_code} - {error_msg}")
            
            # Provide helpful error message for permission issues
            if error_code == 'AccessDeniedException':
                # Try to get the actual IAM user/role being used
                try:
                    import os
                    import boto3
                    sts_client = boto3.client(
                        'sts',
                        region_name=os.getenv('AWS_REGION', 'us-west-2'),
                        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
                    )
                    identity = sts_client.get_caller_identity()
                    user_arn = identity.get('Arn', 'Unknown')
                    account_id = identity.get('Account', 'Unknown')
                except Exception:
                    user_arn = "Could not determine"
                    account_id = "Could not determine"
                
                detailed_error = (
                    f"Polly Access Denied: The IAM identity does not have permission to use Amazon Polly.\n"
                    f"Current IAM identity: {user_arn}\n"
                    f"Account ID: {account_id}\n"
                    f"Required permission: polly:SynthesizeSpeech\n\n"
                    f"To fix:\n"
                    f"1. Go to AWS IAM Console: https://console.aws.amazon.com/iam/\n"
                    f"2. Find the user/role: {user_arn.split('/')[-1] if '/' in user_arn else user_arn}\n"
                    f"3. Add policy: 'AmazonPollyFullAccess' OR create custom policy with polly:SynthesizeSpeech\n"
                    f"4. Wait 1-2 minutes for permissions to propagate\n\n"
                    f"See: POLLY_PERMISSIONS_SETUP.md for detailed instructions."
                )
                logger.error(detailed_error)
                return {
                    "success": False,
                    "error": detailed_error,
                    "error_code": error_code,
                    "iam_identity": user_arn,
                    "help_url": "https://docs.aws.amazon.com/polly/latest/dg/security-iam.html"
                }
            
            return {
                "success": False,
                "error": f"Polly error: {error_code} - {error_msg}",
                "error_code": error_code
            }
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def synthesize_dialogue(self, ssml: str, language: str = 'en') -> Dict[str, Any]:
        """
        Synthesize a dialogue with multiple voices
        Parses SSML to identify doctor (female) vs patient (male) segments and uses appropriate voices
        
        Args:
            ssml: SSML with dialogue (contains voice tags for different speakers)
            language: Language code ('en', 'he', 'ru') to determine which voices to use
            
        Returns:
            Dict with audio data
        """
        try:
            import re
            import io
            
            # Check if language is supported
            if language == 'he':
                return {
                    "success": False,
                    "error": "Hebrew (עברית) is NOT supported by Amazon Polly. Zeina voice is for Arabic, not Hebrew. Please use English (en) or Russian (ru) instead.",
                    "error_code": "LanguageNotSupported",
                    "supported_languages": ["en", "ru"],
                    "help": "Amazon Polly does not currently support Hebrew text-to-speech. Consider using English or Russian, or use a different TTS service that supports Hebrew."
                }
            
            # Get voices for the specified language
            voices = self.VOICES.get(language, self.VOICES['en'])
            doctor_voice = voices['doctor']
            patient_voice = voices['patient']
            
            # Special handling for languages where both speakers use the same voice (e.g., Hebrew)
            same_voice = (doctor_voice == patient_voice)
            if same_voice:
                logger.info(f"Language {language} uses same voice ({doctor_voice}) for both speakers - synthesizing entire SSML as single audio")
                
                # Log original SSML info
                original_text = re.sub(r'<[^>]+>', '', ssml)
                logger.debug(f"Original SSML: {len(original_text)} chars of text, {len(ssml)} total length")
                
                # For same voice, synthesize entire SSML as one piece (avoids MP3 concatenation issues)
                # Simply remove voice tags but preserve ALL other content (breaks, prosody, text, etc.)
                # This is the safest approach - just strip the voice wrapper tags
                
                # Remove opening and closing voice tags, but preserve everything else
                # Use a more careful approach that preserves all content
                clean_ssml = re.sub(r'<voice[^>]*>', '', ssml, flags=re.IGNORECASE)
                clean_ssml = re.sub(r'</voice>', '', clean_ssml, flags=re.IGNORECASE)
                
                # Clean up excessive whitespace but preserve single spaces and SSML structure
                # Replace multiple spaces/newlines with single space, but keep SSML tags intact
                clean_ssml = re.sub(r'[ \t\n\r]+', ' ', clean_ssml)
                clean_ssml = re.sub(r'> ', '>', clean_ssml)  # Remove space after >
                clean_ssml = re.sub(r' <', '<', clean_ssml)  # Remove space before <
                
                # Ensure it's wrapped in <speak> tags
                if not clean_ssml.strip().startswith('<speak>'):
                    clean_ssml = f"<speak>{clean_ssml}</speak>"
                if not clean_ssml.strip().endswith('</speak>'):
                    clean_ssml = clean_ssml.rstrip('</speak>').rstrip() + '</speak>'
                
                logger.debug(f"Removed voice tags, preserving all other SSML content and structure")
                
                # Log cleaned SSML info
                text_only = re.sub(r'<[^>]+>', '', clean_ssml)
                logger.info(f"Cleaned SSML for {language}: {len(text_only)} characters of text (original: {len(original_text)}), {len(clean_ssml)} total SSML length")
                
                # Log first 200 chars of text for debugging
                if text_only:
                    logger.debug(f"First 200 chars of text: {text_only[:200]}...")
                
                result = self.synthesize_speech(clean_ssml, voice_id=doctor_voice)
                if result.get("success") and result.get("audio_data"):
                    audio_data = result.get("audio_data")
                    logger.info(f"✓ Successfully synthesized entire dialogue: {len(audio_data)} bytes ({language})")
                    return {
                        "success": True,
                        "audio_data": audio_data,
                        "content_type": "audio/mpeg",
                        "voice_id": doctor_voice,
                        "format": "mp3",
                        "language": language
                    }
                else:
                    logger.error(f"✗ Failed to synthesize entire dialogue: {result.get('error', 'Unknown error')}")
                    return result
            
            # For different voices, extract segments and synthesize separately
            # Check if SSML contains voice tags for different speakers
            # Check for any voice tags (case-insensitive)
            has_voice_tags = False
            for voice_name in [doctor_voice, patient_voice, 'Joanna', 'Matthew', 'Zeina', 'Tatyana', 'Maxim']:
                if f'voice name="{voice_name}"' in ssml or f'voice name=\"{voice_name}\"' in ssml:
                    has_voice_tags = True
                    break
            
            if has_voice_tags:
                # Extract doctor and patient segments using language-specific voices
                # Try both quoted and unquoted patterns
                doctor_pattern = rf'<voice name=["\']?{re.escape(doctor_voice)}["\']?>(.*?)</voice>'
                patient_pattern = rf'<voice name=["\']?{re.escape(patient_voice)}["\']?>(.*?)</voice>'
                
                doctor_matches = list(re.finditer(doctor_pattern, ssml, re.DOTALL | re.IGNORECASE))
                patient_matches = list(re.finditer(patient_pattern, ssml, re.DOTALL | re.IGNORECASE))
                
                # Also check for English voices as fallback
                if not doctor_matches and not patient_matches:
                    doctor_pattern = r'<voice name=["\']?Joanna["\']?>(.*?)</voice>'
                    patient_pattern = r'<voice name=["\']?Matthew["\']?>(.*?)</voice>'
                    doctor_matches = list(re.finditer(doctor_pattern, ssml, re.DOTALL | re.IGNORECASE))
                    patient_matches = list(re.finditer(patient_pattern, ssml, re.DOTALL | re.IGNORECASE))
                
                logger.debug(f"Found {len(doctor_matches)} doctor segments and {len(patient_matches)} patient segments")
                
                # Combine all matches in order (for different voices)
                all_segments = []
                for match in sorted(doctor_matches + patient_matches, key=lambda m: m.start()):
                    # Determine if this is a doctor or patient segment
                    match_text = match.group(0).lower()
                    if doctor_voice.lower() in match_text or 'joanna' in match_text or 'tatyana' in match_text:
                        all_segments.append(('doctor', match.group(1)))
                    elif patient_voice.lower() in match_text or 'matthew' in match_text or 'maxim' in match_text:
                        all_segments.append(('patient', match.group(1)))
                    else:
                        # Default to doctor if unclear
                        all_segments.append(('doctor', match.group(1)))
                
                if len(all_segments) > 0:
                    # Synthesize each segment separately and combine
                    audio_parts = []
                    for i, (speaker, text) in enumerate(all_segments):
                        # Clean up the text (remove nested tags, keep content)
                        clean_text = re.sub(r'<[^>]+>', '', text).strip()
                        if not clean_text:
                            logger.warning(f"Empty text segment {i+1}/{len(all_segments)} for {speaker}, skipping")
                            continue
                        
                        # Determine voice based on speaker and language
                        voice_id = doctor_voice if speaker == 'doctor' else patient_voice
                        
                        # Create SSML for this segment
                        segment_ssml = f"<speak>{clean_text}</speak>"
                        
                        # Synthesize this segment
                        logger.info(f"Synthesizing segment {i+1}/{len(all_segments)} ({speaker}) with voice {voice_id} ({len(clean_text)} chars, language: {language})")
                        result = self.synthesize_speech(segment_ssml, voice_id=voice_id)
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
                        logger.info(f"✓ Successfully synthesized dialogue: {len(all_segments)} segments parsed, {len(audio_parts)} audio parts created, total {len(combined_audio)} bytes ({language})")
                        return {
                            "success": True,
                            "audio_data": combined_audio,
                            "content_type": "audio/mpeg",
                            "voice_id": f"{doctor_voice}/{patient_voice}",
                            "format": "mp3",
                            "language": language
                        }
                    else:
                        logger.error(f"✗ No audio parts were successfully synthesized from {len(all_segments)} segments. This indicates all synthesis attempts failed.")
                        # Return error with details
                        return {
                            "success": False,
                            "error": f"Failed to synthesize any audio segments. {len(all_segments)} segments were found but none produced audio data.",
                            "segments_found": len(all_segments),
                            "language": language
                        }
                
                # Fallback: if parsing failed or no segments found, use doctor voice for entire dialogue
                logger.warning(f"Could not parse voice tags or no segments found ({len(all_segments)} segments), using doctor voice ({doctor_voice}) for entire dialogue")
                # For Hebrew, try to clean the SSML first - remove voice tags and just use the content
                if same_voice and language == 'he':
                    # Extract all text content from SSML, removing voice tags
                    clean_ssml = re.sub(r'<voice[^>]*>', '', ssml)
                    clean_ssml = re.sub(r'</voice>', '', clean_ssml)
                    logger.info(f"Hebrew fallback: cleaned SSML, synthesizing entire dialogue with {doctor_voice}")
                    result = self.synthesize_speech(clean_ssml, voice_id=doctor_voice)
                else:
                    result = self.synthesize_speech(ssml, voice_id=doctor_voice)
                if result.get("success") and result.get("audio_data"):
                    logger.info(f"Fallback synthesis successful: {len(result.get('audio_data'))} bytes")
                else:
                    logger.error(f"Fallback synthesis failed: {result.get('error', 'Unknown error')}")
                return result
            else:
                # Check for prosody tags as fallback (old format)
                has_prosody_tags = 'prosody rate="slow"' in ssml.lower() or 'prosody rate="medium"' in ssml.lower()
                if has_prosody_tags:
                    logger.info(f"Dialogue contains prosody tags but no voice tags - using doctor voice ({doctor_voice})")
                    result = self.synthesize_speech(ssml, voice_id=doctor_voice)
                    if result.get("success") and result.get("audio_data"):
                        logger.info(f"Prosody-based synthesis successful: {len(result.get('audio_data'))} bytes")
                    return result
                else:
                    # No speaker distinction, use doctor voice
                    logger.info(f"No voice tags found, using doctor voice ({doctor_voice}) for entire dialogue")
                    result = self.synthesize_speech(ssml, voice_id=doctor_voice)
                    if result.get("success") and result.get("audio_data"):
                        logger.info(f"Single-voice synthesis successful: {len(result.get('audio_data'))} bytes")
                    return result
                
        except Exception as e:
            logger.error(f"Error parsing dialogue SSML: {e}", exc_info=True)
            # Fallback to simple synthesis
            voices = self.VOICES.get(language, self.VOICES['en'])
            fallback_voice = voices['doctor']
            logger.warning(f"Using fallback voice ({fallback_voice}) for entire dialogue")
            result = self.synthesize_speech(ssml, voice_id=fallback_voice)
            if result.get("success") and result.get("audio_data"):
                logger.info(f"Fallback synthesis successful: {len(result.get('audio_data'))} bytes")
            return result
    
    def upload_to_s3(self, 
                     audio_data: bytes, 
                     s3_key: str,
                     bucket_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Upload audio file to S3
        
        Args:
            audio_data: Audio file bytes
            s3_key: S3 key/path
            bucket_name: S3 bucket name (defaults to env var)
            
        Returns:
            Dict with S3 URL and metadata
        """
        try:
            import os
            import boto3
            
            if bucket_name is None:
                bucket_name = os.getenv('S3_BUCKET_NAME')
            
            if not bucket_name:
                return {
                    "success": False,
                    "error": "S3 bucket name not configured"
                }
            
            s3_client = boto3.client(
                's3',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                region_name=os.getenv('AWS_REGION', 'us-west-2')
            )
            
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=audio_data,
                ContentType='audio/mpeg'
            )
            
            # Generate pre-signed URL (valid for 1 hour)
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=3600
            )
            
            logger.info(f"Uploaded audio to S3: {s3_key}")
            
            return {
                "success": True,
                "s3_key": s3_key,
                "s3_url": url,
                "bucket": bucket_name
            }
            
        except Exception as e:
            logger.error(f"Error uploading to S3: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

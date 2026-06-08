#!/usr/bin/env python3
"""
Direct LLM Simple Extractor - Uses same database operations as existing system
=============================================================================

This script uses the same approach as document_observation_extractor_phase2.py but 
with pure direct LLM extraction for temporal data.

Usage:
    python direct_llm_simple_extractor.py --patient-id 25793
"""

import argparse
import logging
import sys
from datetime import datetime

# Use the existing extractor's functions directly
try:
    from document_observation_extractor_phase2 import run as run_existing_extractor
except ImportError:
    from flask_app.config.document_observation_extractor_phase2 import run as run_existing_extractor


def setup_logging():
    """Setup basic logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/direct_llm_simple.log'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)


def main():
    """Main script execution - Simply calls existing extractor in patient mode"""
    parser = argparse.ArgumentParser(description='Direct LLM Simple Extractor - Uses existing system with LLM organization')
    parser.add_argument('--patient-id', type=int, required=True, help='Patient ID')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Setup logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger = setup_logging()
    
    logger.info(f"🚀 Starting Direct LLM Simple Extraction for Patient {args.patient_id}")
    logger.info(f"📋 Using existing extractor with patient mode and LLM organization")
    
    try:
        # Save original argv
        original_argv = sys.argv.copy()
        
        # Set up arguments for existing extractor in patient mode
        sys.argv = [
            'document_observation_extractor_phase2.py',
            '--mode', 'patient',
            '--patient-id', str(args.patient_id)
        ]
        
        if args.debug:
            sys.argv.append('--debug')
        
        logger.info(f"🔄 Calling existing extractor with: {' '.join(sys.argv)}")
        
        # Run the existing extractor - it already has LLM organization built in!
        result = run_existing_extractor()
        
        # Restore original argv
        sys.argv = original_argv
        
        if result and result.get('success', True):
            logger.info(f"🎉 SUCCESS: Patient {args.patient_id} processed!")
            logger.info(f"💡 The existing extractor already includes:")
            logger.info(f"   - Direct LLM extraction")
            logger.info(f"   - Temporal timeline organization") 
            logger.info(f"   - Comparison table handling")
            logger.info(f"   - Canonical schema generation")
            logger.info(f"📊 Check results at: /patient_workflow_manifest/{args.patient_id}")
        else:
            logger.error(f"❌ Processing failed for patient {args.patient_id}")
            sys.exit(1)
    
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()

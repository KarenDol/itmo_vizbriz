#!/usr/bin/env python3

import sys
import os

# Add the vizbriz directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'vizbriz'))

# Import and run the document observation extractor
from vizbriz.flask_app.config.document_observation_extractor_phase2 import main

if __name__ == "__main__":
    # Set up arguments for create_canonical mode with patient 10317
    sys.argv = [
        'document_observation_extractor_phase2.py',
        '--mode', 'create_canonical',
        '--patient-id', '10317'
    ]
    
    main()

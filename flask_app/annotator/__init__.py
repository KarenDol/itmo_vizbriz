"""
CBCT OSA Annotator Module

Dedicated module for CBCT segmentation review and labeling tool.
All annotator-related code is organized in this directory.
"""

from flask import Blueprint

# Create blueprint
# Don't specify template_folder - Flask will use the app's default template folder
# Templates will be referenced as 'annotator/cbct_annotator.html'
cbct_annotator_bp = Blueprint(
    'cbct_annotator',
    __name__
)

# Import routes to register them with the blueprint
from flask_app.annotator import routes


from flask import Blueprint, jsonify, request
from .main_routes import main  # Import main Blueprint
from .wizard_routes import wizard  # Import wizard Blueprint
from .dashboard_routes import dashboard  # Import dashboard blueprint
from flask_app.routes.file_management_routes import filemgmt  # Import file management blueprintS
from flask_app.routes.viewer_routes import viewer  # Import file management blueprintS
from flask_app.routes.document_validation_routes import docValid  # Import doc validation blueprints
from flask_app.routes.partnerMgmt_routes import partnerMgmt  # Import partner management blueprints
from flask_app.routes.tracking_routes import tracking  # Import tracking blueprint
from flask_app.routes.vizbriz_quiz_routes import vizbriz_quiz  # Import VizBriz quiz blueprint
from flask_app.annotator import cbct_annotator_bp  # Import CBCT annotator blueprint

__all__ = ['main', 'wizard','dashboard','filemgmt','viewer','docValid', 'partnerMgmt', 'tracking', 'vizbriz_quiz', 'cbct_annotator_bp']  # Export all Blueprints for cleaner imports

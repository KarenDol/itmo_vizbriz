import os
from pathlib import Path
# Try to import boto3, but don't fail if it's not available
try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    print("Warning: boto3 module not available. AWS features may not work.")
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import logging
from logging.handlers import TimedRotatingFileHandler
from flask_app.extensions import db, login_manager

# Try to import openai, but don't fail if it's not available
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("Warning: openai module not available. Some features may not work.")
from flask_app.routes.wizard_routes import wizard  # Import wizard blueprint
from flask_app.routes.short_wizard_routes import short_wizard  # Import short wizard blueprint
from flask_app.routes.file_management_routes import filemgmt  # Import file management blueprint
from flask_app.routes.viewer_routes import viewer  # Import viewer blueprint
from flask_app.routes.document_validation_routes import docValid  # Import document validation blueprint
from flask_app.routes.partnerMgmt_routes import partnerMgmt  # Import partner management blueprint
from flask_app.logging_config import logger
from flask_app.routes.osaagent_routes import osaagent  # Import osaagent blueprint
from flask_app.routes.conversion_quiz_agent import conversion_quiz_agent  # Import conversion quiz agent blueprint
from flask_app.routes.vizbriz_quiz_routes import vizbriz_quiz  # Import VizBriz multilingual quiz blueprint
from flask_app.routes.vizbriz_quiz_test_routes import vizbriz_quiz_test  # Import VizBriz quiz test blueprint
from flask_app.routes.level1_report_hebrew_routes import level1_report_hebrew_bp  # Hebrew Level-1 report preview
from flask_app.routes.o2_extraction_routes import o2_extraction  # Import O2 extraction testing blueprint
from flask_app.routes.sleep_routes import bp_sleep  # Import sleep timeline blueprint
from flask_app.routes.unified_dashboard import unified_bp  # Import unified dashboard blueprint
from flask_app.routes.admin_user_creation import admin_user_creation  # Import admin user creation blueprint
from flask_mail import Mail, Message

# Load environment variables (DB + AWS/S3) from env/app.env so secrets are not hardcoded here.
# NOTE: This will NOT override variables already set in the process environment.
try:
    from dotenv import load_dotenv

    _repo_root = Path(__file__).resolve().parents[1]
    _env_path = _repo_root / "env" / "app.env"
    if _env_path.exists():
        load_dotenv(dotenv_path=_env_path, override=False)
    else:
        print(f"Warning: env file not found at {_env_path} (continuing with existing environment)")
    # Optional: repo-root .env (not committed) for local overrides e.g. OPENAI_API_KEY / sleep pipeline
    _dotenv_path = _repo_root / ".env"
    if _dotenv_path.exists():
        load_dotenv(dotenv_path=_dotenv_path, override=False)
except ImportError:
    print("Warning: python-dotenv not available; env/app.env will not be loaded.")

# Non-secret defaults only — credentials must come from env / .env (see .env.example).
os.environ.setdefault('VIZBRIZ_INFO_EMAIL', 'info@vizbriz.com')
os.environ.setdefault('EXTERNAL_REPORT_BASE_URL', 'https://app.vizbriz.com')
# Environment Configuration
# Auto-detect environment based on ENVIRONMENT variable
ENVIRONMENT = os.getenv('ENVIRONMENT', 'production')

if ENVIRONMENT == 'production':
    # ===== PRODUCTION CONFIGURATION =====
    # Canonical host for all absolute links in emails/QR codes.
    # Intentionally hardcoded so deploys never require flipping BASE_URL.
    os.environ['BASE_URL'] = 'https://app.vizbriz.com'
    os.environ['FLASK_RUN_HOST'] = '0.0.0.0'
    os.environ['FLASK_RUN_PORT'] = '7000'
    # LLM calls enabled in production
    os.environ['DISABLE_LLM_CALLS'] = 'False' 
    print("=== PRODUCTION ENVIRONMENT ===")
    print(f"BASE_URL: {os.environ['BASE_URL']}")
    print(f"FLASK_RUN_HOST: {os.environ['FLASK_RUN_HOST']}")
    print(f"FLASK_RUN_PORT: {os.environ['FLASK_RUN_PORT']}")
    print(f"DISABLE_LLM_CALLS: {os.environ['DISABLE_LLM_CALLS']}")
    print("==============================")
else:
    # ===== DEVELOPMENT CONFIGURATION =====
    # Canonical host for all absolute links in emails/QR codes.
    # Intentionally hardcoded so deploys never require flipping BASE_URL.
    os.environ['BASE_URL'] = 'https://app.vizbriz.com'
    os.environ['FLASK_RUN_HOST'] = '0.0.0.0'
    os.environ['FLASK_RUN_PORT'] = '7000'
    # LLM calls disabled in development to save costs
    os.environ['DISABLE_LLM_CALLS'] = 'False'
    print("=== DEVELOPMENT ENVIRONMENT ===")
    print(f"BASE_URL: {os.environ['BASE_URL']}")
    print(f"FLASK_RUN_HOST: {os.environ['FLASK_RUN_HOST']}")
    print(f"FLASK_RUN_PORT: {os.environ['FLASK_RUN_PORT']}")
    print(f"DISABLE_LLM_CALLS: {os.environ['DISABLE_LLM_CALLS']}")
    print("===============================")
    
    os.environ['LLM_PROVIDER'] = 'openai'


# Bedrock-specific configuration (separate from S3 region)
os.environ['BEDROCK_AWS_REGION'] = 'us-west-2'  # New variable specifically for Bedrock
os.environ['AWS_RETRY_MODE'] = 'adaptive'
os.environ['AWS_MAX_ATTEMPTS'] = '10'

# OpenAI — key from environment only (OPENAI_API_KEY or SLEEP_STUDY_OPENAI_API_KEY)
if OPENAI_AVAILABLE:
    _openai_key = os.getenv('OPENAI_API_KEY') or os.getenv('SLEEP_STUDY_OPENAI_API_KEY')
    if _openai_key:
        openai.api_key = _openai_key
    else:
        print("Warning: OPENAI_API_KEY not set; OpenAI-backed features will be unavailable")
else:
    print("OpenAI module not available - some features may be limited")

def create_app():
    # Create the Flask application
    app = Flask(__name__, static_folder='flask_static')

    # Configure logging regardless of debug mode
    log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_file_path = os.path.join(log_dir, 'app.log')

    # Use TimedRotatingFileHandler for better Windows compatibility
    try:
        file_handler = TimedRotatingFileHandler(
            log_file_path, 
            when='midnight', 
            interval=1, 
            backupCount=7,
            encoding='utf-8'
        )
    except PermissionError:
        # Fallback: if we can't create the rotating handler, use a simple file handler
        print("Warning: Could not create rotating log file. Using simple file handler.")
        file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    
    file_handler.setLevel(logging.INFO)  # You can change to DEBUG if needed
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
    file_handler.setFormatter(formatter)

    # Add only once - check for any file handler, not just RotatingFileHandler
    if not any(isinstance(h, (TimedRotatingFileHandler, logging.FileHandler)) for h in app.logger.handlers):
        app.logger.addHandler(file_handler)

    # Set app logger level
    app.logger.setLevel(logging.INFO)

    # Suppress noisy logs
    for noisy in ['werkzeug', 'boto3', 'botocore', 'urllib3', 's3transfer']:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Set logging level
    app.logger.setLevel(logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    
    # Log startup information
    app.logger.info("=== Application Starting ===")
    app.logger.debug(f"Environment variable S3_BUCKET_NAME: {os.getenv('S3_BUCKET_NAME')}")
    app.logger.debug(f"AWS_REGION: {os.getenv('AWS_REGION')}")
    app.logger.debug(f"AWS_ACCESS_KEY_ID exists: {bool(os.getenv('AWS_ACCESS_KEY_ID'))}")
    app.logger.debug(f"AWS_SECRET_ACCESS_KEY exists: {bool(os.getenv('AWS_SECRET_ACCESS_KEY'))}")
    app.logger.info("===========================")

    # Configure the S3 client and other logging
    logging.getLogger("watchdog.observers.inotify_buffer").setLevel(logging.WARNING)

    # Configure Flask-Mail with SendGrid
    app.config['MAIL_SERVER'] = 'smtp.sendgrid.net'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = 'apikey'
    app.config['MAIL_PASSWORD'] = os.getenv('SENDGRID_API_KEY')
    app.config['MAIL_DEFAULT_SENDER'] = 'info@vizbriz.com'
    
    # Initialize Flask-Mail
    mail = Mail(app)
    app.extensions['mail'] = mail

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    if not app.config['SECRET_KEY']:
        raise ValueError("No SECRET_KEY set for Flask application. Set the SECRET_KEY environment variable.")
    
    # API key for Lambda to authenticate with data extraction endpoint
    # This allows Lambda functions to call the extraction API without user login
    app.config['EXTRACTION_API_KEY'] = os.getenv('EXTRACTION_API_KEY')
    # Configure database connection to use local MySQL server
    app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{os.getenv('DB_USERNAME', 'root')}:{os.getenv('DB_PASSWORD', 'new_password')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '3307')}/{os.getenv('DB_NAME', 'vizbriz')}"
    print("Environment Variables:")
    print(f"DB_USERNAME: {os.getenv('DB_USERNAME')}")
    _db_password = os.getenv('DB_PASSWORD')
    print(f"DB_PASSWORD: {(_db_password[:4] + '... (truncated)') if _db_password else None}")
    print(f"DB_HOST: {os.getenv('DB_HOST')}")
    print(f"DB_NAME: {os.getenv('DB_NAME')}")
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Ensure SQLAlchemy uses non-default pool settings (otherwise it falls back to
    # pool_size=5 / max_overflow=10 / pool_timeout=30, which can cause QueuePool timeouts under load)
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_recycle': 3600,   # recycle stale MySQL connections
        'pool_pre_ping': True,  # validate connections before use
        'pool_size': 10,
        'max_overflow': 20,
        'pool_timeout': 30,
    }
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 * 1024  # 5GB limit (for large CBCT RAR/ZIP files)
    app.config['S3_BUCKET_NAME'] = os.getenv('S3_BUCKET_NAME')
    if app.config['S3_BUCKET_NAME']:
        app.logger.info(f"S3 bucket configured: {app.config['S3_BUCKET_NAME']}")
    else:
        app.logger.warning(f"S3_BUCKET_NAME not found in environment variables")

    app.config['EXTERNAL_REPORT_BASE_URL'] = os.getenv('EXTERNAL_REPORT_BASE_URL')
    # Base URL for generating absolute links (emails, PDFs, etc.)
    app.config['BASE_URL'] = os.getenv('BASE_URL', '').rstrip('/')
    app.config['CBCT_MPR_MAX_WORKING_BYTES'] = int(os.getenv('CBCT_MPR_MAX_WORKING_BYTES', '3000000000'))
    # Default to tolerant behavior: skip any slices/files whose pixel dimensions
    # don't match the series, rather than failing the entire MPR generation run.
    app.config['CBCT_MPR_SKIP_DIM_MISMATCH_SLICES'] = os.getenv(
        'CBCT_MPR_SKIP_DIM_MISMATCH_SLICES', 'True'
    ).strip().lower() in ('1', 'true', 'yes', 'y', 'on')

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    from .routes.main_routes import main as main_blueprint
    from .routes.tracking_routes import tracking  # Import tracking blueprint
    from .routes.forms_management_routes import forms_mgmt  # Import forms management blueprint
    from .routes.admin_routes import admin  # Import admin blueprint
    from .routes.action_routes import action_bp  # Import action blueprint
    from .routes.cursor_routes import cursor_bp  # Import cursor blueprint
    from .routes.ingest_routes import ingest  # Import ingest blueprint
    from .routes.bedrock_vector_routes import bedrock_vector_bp  # Import Bedrock vector blueprint
    from .routes.document_processing_queue_routes import document_processing_queue_bp  # Import document processing queue blueprint
    from .routes.data_extraction_routes import data_extraction_bp  # Import data extraction blueprint
    from .routes.token_usage_routes import token_usage  # Import token usage blueprint
    from .routes.reports_files_routes import reports_files_bp  # Import reports & files blueprint
    from .routes.routes_stage_summary import stage_summary_bp  # Import stage summary blueprint
    from .routes.level4_microsection_routes import level4_micro_bp  # Import Level-4 micro-section blueprint
    from .routes.level3_report_routes import level3_report_bp  # Import Level-3 report blueprint
    from .routes.l3_autoreport_routes import l3_autoreport_bp  # Import L3 autoreport blueprint
    # Optional blueprint (may not be present in production deployments)
    try:
        from .routes.audio_test_routes import audio_test_bp  # type: ignore
    except ModuleNotFoundError:
        audio_test_bp = None
    app.register_blueprint(main_blueprint)
    app.register_blueprint(wizard)
    app.register_blueprint(short_wizard)
    app.register_blueprint(filemgmt)
    app.register_blueprint(viewer)
    app.register_blueprint(docValid)
    app.register_blueprint(partnerMgmt)
    app.register_blueprint(osaagent)
    app.register_blueprint(conversion_quiz_agent, url_prefix='')  # No URL prefix for conversion quiz agent
    app.register_blueprint(vizbriz_quiz)  # Register VizBriz multilingual quiz blueprint (/vizbriz)
    app.register_blueprint(vizbriz_quiz_test)  # Register VizBriz quiz test blueprint (/vizbriz-test)
    app.register_blueprint(level1_report_hebrew_bp)  # Register Hebrew Level-1 report preview blueprint (/vizbriz)
    app.register_blueprint(o2_extraction)  # Register O2 extraction testing blueprint
    app.register_blueprint(bp_sleep)  # Register sleep timeline blueprint
    app.register_blueprint(tracking)  # Register tracking blueprint
    app.register_blueprint(forms_mgmt)  # Register forms management blueprint
    app.register_blueprint(admin)  # Register admin blueprint
    app.register_blueprint(action_bp)  # Register action blueprint
    app.register_blueprint(cursor_bp)  # Register cursor blueprint
    app.register_blueprint(ingest)  # Register ingest blueprint
    app.register_blueprint(unified_bp)  # Register unified dashboard blueprint
    app.register_blueprint(admin_user_creation)  # Register admin user creation blueprint
    app.register_blueprint(bedrock_vector_bp)  # Register Bedrock vector blueprint
    app.register_blueprint(document_processing_queue_bp)  # Register document processing queue blueprint
    app.register_blueprint(data_extraction_bp)  # Register data extraction blueprint
    app.register_blueprint(token_usage)  # Register token usage blueprint
    app.register_blueprint(reports_files_bp)  # Register reports & files blueprint
    app.register_blueprint(stage_summary_bp)  # Register stage summary blueprint
    app.register_blueprint(level4_micro_bp)  # Register Level-4 micro-section blueprint
    app.register_blueprint(level3_report_bp)  # Register Level-3 report blueprint
    app.register_blueprint(l3_autoreport_bp)  # Register L3 autoreport blueprint
    if audio_test_bp is not None:
        app.register_blueprint(audio_test_bp)  # Register audio test blueprint
    from flask_app.annotator import cbct_annotator_bp
    app.register_blueprint(cbct_annotator_bp)  # Register CBCT annotator blueprint

    from .models import Dentist

    @login_manager.user_loader
    def load_user(user_id):
        return Dentist.query.get(int(user_id))

    # Add JSON filter for templates
    import json

    @app.template_filter('from_json')
    def from_json_filter(value):
        try:
            return json.loads(value) if value else {}
        except:
            return {}
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass
    
    # No additional mail config here; VIZBRIZ_INFO_EMAIL comes from os.environ above

    @app.after_request
    def add_cross_origin_resource_policy_for_embeddable_assets(response):
        """
        Pages that set Cross-Origin-Embedder-Policy (e.g. require-corp) only load subresources
        whose responses opt in via Cross-Origin-Resource-Policy. Without this, images under
        /flask_static/ can fail with net::ERR_BLOCKED_BY_RESPONSE (COEP) in DevTools.
        """
        try:
            path = request.path or ""
            if (
                path.startswith("/flask_static/")
                or path.startswith("/static/")
                or path.startswith("/vizbriz/assets/fonts/")
            ):
                response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        except Exception:
            pass
        return response

    with app.app_context():
        # Remove db.create_all() since tables already exist
        from .routes.main_routes import check_db_connection
        check_db_connection()
    return app
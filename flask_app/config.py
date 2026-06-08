import os

class Config:
    # Database configuration
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{os.getenv('DB_USERNAME', 'root')}:{os.getenv('DB_PASSWORD', 'new_password')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '3307')}/{os.getenv('DB_NAME', 'vizbriz')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Flask-Mail configuration
    MAIL_SERVER = 'smtp.sendgrid.net'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = 'apikey'
    MAIL_PASSWORD = os.getenv('SENDGRID_API_KEY')
    MAIL_DEFAULT_SENDER = 'eran@vizbriz.com'
    
    # Flask configuration
    SECRET_KEY = os.getenv('SECRET_KEY')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024 * 1024  # 5GB limit (for large CBCT RAR/ZIP files)
    
    # AWS configuration
    S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
    
    # Database connection pool settings
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': 3600,  # Recycle connections after 1 hour
        'pool_pre_ping': True,  # Enable connection health checks
        'pool_size': 10,  # Maximum number of connections to keep
        'max_overflow': 20,  # Maximum number of connections that can be created beyond pool_size
        'pool_timeout': 30,  # Seconds to wait before giving up on getting a connection from the pool
    }
    
    # LLM Configuration
    # Set to True to disable all LLM calls (useful for testing to save costs)
    DISABLE_LLM_CALLS = os.getenv('DISABLE_LLM_CALLS', 'False').lower() in ('true', '1', 'yes', 'on')
    
    # Environment configuration
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'production') 
# flask_app/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_login import LoginManager
from sqlalchemy import event
from sqlalchemy.exc import OperationalError, DisconnectionError
import time

db = SQLAlchemy()
mail = Mail()
login_manager = LoginManager()

def init_extensions(app):
    # Configure SQLAlchemy
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_recycle': 3600,  # Recycle connections after 1 hour
        'pool_pre_ping': True,  # Enable connection health checks
        'pool_size': 10,  # Maximum number of connections to keep
        'max_overflow': 20,  # Maximum number of connections that can be created beyond pool_size
        'pool_timeout': 30,  # Seconds to wait before giving up on getting a connection from the pool
    }
    
    # Initialize extensions
    db.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    
    # Add connection retry logic
    @event.listens_for(db.engine, 'connect')
    def connect(dbapi_connection, connection_record):
        connection_record.info['pid'] = id(dbapi_connection)

    @event.listens_for(db.engine, 'checkout')
    def checkout(dbapi_connection, connection_record, connection_proxy):
        pid = id(dbapi_connection)
        if connection_record.info['pid'] != pid:
            connection_record.info['pid'] = pid
            connection_record.info['checked_out'] = True

    def retry_on_disconnect(func):
        def wrapper(*args, **kwargs):
            max_retries = 3
            retry_delay = 1  # seconds
            
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DisconnectionError) as e:
                    if attempt == max_retries - 1:  # Last attempt
                        raise
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
        return wrapper

    # Apply retry decorator to common database operations
    original_query = db.session.query
    db.session.query = retry_on_disconnect(original_query)
    
    original_add = db.session.add
    db.session.add = retry_on_disconnect(original_add)
    
    original_commit = db.session.commit
    db.session.commit = retry_on_disconnect(original_commit)
    
    original_rollback = db.session.rollback
    db.session.rollback = retry_on_disconnect(original_rollback)

    return db, mail, login_manager
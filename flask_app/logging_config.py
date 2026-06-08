import logging
import os
import sys
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

def setup_logger():
    # Create a logger instance
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # Create a file handler with rotation
    # Use the project directory for the log file
    log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_file_path = os.path.join(log_dir, 'app.log')
    
    # Use TimedRotatingFileHandler instead of RotatingFileHandler for better Windows compatibility
    # This rotates logs daily instead of by size, which is less likely to cause file locking issues
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
    
    file_handler.setLevel(logging.DEBUG)

    # Create a console handler (optional)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # Define a formatter
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

# Create a global logger instance
logger = setup_logger()

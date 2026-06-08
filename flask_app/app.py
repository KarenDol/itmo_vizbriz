from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Get port from environment variable, default to 5000 if not set
port = int(os.getenv('FLASK_RUN_PORT', 5000)) 
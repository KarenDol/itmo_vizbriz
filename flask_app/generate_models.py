import os
import subprocess

# Database connection details
DB_USER = "admin"
DB_PASS = "Gamla2024!"
DB_HOST = "dbsharkbiit.ctouu1wp7xkj.us-east-2.rds.amazonaws.com"
DB_NAME = "sharkbiit"

# Construct the database URL
db_url = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"

# Command to generate models
command = f"flask-sqlacodegen '{db_url}' --flask > models222.py"

try:
    # Execute the command
    subprocess.run(command, shell=True, check=True)
    print("Models generated successfully!")
except subprocess.CalledProcessError as e:
    print(f"Error generating models: {e}")

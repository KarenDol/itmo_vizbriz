import sqlite3
import os

# Get the current directory
current_dir = os.getcwd()

# Create or connect to a database in the current directory
db_path = os.path.join(current_dir, 'dentist_data.db')
conn = sqlite3.connect(db_path)
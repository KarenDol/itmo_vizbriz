import os
import sqlite3

# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Construct the path to the database
db_path = os.path.join(script_dir, 'dentists_data.db')

# Connect to the database (this will create it if it doesn't exist)
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Create your table
cursor.execute('''
CREATE TABLE IF NOT EXISTS Dentists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    DSO TEXT,
    status TEXT,
    email TEXT,
    password TEXT,
    role TEXT,
    comment TEXT,
    last_updated DATE
)
''')

conn.commit()
conn.close()

print(f"Database created/connected at: {db_path}")
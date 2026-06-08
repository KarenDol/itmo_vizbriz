import mysql.connector
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def create_conversion_quiz_table():
    """
    Create the conversion_quiz table in the database
    """
    # Get database connection details from environment variables
    host = os.getenv('DB_HOST', 'vizbrizapp222.ch8koiygcu36.us-east-2.rds.amazonaws.com')
    user = os.getenv('DB_USERNAME', 'admin')
    password = os.getenv('DB_PASSWORD', 'Vizbriz2025!')
    database = os.getenv('DB_NAME', 'vizbriz')
    
    # Connect to the database
    conn = mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=database
    )
    cursor = conn.cursor()
    
    # Create the conversion_quiz table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversion_quiz (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        quiz_input TEXT NOT NULL,
        cta TEXT,
        clinic_email VARCHAR(120) NOT NULL,
        patient_email VARCHAR(120) NOT NULL,
        ai_response TEXT,
        gender VARCHAR(20),
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX (user_id),
        INDEX (clinic_email),
        INDEX (patient_email),
        INDEX (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """)
    
    # Commit the changes
    conn.commit()
    
    # Close the connection
    cursor.close()
    conn.close()
    
    print("conversion_quiz table created successfully!")

if __name__ == "__main__":
    create_conversion_quiz_table() 
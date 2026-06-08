import mysql.connector
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def remove_gender_column():
    """
    Remove gender column from the conversion_quiz table
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
    
    try:
        # Remove gender column from the conversion_quiz table
        cursor.execute("""
        ALTER TABLE conversion_quiz 
        DROP COLUMN gender;
        """)
        
        # Commit the changes
        conn.commit()
        print("Successfully removed gender column from conversion_quiz table!")
        
    except mysql.connector.Error as err:
        if err.errno == 1091:  # Column doesn't exist error
            print("Gender column doesn't exist in conversion_quiz table.")
        else:
            print(f"Error: {err}")
            raise err
            
    finally:
        # Close the connection
        cursor.close()
        conn.close()

if __name__ == "__main__":
    remove_gender_column() 
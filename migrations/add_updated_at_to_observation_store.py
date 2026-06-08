import mysql.connector
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def add_updated_at_column():
    """
    Add updated_at column to observation_store table
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
        # Add updated_at column
        cursor.execute("""
        ALTER TABLE observation_store
        ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
        """)
        
        # Commit the changes
        conn.commit()
        print("Successfully added updated_at column to observation_store table")
        
    except Exception as e:
        print(f"Error adding column: {str(e)}")
        conn.rollback()
        
    finally:
        # Close the connection
        cursor.close()
        conn.close()

if __name__ == "__main__":
    add_updated_at_column() 
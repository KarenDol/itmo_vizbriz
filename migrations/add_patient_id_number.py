"""Add id_number column to patients table for Israeli ID (teudat zehut)."""
import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv()


def add_id_number_column():
    """Add id_number column to patients table."""
    host = os.getenv('DB_HOST', 'vizbrizapp222.ch8koiygcu36.us-east-2.rds.amazonaws.com')
    user = os.getenv('DB_USERNAME', 'admin')
    password = os.getenv('DB_PASSWORD', 'Vizbriz2025!')
    database = os.getenv('DB_NAME', 'vizbriz')

    conn = mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=database
    )
    cursor = conn.cursor()

    try:
        cursor.execute("""
        ALTER TABLE patients
        ADD COLUMN id_number VARCHAR(20) NULL;
        """)
        conn.commit()
        print("Successfully added id_number column to patients table.")
    except mysql.connector.Error as err:
        if err.errno == 1060:
            print("id_number column already exists in patients table.")
        else:
            print(f"Error: {err}")
            raise err
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    add_id_number_column()

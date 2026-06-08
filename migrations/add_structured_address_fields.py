#!/usr/bin/env python3
"""
Migration script to add structured address fields to patients table.
This script adds new address fields while keeping the original address field for backward compatibility.
"""

import os
import sys
import mysql.connector
from mysql.connector import Error

def run_migration():
    """Execute the SQL migration to add structured address fields."""
    
    # Database connection parameters
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'vizbriz'),
        'port': int(os.getenv('DB_PORT', 3306))
    }
    
    try:
        # Connect to MySQL database
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor()
        
        print("Connected to MySQL database successfully")
        
        # Read and execute the SQL migration
        sql_file_path = os.path.join(os.path.dirname(__file__), 'add_structured_address_fields.sql')
        
        with open(sql_file_path, 'r') as file:
            sql_script = file.read()
        
        # Split the script into individual statements
        statements = [stmt.strip() for stmt in sql_script.split(';') if stmt.strip()]
        
        for statement in statements:
            if statement:
                print(f"Executing: {statement[:100]}...")
                cursor.execute(statement)
        
        connection.commit()
        print("Migration completed successfully!")
        
    except Error as e:
        print(f"Error during migration: {e}")
        sys.exit(1)
        
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            print("Database connection closed")

if __name__ == "__main__":
    run_migration()

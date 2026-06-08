#!/usr/bin/env python3
"""
Update all existing AdminFile records to set is_public=True
This makes all previously uploaded admin files visible to everyone.
"""

import mysql.connector
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def update_admin_files_to_public():
    """
    Update all existing AdminFile records to set is_public=True
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
    cursor = conn.cursor(dictionary=True)
    
    try:
        # First, count how many files will be updated
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM adminfiles 
            WHERE is_public = 0 OR is_public IS NULL
        """)
        result = cursor.fetchone()
        files_to_update = result['count']
        
        print(f"Found {files_to_update} admin files that need to be updated to public")
        
        if files_to_update == 0:
            print("No files need to be updated. All admin files are already public.")
            return
        
        # Get a sample of files that will be updated (for verification)
        cursor.execute("""
            SELECT id, name, patient_id, file_category, is_public 
            FROM adminfiles 
            WHERE is_public = 0 OR is_public IS NULL
            LIMIT 5
        """)
        sample_files = cursor.fetchall()
        
        if sample_files:
            print("\nSample files that will be updated:")
            for file in sample_files:
                print(f"  - ID: {file['id']}, Name: {file['name']}, Category: {file.get('file_category', 'N/A')}, Current is_public: {file['is_public']}")
        
        # Update all admin files to be public
        # All files uploaded by admins should be visible to everyone
        cursor.execute("""
            UPDATE adminfiles 
            SET is_public = 1 
            WHERE is_public = 0 OR is_public IS NULL
        """)
        
        rows_updated = cursor.rowcount
        
        # Commit the changes
        conn.commit()
        print(f"\n✅ Successfully updated {rows_updated} admin files to be public")
        
        # Verify the update
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM adminfiles 
            WHERE is_public = 1
        """)
        result = cursor.fetchone()
        total_public = result['count']
        
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM adminfiles 
            WHERE is_public = 0
        """)
        result = cursor.fetchone()
        total_private = result['count']
        
        print(f"\n📊 Summary:")
        print(f"   - Total public files: {total_public}")
        print(f"   - Total private files: {total_private}")
        
    except Exception as e:
        print(f"❌ Error updating admin files: {str(e)}")
        conn.rollback()
        raise
        
    finally:
        # Close the connection
        cursor.close()
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("Updating existing AdminFile records to be public")
    print("=" * 60)
    update_admin_files_to_public()
    print("\n✅ Migration completed!")

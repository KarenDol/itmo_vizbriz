#!/usr/bin/env python3
"""
Standalone Migration: Create DSO records from unique dentist DSO strings, then create associations
This ensures every dentist has a matching DSO record
"""

import mysql.connector
from mysql.connector import Error
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables (same as Flask app)
load_dotenv()

# Set the same hardcoded environment variables as Flask app
os.environ['DB_USERNAME'] = 'admin'
os.environ['DB_PASSWORD'] = 'Vizbriz2025!'
os.environ['DB_HOST'] = 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com'
os.environ['DB_PORT'] = '3306'
os.environ['DB_NAME'] = 'vizbriz'

# Database configuration - using same config as Flask app
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3307')),
    'database': os.getenv('DB_NAME', 'vizbriz'),
    'user': os.getenv('DB_USERNAME', 'root'),
    'password': os.getenv('DB_PASSWORD', 'new_password')
}

def connect_to_database():
    """Connect to MySQL database"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"❌ Error connecting to MySQL: {e}")
        return None

def migrate_dso_data():
    """
    Create DSO records from unique dentist DSO strings and create associations
    """
    print("🚀 Starting DSO migration from dentist data...")
    
    connection = connect_to_database()
    if not connection:
        return False
    
    try:
        cursor = connection.cursor()
        
        # Step 1: Get unique DSO values from dentists table
        print("\n📊 Step 1: Analyzing unique DSO values in dentists table...")
        
        cursor.execute("""
            SELECT DSO, COUNT(*) as dentist_count
            FROM dentists 
            WHERE DSO IS NOT NULL 
              AND DSO != '' 
              AND DSO != 'NULL'
            GROUP BY DSO
            ORDER BY DSO
        """)
        
        unique_dsos = cursor.fetchall()
        print(f"Found {len(unique_dsos)} unique DSO values:")
        for dso_name, dentist_count in unique_dsos:
            print(f"  - '{dso_name}' ({dentist_count} dentists)")
        
        if not unique_dsos:
            print("ℹ️  No DSO values found in dentists table. Migration not needed.")
            return True
        
        # Step 2: Create DSO records for each unique DSO string
        print(f"\n🏗️  Step 2: Creating DSO records...")
        
        created_dsos = []
        for dso_name, dentist_count in unique_dsos:
            # Check if DSO already exists
            cursor.execute("SELECT id, name FROM dsos WHERE name = %s", (dso_name,))
            existing_dso = cursor.fetchone()
            
            if existing_dso:
                dso_id, dso_name_db = existing_dso
                print(f"  ✅ DSO '{dso_name}' already exists (ID: {dso_id})")
                created_dsos.append((dso_id, dso_name))
            else:
                # Create new DSO with placeholder data
                email = f"info@{dso_name.lower().replace(' ', '').replace('-', '')}.com"
                now = datetime.now()
                
                cursor.execute("""
                    INSERT INTO dsos (name, email, contact_person, telephone, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (dso_name, email, "Admin", "000-000-0000", "active", now, now))
                
                dso_id = cursor.lastrowid
                created_dsos.append((dso_id, dso_name))
                print(f"  ✨ Created DSO '{dso_name}' (ID: {dso_id})")
        
        print(f"✅ Successfully created/verified {len(created_dsos)} DSO records")
        
        # Step 3: Create associations between dentists and DSOs
        print(f"\n🔗 Step 3: Creating dentist-DSO associations...")
        
        associations_created = 0
        for dso_id, dso_name in created_dsos:
            # Find all dentists with this DSO string
            cursor.execute("""
                SELECT id, name FROM dentists 
                WHERE DSO = %s
            """, (dso_name,))
            
            dentists_with_dso = cursor.fetchall()
            
            for dentist_id, dentist_name in dentists_with_dso:
                # Check if association already exists
                cursor.execute("""
                    SELECT 1 FROM dentist_dso_association 
                    WHERE dentist_id = %s AND dso_id = %s
                """, (dentist_id, dso_id))
                
                if not cursor.fetchone():
                    # Create association
                    cursor.execute("""
                        INSERT INTO dentist_dso_association (dentist_id, dso_id)
                        VALUES (%s, %s)
                    """, (dentist_id, dso_id))
                    
                    associations_created += 1
                    print(f"  🔗 Associated dentist '{dentist_name}' (ID: {dentist_id}) with DSO '{dso_name}' (ID: {dso_id})")
        
        print(f"✅ Successfully created {associations_created} dentist-DSO associations")
        
        # Commit all changes
        connection.commit()
        
        # Step 4: Verify the results
        print(f"\n📈 Step 4: Verification Summary...")
        
        # Show DSO summary
        cursor.execute("""
            SELECT d.id, d.name, COUNT(da.dentist_id) as dentist_count
            FROM dsos d
            LEFT JOIN dentist_dso_association da ON da.dso_id = d.id
            GROUP BY d.id, d.name
            ORDER BY d.name
        """)
        
        dso_summary = cursor.fetchall()
        print(f"\n📋 DSO Summary ({len(dso_summary)} total DSOs):")
        for dso_id, dso_name, dentist_count in dso_summary:
            print(f"  - DSO '{dso_name}' (ID: {dso_id}) - {dentist_count} dentists")
        
        # Show dentists summary
        cursor.execute("SELECT COUNT(*) FROM dentists")
        total_dentists = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(DISTINCT dentist_id) 
            FROM dentist_dso_association
        """)
        dentists_with_associations = cursor.fetchone()[0]
        
        print(f"\n👥 Dentist Summary:")
        print(f"  - Total dentists: {total_dentists}")
        print(f"  - Dentists with DSO associations: {dentists_with_associations}")
        print(f"  - Dentists without DSO associations: {total_dentists - dentists_with_associations}")
        
        print(f"\n🎉 Migration completed successfully!")
        return True
        
    except Error as e:
        print(f"❌ Error during migration: {e}")
        connection.rollback()
        return False
        
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def main():
    """Main function to run the migration"""
    print("🔧 Standalone DSO Migration Script")
    print("=" * 50)
    
    # Show database configuration being used
    print("📁 Database Configuration:")
    print(f"  - Host: {DB_CONFIG['host']}")
    print(f"  - Port: {DB_CONFIG['port']}")
    print(f"  - Database: {DB_CONFIG['database']}")
    print(f"  - User: {DB_CONFIG['user']}")
    print(f"  - Password: {'***' if DB_CONFIG['password'] else 'Not set'}")
    print()
    
    success = migrate_dso_data()
    
    if success:
        print("\n✅ Migration completed successfully!")
        print("\nNext steps:")
        print("1. Review the created DSO records and update contact information as needed")
        print("2. Run the final migration to drop the old DSO column")
        print("\nTo update DSO contact info, you can run SQL like:")
        print("UPDATE dsos SET email='real@email.com', contact_person='Real Name', telephone='123-456-7890' WHERE name='DSO_NAME';")
        return 0
    else:
        print("\n❌ Migration failed!")
        return 1

if __name__ == "__main__":
    exit(main()) 
#!/usr/bin/env python3
"""
Simple migration script to add new columns to the existing patientcomments table
Run this script to extend the comments table with titration and numeric value support
"""

import os
import sys
from sqlalchemy import create_engine, text

def run_migration():
    """Add new columns to the patientcomments table"""
    
    # Database connection (adjust these values for your setup)
    DATABASE_URL = os.getenv('DATABASE_URL', 'mysql+pymysql://username:password@localhost/vizbriz')
    
    try:
        # Create database engine
        engine = create_engine(DATABASE_URL)
        
        with engine.connect() as connection:
            # Start transaction
            trans = connection.begin()
            
            try:
                # Check if columns already exist
                result = connection.execute(text("""
                    SELECT COLUMN_NAME 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_NAME = 'patientcomments' 
                    AND TABLE_SCHEMA = DATABASE()
                    AND COLUMN_NAME IN ('comment_type', 'numeric_value', 'numeric_unit', 'is_urgent', 'is_internal')
                """))
                
                existing_columns = [row[0] for row in result]
                print(f"Existing columns: {existing_columns}")
                
                # Add columns that don't exist
                columns_to_add = []
                
                if 'comment_type' not in existing_columns:
                    columns_to_add.append("ADD COLUMN comment_type VARCHAR(50) DEFAULT 'general'")
                
                if 'numeric_value' not in existing_columns:
                    columns_to_add.append("ADD COLUMN numeric_value DECIMAL(10,2) NULL")
                
                if 'numeric_unit' not in existing_columns:
                    columns_to_add.append("ADD COLUMN numeric_unit VARCHAR(20) NULL")
                
                if 'is_urgent' not in existing_columns:
                    columns_to_add.append("ADD COLUMN is_urgent BOOLEAN DEFAULT FALSE")
                
                if 'is_internal' not in existing_columns:
                    columns_to_add.append("ADD COLUMN is_internal BOOLEAN DEFAULT FALSE")
                
                if columns_to_add:
                    # Execute ALTER TABLE statement
                    alter_sql = f"ALTER TABLE patientcomments {', '.join(columns_to_add)}"
                    print(f"Executing: {alter_sql}")
                    connection.execute(text(alter_sql))
                    
                    print("✅ Successfully added new columns to patientcomments table!")
                    print("Added columns:", [col.split()[2] for col in columns_to_add])
                else:
                    print("✅ All columns already exist in patientcomments table!")
                
                # Commit transaction
                trans.commit()
                
            except Exception as e:
                # Rollback on error
                trans.rollback()
                print(f"❌ Error during migration: {e}")
                raise
                
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        print("\nPlease check your database connection settings:")
        print("1. Make sure your database server is running")
        print("2. Verify the DATABASE_URL environment variable")
        print("3. Ensure you have the necessary permissions")
        return False
    
    return True

def verify_migration():
    """Verify that the migration was successful"""
    DATABASE_URL = os.getenv('DATABASE_URL', 'mysql+pymysql://username:password@localhost/vizbriz')
    
    try:
        engine = create_engine(DATABASE_URL)
        
        with engine.connect() as connection:
            result = connection.execute(text("""
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'patientcomments' 
                AND TABLE_SCHEMA = DATABASE()
                AND COLUMN_NAME IN ('comment_type', 'numeric_value', 'numeric_unit', 'is_urgent', 'is_internal')
                ORDER BY ORDINAL_POSITION
            """))
            
            print("\n📋 Verification - New columns in patientcomments table:")
            print("-" * 60)
            for row in result:
                print(f"  {row[0]:<15} | {row[1]:<15} | Nullable: {row[2]:<3} | Default: {row[3] or 'None'}")
            print("-" * 60)
            
    except Exception as e:
        print(f"❌ Verification error: {e}")

if __name__ == "__main__":
    print("🚀 Starting patientcomments table migration...")
    print("=" * 50)
    
    if run_migration():
        verify_migration()
        print("\n✅ Migration completed successfully!")
        print("\nNext steps:")
        print("1. Update your PatientComment model in models.py with the new fields")
        print("2. Test the new comment functionality in your application")
        print("3. The new tab should now be able to store and display typed comments with numeric values")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)

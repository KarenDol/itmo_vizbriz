#!/usr/bin/env python3
"""
Migration script to add morning aligner and advancement fields to patient_device_order table
Date: 2025-01-07
Description: Extends the patient_device_order table with new fields for morning aligner tracking and device advancement configuration
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_app import create_app, db
from sqlalchemy import text

def run_migration():
    """Run the migration to add new fields to patient_device_order table"""
    app = create_app()
    
    with app.app_context():
        try:
            print("Starting migration: Add device order fields...")
            
            # Check if columns already exist
            result = db.session.execute(text("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'patient_device_order' 
                AND COLUMN_NAME IN ('morning_aligner_used', 'morning_aligner_type', 'advancement')
            """))
            existing_columns = [row[0] for row in result.fetchall()]
            
            if 'morning_aligner_used' not in existing_columns:
                print("Adding morning_aligner_used column...")
                db.session.execute(text("""
                    ALTER TABLE patient_device_order 
                    ADD COLUMN morning_aligner_used BOOLEAN DEFAULT FALSE
                """))
            else:
                print("morning_aligner_used column already exists")
            
            if 'morning_aligner_type' not in existing_columns:
                print("Adding morning_aligner_type column...")
                db.session.execute(text("""
                    ALTER TABLE patient_device_order 
                    ADD COLUMN morning_aligner_type VARCHAR(50) NULL
                """))
            else:
                print("morning_aligner_type column already exists")
            
            if 'advancement' not in existing_columns:
                print("Adding advancement column...")
                db.session.execute(text("""
                    ALTER TABLE patient_device_order 
                    ADD COLUMN advancement DECIMAL(5,2) NULL
                """))
            else:
                print("advancement column already exists")
            
            # Add indexes for better performance
            print("Adding indexes...")
            try:
                db.session.execute(text("""
                    CREATE INDEX idx_patient_device_order_morning_aligner 
                    ON patient_device_order(morning_aligner_used)
                """))
            except Exception as e:
                if "Duplicate key name" in str(e):
                    print("Index idx_patient_device_order_morning_aligner already exists")
                else:
                    raise e
            
            try:
                db.session.execute(text("""
                    CREATE INDEX idx_patient_device_order_advancement 
                    ON patient_device_order(advancement)
                """))
            except Exception as e:
                if "Duplicate key name" in str(e):
                    print("Index idx_patient_device_order_advancement already exists")
                else:
                    raise e
            
            # Commit the changes
            db.session.commit()
            print("Migration completed successfully!")
            
            # Verify the changes
            print("\nVerifying table structure...")
            result = db.session.execute(text("DESCRIBE patient_device_order"))
            columns = result.fetchall()
            
            print("\nCurrent patient_device_order table structure:")
            for column in columns:
                print(f"  {column[0]} - {column[1]} - {column[2]} - {column[3]} - {column[4]} - {column[5]}")
            
        except Exception as e:
            print(f"Migration failed: {str(e)}")
            db.session.rollback()
            raise e

if __name__ == "__main__":
    run_migration()

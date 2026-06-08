#!/usr/bin/env python3
"""
Migration script to create Level 4 device design tables
Creates l4_device_design and l4_device_options tables
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.extensions import db
from flask_app.models import L4DeviceDesign, L4DeviceOption

def create_l4_tables():
    """Create the Level 4 device design tables"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("Creating Level 4 Device Design Tables")
        print("="*80)
        
        try:
            # Check if tables already exist
            inspector = db.inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            if 'l4_device_design' in existing_tables:
                print("⚠ Table 'l4_device_design' already exists")
                response = input("Do you want to drop and recreate it? (yes/no): ")
                if response.lower() == 'yes':
                    print("Dropping existing table...")
                    L4DeviceDesign.__table__.drop(db.engine, checkfirst=True)
                else:
                    print("Skipping table creation")
                    return
            
            if 'l4_device_options' in existing_tables:
                print("⚠ Table 'l4_device_options' already exists")
                response = input("Do you want to drop and recreate it? (yes/no): ")
                if response.lower() == 'yes':
                    print("Dropping existing table...")
                    L4DeviceOption.__table__.drop(db.engine, checkfirst=True)
                else:
                    print("Skipping table creation")
                    return
            
            # Create tables
            print("\nCreating table: l4_device_design")
            L4DeviceDesign.__table__.create(db.engine, checkfirst=True)
            print("✓ Created l4_device_design")
            
            print("\nCreating table: l4_device_options")
            L4DeviceOption.__table__.create(db.engine, checkfirst=True)
            print("✓ Created l4_device_options")
            
            # Verify tables were created
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'l4_device_design' in tables and 'l4_device_options' in tables:
                print("\n" + "="*80)
                print("✓ SUCCESS: Both tables created successfully!")
                print("="*80)
                
                # Show table structure
                print("\nTable: l4_device_design")
                print("-" * 80)
                design_columns = inspector.get_columns('l4_device_design')
                for col in design_columns:
                    print(f"  {col['name']:30} {str(col['type']):30} nullable={col['nullable']}")
                
                print("\nTable: l4_device_options")
                print("-" * 80)
                options_columns = inspector.get_columns('l4_device_options')
                for col in options_columns:
                    print(f"  {col['name']:30} {str(col['type']):30} nullable={col['nullable']}")
                
                # Check for unique constraint
                print("\nUnique Constraints:")
                design_constraints = inspector.get_unique_constraints('l4_device_design')
                for constraint in design_constraints:
                    print(f"  {constraint['name']}: {constraint['column_names']}")
                
                # Check for foreign keys
                print("\nForeign Keys:")
                options_fks = inspector.get_foreign_keys('l4_device_options')
                for fk in options_fks:
                    print(f"  {fk['name']}: {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")
                
            else:
                print("\n✗ ERROR: Tables were not created properly")
                print(f"Existing tables: {tables}")
                
        except Exception as e:
            print(f"\n✗ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        return True

if __name__ == "__main__":
    success = create_l4_tables()
    sys.exit(0 if success else 1)

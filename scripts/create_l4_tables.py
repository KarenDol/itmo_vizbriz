#!/usr/bin/env python3
"""
Create Level 4 device design tables in the database
Uses Flask's db.create_all() to create tables from models
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.extensions import db
from flask_app.models import L4DeviceDesign, L4DeviceOption

def create_tables():
    """Create the Level 4 device design tables"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("Creating Level 4 Device Design Tables")
        print("="*80)
        print("\nThis will create:")
        print("  - l4_device_design (Table A)")
        print("  - l4_device_options (Table B)")
        print()
        
        try:
            # Check if tables already exist
            inspector = db.inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            if 'l4_device_design' in existing_tables or 'l4_device_options' in existing_tables:
                print("⚠ Tables already exist!")
                print(f"  l4_device_design exists: {'l4_device_design' in existing_tables}")
                print(f"  l4_device_options exists: {'l4_device_options' in existing_tables}")
                print("\nNote: db.create_all() will not drop existing tables.")
                print("If you need to recreate them, drop them first manually.")
                response = input("\nContinue anyway? (yes/no): ")
                if response.lower() != 'yes':
                    print("Cancelled.")
                    return False
            
            # Create all tables (only creates if they don't exist)
            print("\nCreating tables...")
            db.create_all()
            
            # Verify tables were created
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'l4_device_design' in tables and 'l4_device_options' in tables:
                print("\n✓ SUCCESS: Both tables created successfully!")
                print("="*80)
                
                # Show table structure
                print("\nTable: l4_device_design")
                print("-" * 80)
                design_columns = inspector.get_columns('l4_device_design')
                for col in design_columns:
                    nullable = "NULL" if col['nullable'] else "NOT NULL"
                    default = f" DEFAULT {col.get('default')}" if col.get('default') is not None else ""
                    print(f"  {col['name']:30} {str(col['type']):30} {nullable}{default}")
                
                print("\nTable: l4_device_options")
                print("-" * 80)
                options_columns = inspector.get_columns('l4_device_options')
                for col in options_columns:
                    nullable = "NULL" if col['nullable'] else "NOT NULL"
                    default = f" DEFAULT {col.get('default')}" if col.get('default') is not None else ""
                    print(f"  {col['name']:30} {str(col['type']):30} {nullable}{default}")
                
                # Check for unique constraint
                print("\nUnique Constraints:")
                try:
                    design_constraints = inspector.get_unique_constraints('l4_device_design')
                    for constraint in design_constraints:
                        print(f"  {constraint['name']}: {constraint['column_names']}")
                except Exception as e:
                    print(f"  (Could not retrieve constraints: {e})")
                
                # Check for foreign keys
                print("\nForeign Keys:")
                try:
                    options_fks = inspector.get_foreign_keys('l4_device_options')
                    for fk in options_fks:
                        print(f"  {fk['name']}: {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")
                except Exception as e:
                    print(f"  (Could not retrieve foreign keys: {e})")
                
                print("\n" + "="*80)
                print("Tables are ready to use!")
                print("="*80)
                
            else:
                print("\n✗ ERROR: Tables were not created properly")
                print(f"Existing tables: {tables}")
                if 'l4_device_design' not in tables:
                    print("  Missing: l4_device_design")
                if 'l4_device_options' not in tables:
                    print("  Missing: l4_device_options")
                return False
                
        except Exception as e:
            print(f"\n✗ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        return True

if __name__ == "__main__":
    success = create_tables()
    sys.exit(0 if success else 1)

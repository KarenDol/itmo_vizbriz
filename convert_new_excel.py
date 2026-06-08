#!/usr/bin/env python3
"""
Convert the new VizBriz Excel file to CSV for analysis
"""

import sys
import os

# Add the vizbriz directory to the path to use existing modules
sys.path.append('/home/ec2-user/vizbriz')

try:
    import pandas as pd
    
    # Read the Excel file
    excel_file = '/home/ec2-user/requirements/VizBriz_Scoring_and_RedFlags_WITH_DiagnosedSSI.xlsx'
    
    # Get all sheet names
    xl = pd.ExcelFile(excel_file)
    print('Available sheets:', xl.sheet_names)
    
    # Create output directory
    os.makedirs('/home/ec2-user/requirements/csv_export_new', exist_ok=True)
    
    # Convert each sheet to CSV
    for sheet_name in xl.sheet_names:
        df = pd.read_excel(excel_file, sheet_name=sheet_name)
        csv_path = f'/home/ec2-user/requirements/csv_export_new/{sheet_name}.csv'
        df.to_csv(csv_path, index=False)
        print(f'Converted {sheet_name} to CSV: {len(df)} rows, {len(df.columns)} columns')
        
        # Show first few rows for each sheet
        print(f'First 3 rows of {sheet_name}:')
        print(df.head(3))
        print('---')
        
except ImportError as e:
    print(f"Error: {e}")
    print("Please install pandas: pip install pandas openpyxl")
except Exception as e:
    print(f"Error processing Excel file: {e}")

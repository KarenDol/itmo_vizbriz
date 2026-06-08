"""
Convert VizBriz Excel files to CSV for easier processing
"""

import pandas as pd
from pathlib import Path

# Paths
requirements_dir = Path('/home/ec2-user/requirements')
output_dir = requirements_dir / 'csv_export'
output_dir.mkdir(exist_ok=True)

# Excel files to convert
excel_files = {
    'questions_spec': 'VizBriz_Questionnaire_Dev_Spec_v9 - final.xlsx',
    'questions_package': 'VizBriz_Questionnaire_Dev_Package_Updated.xlsx',
    'messaging_matrix': 'VizBriz_Outcome_Messaging_Matrix_v4.xlsx',
    'messaging_bilingual': 'VizBriz_Outcome_Messaging_Bilingual_v1.xlsx',
    'messaging_bilingual_ru': 'VizBriz_Outcome_Messaging_Bilingual_RU_v1.xlsx'
}

print("=" * 80)
print("Converting Excel files to CSV")
print("=" * 80)

for file_key, filename in excel_files.items():
    file_path = requirements_dir / filename
    
    if not file_path.exists():
        print(f"\n❌ Not found: {filename}")
        continue
    
    print(f"\n📄 Processing: {filename}")
    
    try:
        # Read all sheets
        excel_file = pd.ExcelFile(file_path)
        
        for sheet_name in excel_file.sheet_names:
            # Read sheet
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            
            # Create CSV filename
            csv_filename = f"{file_key}_{sheet_name.replace(' ', '_')}.csv"
            csv_path = output_dir / csv_filename
            
            # Save to CSV
            df.to_csv(csv_path, index=False, encoding='utf-8')
            
            print(f"   ✅ Exported: {csv_filename} ({len(df)} rows, {len(df.columns)} columns)")
    
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")

print("\n" + "=" * 80)
print(f"✅ CSV files saved to: {output_dir}")
print("=" * 80)


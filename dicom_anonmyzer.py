from flask import Flask, request, send_file
import zipfile
import pydicom
import io
import os
import tempfile
import shutil
from datetime import datetime

app = Flask(__name__)

def anonymize_dataset(dataset):
    """Anonymize a DICOM dataset by removing patient information."""
    # Remove patient identifying information
    dataset.PatientName = "Anonymous"
    dataset.PatientID = "ID" + datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Clear other identifying fields
    if "PatientBirthDate" in dataset:
        dataset.PatientBirthDate = ""
    if "PatientAddress" in dataset:
        dataset.PatientAddress = ""
    
    # Remove private tags
    dataset.remove_private_tags()
    
    # Handle other optional fields
    for tag in ["OtherPatientIDs", "OtherPatientNames"]:
        if tag in dataset:
            delattr(dataset, tag)
    
    return dataset

@app.route('/anonymize', methods=['POST'])
def anonymize_dicom_zip():
    if 'file' not in request.files:
        return "No file part", 400
    
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    
    # Create temporary directories
    temp_dir = tempfile.mkdtemp()
    anon_dir = tempfile.mkdtemp()
    
    try:
        # Extract the uploaded zip file
        zip_path = os.path.join(temp_dir, "original.zip")
        file.save(zip_path)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Process each DICOM file
        for root, _, files in os.walk(temp_dir):
            for filename in files:
                if filename.endswith('.dcm'):
                    file_path = os.path.join(root, filename)
                    try:
                        # Read, anonymize and save the DICOM file
                        ds = pydicom.dcmread(file_path)
                        ds = anonymize_dataset(ds)
                        
                        # Create relative path for saving
                        rel_path = os.path.relpath(file_path, temp_dir)
                        anon_path = os.path.join(anon_dir, rel_path)
                        
                        # Ensure directory exists
                        os.makedirs(os.path.dirname(anon_path), exist_ok=True)
                        ds.save_as(anon_path)
                    except:
                        # Skip files that aren't valid DICOM
                        continue
        
        # Create a new zip file with anonymized DICOM files
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for root, _, files in os.walk(anon_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, anon_dir)
                    zf.write(file_path, arcname=arcname)
        
        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'anonymized_dicom_{datetime.now().strftime("%Y%m%d%H%M%S")}.zip'
        )
    
    finally:
        # Clean up temporary directories
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(anon_dir, ignore_errors=True)

if __name__ == '__main__':
    app.run(debug=True)

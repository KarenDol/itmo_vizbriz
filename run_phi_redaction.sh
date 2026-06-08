#!/bin/bash

# PHI Redaction Script Runner
# This script sets up and runs the PHI redaction process

set -e  # Exit on any error

echo "PHI Redaction Script Runner"
echo "=========================="

# Check if we're in the right directory
if [ ! -f "phi_redaction_script.py" ]; then
    echo "Error: phi_redaction_script.py not found in current directory"
    echo "Please run this script from the vizbriz directory"
    exit 1
fi

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Warning: No virtual environment detected"
    echo "It's recommended to run this in a virtual environment"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Exiting. Please activate your virtual environment first."
        exit 1
    fi
fi

# Check AWS credentials
echo "Checking AWS credentials..."
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "Error: AWS credentials not configured or invalid"
    echo "Please run 'aws configure' or set AWS environment variables"
    exit 1
fi
echo "✓ AWS credentials are valid"

# Check if dependencies are installed
echo "Checking dependencies..."
if ! python -c "import presidio_analyzer, presidio_anonymizer, boto3, pdfplumber, docx" > /dev/null 2>&1; then
    echo "Dependencies not found. Installing..."
    python setup_phi_redaction.py
    if [ $? -ne 0 ]; then
        echo "Error: Failed to install dependencies"
        exit 1
    fi
else
    echo "✓ Dependencies are installed"
fi

# Run test suite
echo "Running test suite..."
python test_phi_redaction.py
if [ $? -ne 0 ]; then
    echo "Error: Test suite failed"
    echo "Please fix the issues before running the main script"
    exit 1
fi

# Confirm before running
echo ""
echo "Ready to run PHI redaction on s3://vizbrizknowledgebase"
echo "This will:"
echo "  - Scan all files in the bucket"
echo "  - Detect and redact PHI information"
echo "  - Save redacted files to s3://vizbrizknowledgebase/redacted/"
echo "  - Create detailed logs in phi_redaction.log"
echo ""
read -p "Continue with PHI redaction? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "PHI redaction cancelled"
    exit 0
fi

# Run the main script
echo "Starting PHI redaction process..."
echo "Logs will be written to phi_redaction.log"
echo ""

python phi_redaction_script.py

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ PHI redaction completed successfully!"
    echo "Check phi_redaction.log for detailed results"
else
    echo ""
    echo "✗ PHI redaction failed"
    echo "Check phi_redaction.log for error details"
    exit 1
fi

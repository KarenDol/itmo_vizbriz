#!/bin/bash
# Simple script to run the case-card generator

echo "Case-Card Generator Setup"
echo "========================="

# Check if we're in the right directory
if [ ! -f "case_card_generator.py" ]; then
    echo "Error: case_card_generator.py not found. Please run from the vizbriz directory."
    exit 1
fi

# Set environment variables (modify these as needed)
export SOURCE_BUCKET="vizbrizknowledgebase"
export RESEARCH_BUCKET="vizbrizknowledgebase"  # Same bucket, different folder
export HMAC_SECRET="your-secret-key-here-change-this"
export MYSQL_HOST="localhost"
export MYSQL_PORT="3306"
export MYSQL_DATABASE="vizbriz"
export MYSQL_USER="root"
export MYSQL_PASSWORD=""

echo "Configuration:"
echo "  Source Bucket: $SOURCE_BUCKET"
echo "  Research Bucket: $RESEARCH_BUCKET"
echo "  MySQL Host: $MYSQL_HOST"
echo "  MySQL Database: $MYSQL_DATABASE"
echo ""

# Check if virtual environment exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
else
    echo "No virtual environment found. Using system Python."
fi

# Install requirements
echo "Installing requirements..."
pip install -r case_card_requirements.txt

# Run the generator
echo "Starting case-card generation..."
python3 case_card_generator.py

echo "Case-card generation complete!"
echo "Check case_card_generator.log for detailed logs."

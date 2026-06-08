FROM python:3.11

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    unrar \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install additional dependencies that might be missing
RUN pip install --no-cache-dir \
    fpdf \
    pytesseract \
    pdf2image \
    pdfplumber \
    rarfile \
    pydicom \
    trimesh \
    PyPDF2>=2.0.0 \
    flask-mail \
    flask-migrate \
    pdfrw

# Copy the application code
COPY . .

# Set environment variables
ENV FLASK_APP=run.py
ENV FLASK_ENV=development
ENV PYTHONPATH=/app

# Expose the port the app will run on
EXPOSE 7000

# Command to run the application
CMD ["python", "run.py"] 
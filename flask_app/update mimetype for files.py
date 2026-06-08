from mimetypes import guess_type
from datetime import datetime
from your_app import db
from your_app.models import File

# Iterate through all records in the Files table
for file in File.query.all():
    if not file.file_type or 'application/octet-stream' in file.file_type:
        # Guess the MIME type based on the file name
        mime_type, _ = guess_type(file.name)
        if mime_type:
            file.file_type = mime_type
            db.session.add(file)
            print(f"Updated MIME type for file {file.name} to {mime_type}")
        else:
            print(f"Could not determine MIME type for {file.name}")

# Commit changes to the database
db.session.commit()
print("Database records updated successfully.")

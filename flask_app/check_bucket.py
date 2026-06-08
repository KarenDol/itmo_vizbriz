import os

s3_bucket = os.getenv('S3_BUCKET_NAME')
if s3_bucket:
    print(f"S3_BUCKET_NAME: {s3_bucket}")
else:
    print("S3_BUCKET_NAME environment variable not found.")
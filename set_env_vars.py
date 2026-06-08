import os

# Print the current environment variable
current_bucket = os.getenv('S3_BUCKET_NAME')
print(f"Current S3_BUCKET_NAME: {current_bucket}")

# Set the correct environment variable
os.environ['S3_BUCKET_NAME'] = 'sharkbiitpatientdataai'
print(f"New S3_BUCKET_NAME: {os.environ['S3_BUCKET_NAME']}")

# Verify other important variables
print("\nOther environment variables:")
print(f"AWS_ACCESS_KEY_ID: {os.getenv('AWS_ACCESS_KEY_ID')}")
print(f"AWS_REGION: {os.getenv('AWS_REGION')}")
print(f"DB_HOST: {os.getenv('DB_HOST')}")
print(f"DB_NAME: {os.getenv('DB_NAME')}") 
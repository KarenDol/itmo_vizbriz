import boto3

try:
    s3 = boto3.client('s3')
    buckets = s3.list_buckets()
    print("Buckets:", buckets)
except Exception as e:
    print("Error connecting to S3:", e)
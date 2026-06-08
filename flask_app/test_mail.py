import boto3
import os

# The IAM role automatically provides credentials
region = os.environ.get('AWS_REGION', 'us-west-2')
ses_client = boto3.client('ses', region_name=region)

def send_email(to_address, subject, body):
    """
    Sends an email using AWS SES.
    """
    sender_email = "eran@sharkbiit.com"  # Replace with your verified sender email
    try:
        response = ses_client.send_email(
            Source=sender_email,
            Destination={'ToAddresses': [to_address]},  # The recipient email
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}},
            }
        )
        print(f"Email sent! Message ID: {response['MessageId']}")
    except Exception as e:
        print(f"Error sending email: {str(e)}")

# Call the function to send the email
send_email(
    to_address="eran@sharkbiit.com",  # Replace with the recipient's email address
    subject="Test Email from AWS SES",
    body="This is a test email sent using AWS SES."
)

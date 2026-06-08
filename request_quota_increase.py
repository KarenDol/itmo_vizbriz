#!/usr/bin/env python3
"""
AWS Bedrock Quota Increase Request Script
Helps request quota increases for Bedrock service
"""

import boto3
import json
import sys
from botocore.exceptions import ClientError

def list_bedrock_quotas(region="us-east-1"):
    """List current Bedrock quotas"""
    try:
        service_quotas = boto3.client('service-quotas', region_name=region)
        
        print("🔍 Current Bedrock Quotas:")
        print("=" * 50)
        
        # List all Bedrock quotas
        paginator = service_quotas.get_paginator('list_service_quotas')
        page_iterator = paginator.paginate(ServiceCode='bedrock')
        
        for page in page_iterator:
            for quota in page['Quotas']:
                print(f"Quota Name: {quota['QuotaName']}")
                print(f"Current Value: {quota['Value']}")
                print(f"Adjustable: {quota['Adjustable']}")
                print(f"Quota Code: {quota['QuotaCode']}")
                print("-" * 30)
                
    except ClientError as e:
        print(f"Error listing quotas: {e}")
        return False
    
    return True

def request_quota_increase(quota_code, desired_value, region="us-east-1"):
    """Request a quota increase"""
    try:
        service_quotas = boto3.client('service-quotas', region_name=region)
        
        # Get current quota value first
        current_quota = service_quotas.get_service_quota(
            ServiceCode='bedrock',
            QuotaCode=quota_code
        )
        
        current_value = current_quota['Quota']['Value']
        
        print(f"📊 Requesting quota increase:")
        print(f"   Quota Code: {quota_code}")
        print(f"   Current Value: {current_value}")
        print(f"   Desired Value: {desired_value}")
        
        # Request the increase
        response = service_quotas.request_service_quota_increase(
            ServiceCode='bedrock',
            QuotaCode=quota_code,
            DesiredValue=desired_value
        )
        
        print(f"✅ Quota increase request submitted!")
        print(f"   Request ID: {response['RequestedQuota']['Id']}")
        print(f"   Status: {response['RequestedQuota']['Status']}")
        
        return True
        
    except ClientError as e:
        print(f"❌ Error requesting quota increase: {e}")
        return False

def main():
    print("🚀 AWS Bedrock Quota Management")
    print("=" * 40)
    
    # First, list current quotas
    if not list_bedrock_quotas():
        print("Failed to list quotas. Check your AWS credentials and permissions.")
        return
    
    print("\n💡 Common Bedrock Quotas to Increase:")
    print("1. InvokeModel requests per second")
    print("2. InvokeModel requests per minute") 
    print("3. InvokeModel requests per hour")
    print("4. Concurrent requests")
    
    print("\n📝 To request an increase manually:")
    print("1. Go to AWS Console → Service Quotas → Amazon Bedrock")
    print("2. Find the quota you want to increase")
    print("3. Click 'Request quota increase'")
    print("4. Enter desired value and business justification")
    
    print("\n🔧 Or use AWS CLI:")
    print("aws service-quotas request-service-quota-increase \\")
    print("  --service-code bedrock \\")
    print("  --quota-code <QUOTA_CODE> \\")
    print("  --desired-value <NEW_VALUE> \\")
    print("  --region us-east-1")

if __name__ == "__main__":
    main()







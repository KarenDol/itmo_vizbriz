#!/usr/bin/env python3
"""
Simple test to check if Bedrock endpoints are working
"""

import requests
import json

def test_bedrock_endpoints():
    """Test Bedrock endpoints"""
    print("🔍 Testing Bedrock Endpoints")
    print("=" * 30)
    
    base_url = "http://13.58.61.189:7000"
    
    # Test 1: Bedrock test endpoint
    print("1. Testing /bedrock/test...")
    try:
        response = requests.get(f"{base_url}/bedrock/test", timeout=10)
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Success: {data.get('message', 'No message')}")
            print(f"   ✅ Knowledge Base: {data.get('knowledge_base_name', 'Unknown')}")
        else:
            print(f"   ❌ Failed: {response.text}")
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
    
    # Test 2: Bedrock query endpoint
    print("\n2. Testing /bedrock/query...")
    try:
        response = requests.post(
            f"{base_url}/bedrock/query",
            json={"query": "What is sleep apnea?"},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                print(f"   ✅ Success: {data.get('response', 'No response')[:100]}...")
                print(f"   ✅ Citations: {len(data.get('citations', []))}")
            else:
                print(f"   ❌ Failed: {data.get('message', 'Unknown error')}")
        else:
            print(f"   ❌ HTTP Error: {response.text}")
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
    
    print("\n" + "=" * 30)
    print("🎯 NEXT STEPS:")
    print("=" * 30)
    print("""
    If Bedrock endpoints return 404:
    1. Restart your Flask app
    2. The Bedrock blueprint needs to be registered
    
    If Bedrock endpoints work:
    ✅ Your Bedrock integration is working!
    ✅ You can now integrate it with your chatbot
    """)

if __name__ == "__main__":
    test_bedrock_endpoints()
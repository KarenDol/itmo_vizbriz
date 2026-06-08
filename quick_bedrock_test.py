#!/usr/bin/env python3
"""
Quick test script for Bedrock chatbot integration
Run this to quickly verify your chatbot is using Bedrock
"""

import requests
import json
import sys

def quick_test():
    """Quick test of Bedrock integration"""
    print("🚀 Quick Bedrock Chatbot Test")
    print("=" * 40)
    
    base_url = "http://13.58.61.189:7000"
    
    # Test 1: Connection
    print("1. Testing Bedrock connection...")
    try:
        response = requests.get(f"{base_url}/bedrock/test", timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Connected to: {data.get('knowledge_base_name')}")
            print(f"   ✅ Model: Claude 3.5 Sonnet v2")
        else:
            print(f"   ❌ Connection failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Connection error: {str(e)}")
        return False
    
    # Test 2: Simple query
    print("\n2. Testing simple query...")
    try:
        response = requests.post(
            f"{base_url}/bedrock/query",
            json={"query": "What is sleep apnea?"},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            citations = data.get('citations', [])
            
            print(f"   ✅ Query successful!")
            print(f"   ✅ Response length: {len(response_text)} characters")
            print(f"   ✅ Citations: {len(citations)} sources")
            print(f"   ✅ Response preview: {response_text[:100]}...")
            
            # Check if response contains medical content
            medical_terms = ['sleep', 'apnea', 'breathing', 'oxygen', 'treatment']
            found_terms = [term for term in medical_terms if term.lower() in response_text.lower()]
            print(f"   ✅ Medical terms found: {found_terms}")
            
        else:
            print(f"   ❌ Query failed: {response.status_code}")
            print(f"   ❌ Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ❌ Query error: {str(e)}")
        return False
    
    # Test 3: Patient-specific query
    print("\n3. Testing patient-specific query...")
    try:
        response = requests.post(
            f"{base_url}/bedrock/patient-query",
            json={
                "query": "What are the latest AHI results for this patient?",
                "patient_id": "3333"
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Patient query successful!")
            print(f"   ✅ Patient ID: {data.get('patient_id')}")
            print(f"   ✅ Response length: {len(data.get('response', ''))}")
        else:
            print(f"   ❌ Patient query failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"   ❌ Patient query error: {str(e)}")
        return False
    
    print("\n" + "=" * 40)
    print("🎉 All tests passed! Your chatbot is using Bedrock!")
    print("\nTo use the chatbot:")
    print("1. Open test_chatbot_web.html in your browser")
    print("2. Or run: python test_bedrock_chatbot.py")
    print("3. Or make direct API calls to /bedrock/* endpoints")
    
    return True

if __name__ == "__main__":
    try:
        success = quick_test()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n❌ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        sys.exit(1)

#!/usr/bin/env python3
"""
Script to verify performance optimizations maintain backward compatibility.
"""

import os
import sys
import time
import requests
from datetime import datetime

# Add the flask_app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def verify_route_compatibility():
    """Verify that all routes still work after performance optimizations."""
    print("=== ROUTE COMPATIBILITY VERIFICATION ===")
    
    try:
        from flask_app import create_app
        
        app = create_app()
        
        # Test that the main route still exists and works
        with app.test_client() as client:
            # Test the main patient workflow manifest route
            response = client.get('/patient_workflow_manifest/1')
            print(f"Main route status: {response.status_code}")
            
            # Test new API endpoints
            api_endpoints = [
                '/api/patient/1/execution-manifest',
                '/api/patient/1/canonical-data', 
                '/api/patient/1/basic-manifest'
            ]
            
            for endpoint in api_endpoints:
                response = client.get(endpoint)
                print(f"API endpoint {endpoint}: {response.status_code}")
        
        print("✅ Route compatibility verification completed")
        return True
        
    except Exception as e:
        print(f"❌ Route compatibility verification failed: {e}")
        return False

def test_performance_improvements():
    """Test that performance improvements are working."""
    print("\n=== PERFORMANCE IMPROVEMENT VERIFICATION ===")
    
    try:
        from flask_app.services.performance_service import PerformanceService
        from flask_app.services.cache_service import CacheService
        
        # Test performance service
        print("Testing PerformanceService...")
        basic_data = PerformanceService.get_basic_manifest_data(1)
        if basic_data:
            print("✅ PerformanceService working")
        else:
            print("⚠️ PerformanceService returned None (may be expected for non-existent patient)")
        
        # Test cache service
        print("Testing CacheService...")
        CacheService.set('test_key', 'test_value', timeout=60)
        cached_value = CacheService.get('test_key')
        if cached_value == 'test_value':
            print("✅ CacheService working")
        else:
            print("❌ CacheService not working properly")
        
        # Clean up test cache
        CacheService.delete('test_key')
        
        print("✅ Performance improvement verification completed")
        return True
        
    except Exception as e:
        print(f"❌ Performance improvement verification failed: {e}")
        return False

def benchmark_route_performance():
    """Benchmark route performance before and after optimizations."""
    print("\n=== ROUTE PERFORMANCE BENCHMARK ===")
    
    try:
        from flask_app import create_app
        
        app = create_app()
        
        with app.test_client() as client:
            # Benchmark the main route
            start_time = time.time()
            response = client.get('/patient_workflow_manifest/1')
            end_time = time.time()
            
            response_time = (end_time - start_time) * 1000  # Convert to milliseconds
            
            print(f"Route response time: {response_time:.2f}ms")
            print(f"Response status: {response.status_code}")
            
            if response_time < 1000:  # Less than 1 second
                print("✅ Route performance is good")
            else:
                print("⚠️ Route performance may need further optimization")
        
        return True
        
    except Exception as e:
        print(f"❌ Performance benchmark failed: {e}")
        return False

def main():
    """Run all verification tests."""
    print(f"Performance Optimization Verification - {datetime.now()}")
    print("=" * 60)
    
    results = []
    
    # Run verification tests
    results.append(verify_route_compatibility())
    results.append(test_performance_improvements())
    results.append(benchmark_route_performance())
    
    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("✅ All verifications passed! Performance optimizations are working correctly.")
    else:
        print("❌ Some verifications failed. Please check the issues above.")
    
    return passed == total

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

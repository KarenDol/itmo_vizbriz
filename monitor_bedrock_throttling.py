#!/usr/bin/env python3
"""
Bedrock Throttling Monitor
Monitor and diagnose AWS Bedrock throttling issues
"""

import boto3
import time
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import argparse
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BedrockMonitor:
    def __init__(self, region="us-east-1"):
        self.region = region
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.stats = defaultdict(int)
        self.errors = []
        self.start_time = time.time()
        
    def test_bedrock_call(self, test_message="Hello, this is a test message"):
        """Test a single Bedrock call"""
        try:
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": [{"role": "user", "content": test_message}],
                "max_tokens": 50,
                "temperature": 0.1,
                "top_p": 0.9,
            }
            
            start_time = time.time()
            response = self.client.invoke_model(
                modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
                contentType="application/json",
                accept="application/json",
                body=json.dumps(payload),
            )
            end_time = time.time()
            
            result = json.loads(response["body"].read())
            success = result.get("content") and len(result["content"]) > 0
            
            if success:
                self.stats["success"] += 1
                self.stats["total_time"] += (end_time - start_time)
                logger.info(f"✅ Success: {end_time - start_time:.2f}s")
            else:
                self.stats["empty_response"] += 1
                logger.warning("⚠️ Empty response")
                
            return True, end_time - start_time
            
        except Exception as e:
            error_str = str(e).lower()
            self.stats["errors"] += 1
            
            if "throttling" in error_str or "too many requests" in error_str:
                self.stats["throttling_errors"] += 1
                logger.error(f"🚫 Throttling Error: {e}")
            elif "quota" in error_str or "limit" in error_str:
                self.stats["quota_errors"] += 1
                logger.error(f"📊 Quota Error: {e}")
            else:
                self.stats["other_errors"] += 1
                logger.error(f"❌ Other Error: {e}")
            
            self.errors.append({
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "type": "throttling" if "throttling" in error_str else "other"
            })
            
            return False, 0
    
    def run_load_test(self, num_requests=10, delay=1.0):
        """Run a load test with specified number of requests"""
        logger.info(f"🚀 Starting load test: {num_requests} requests with {delay}s delay")
        logger.info(f"Region: {self.region}")
        
        for i in range(num_requests):
            logger.info(f"Request {i+1}/{num_requests}")
            success, duration = self.test_bedrock_call()
            
            if not success and self.stats["throttling_errors"] > 0:
                logger.warning("Throttling detected, increasing delay...")
                time.sleep(delay * 2)
            else:
                time.sleep(delay)
        
        self.print_summary()
    
    def print_summary(self):
        """Print test summary"""
        total_requests = self.stats["success"] + self.stats["errors"] + self.stats["empty_response"]
        if total_requests == 0:
            logger.info("No requests made")
            return
        
        success_rate = (self.stats["success"] / total_requests) * 100
        avg_time = self.stats["total_time"] / self.stats["success"] if self.stats["success"] > 0 else 0
        
        logger.info("\n" + "="*50)
        logger.info("📊 BEDROCK LOAD TEST SUMMARY")
        logger.info("="*50)
        logger.info(f"Region: {self.region}")
        logger.info(f"Total Requests: {total_requests}")
        logger.info(f"Successful: {self.stats['success']} ({success_rate:.1f}%)")
        logger.info(f"Throttling Errors: {self.stats['throttling_errors']}")
        logger.info(f"Quota Errors: {self.stats['quota_errors']}")
        logger.info(f"Other Errors: {self.stats['other_errors']}")
        logger.info(f"Empty Responses: {self.stats['empty_response']}")
        logger.info(f"Average Response Time: {avg_time:.2f}s")
        
        if self.errors:
            logger.info("\n🔍 RECENT ERRORS:")
            for error in self.errors[-5:]:  # Show last 5 errors
                logger.info(f"  {error['timestamp']}: {error['error']}")
        
        # Recommendations
        logger.info("\n💡 RECOMMENDATIONS:")
        if self.stats["throttling_errors"] > 0:
            logger.info("  • Throttling detected - consider implementing rate limiting")
            logger.info("  • Increase delays between requests")
            logger.info("  • Check AWS Bedrock quotas in your account")
        
        if success_rate < 80:
            logger.info("  • Low success rate - check network connectivity and credentials")
        
        if avg_time > 5:
            logger.info("  • Slow response times - consider optimizing payload size")
    
    def check_aws_quotas(self):
        """Check AWS service quotas for Bedrock"""
        try:
            service_quotas = boto3.client('service-quotas', region_name=self.region)
            
            # Common Bedrock quotas to check
            quota_names = [
                "InvokeModel requests per second",
                "InvokeModel requests per minute", 
                "InvokeModel requests per hour"
            ]
            
            logger.info("🔍 Checking AWS Service Quotas...")
            
            for quota_name in quota_names:
                try:
                    response = service_quotas.get_service_quota(
                        ServiceCode='bedrock',
                        QuotaCode=quota_name
                    )
                    quota = response['Quota']
                    logger.info(f"  {quota_name}: {quota['Value']}")
                except Exception as e:
                    logger.warning(f"  Could not retrieve quota for {quota_name}: {e}")
                    
        except Exception as e:
            logger.warning(f"Could not check service quotas: {e}")

def main():
    parser = argparse.ArgumentParser(description="Monitor AWS Bedrock throttling issues")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--requests", type=int, default=10, help="Number of test requests")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests (seconds)")
    parser.add_argument("--check-quotas", action="store_true", help="Check AWS service quotas")
    
    args = parser.parse_args()
    
    monitor = BedrockMonitor(args.region)
    
    if args.check_quotas:
        monitor.check_aws_quotas()
        print()
    
    monitor.run_load_test(args.requests, args.delay)

if __name__ == "__main__":
    main()

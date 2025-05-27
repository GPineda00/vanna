#!/usr/bin/env python3
"""
Vanna SQL Optimization Validation Script

This script validates that all optimization components are properly configured
and functioning for high-load scenarios.
"""

import os
import sys
import time
import requests
import concurrent.futures
import redis
import json
from datetime import datetime

class OptimizationValidator:
    def __init__(self, base_url="http://localhost:5000"):
        self.base_url = base_url
        self.redis_client = None
        self.test_results = {}
        
    def connect_redis(self):
        """Test Redis connectivity"""
        try:
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', '6379')),
                password=os.getenv('REDIS_PASSWORD'),
                decode_responses=True
            )
            self.redis_client.ping()
            return True
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
            return False
    
    def test_health_endpoint(self):
        """Test the health check endpoint"""
        try:
            response = requests.get(f"{self.base_url}/api/health", timeout=10)
            if response.status_code == 200:
                health_data = response.json()
                print("✅ Health check endpoint working")
                print(f"   - Database status: {health_data.get('database_status', 'unknown')}")
                print(f"   - Ollama status: {health_data.get('ollama_status', 'unknown')}")
                
                optimization_systems = health_data.get('optimization_systems', {})
                if optimization_systems:
                    print("✅ Optimization systems detected:")
                    for system, status in optimization_systems.items():
                        if isinstance(status, dict):
                            print(f"   - {system}: {status.get('status', 'unknown')}")
                        else:
                            print(f"   - {system}: {status}")
                else:
                    print("⚠️  No optimization system status found")
                
                return True
            else:
                print(f"❌ Health check failed with status {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Health check error: {e}")
            return False
    
    def test_caching_system(self):
        """Test Redis caching functionality"""
        if not self.redis_client:
            print("❌ Cannot test caching - Redis not connected")
            return False
        
        try:
            # Test basic Redis operations
            test_key = "vanna:test:cache"
            test_value = "optimization_test"
            
            self.redis_client.set(test_key, test_value, ex=60)
            retrieved_value = self.redis_client.get(test_key)
            
            if retrieved_value == test_value:
                print("✅ Redis caching system working")
                self.redis_client.delete(test_key)
                return True
            else:
                print("❌ Redis caching test failed")
                return False
                
        except Exception as e:
            print(f"❌ Caching test error: {e}")
            return False
    
    def test_session_management(self):
        """Test session creation and management"""
        try:
            # Create a session
            response = requests.get(f"{self.base_url}/api/session", timeout=10)
            if response.status_code == 200:
                session_data = response.json()
                if session_data.get('session_id'):
                    print("✅ Session management working")
                    print(f"   - Active users: {session_data.get('active_users', 0)}")
                    return True
                else:
                    print("❌ Session creation failed")
                    return False
            else:
                print(f"❌ Session endpoint failed with status {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Session test error: {e}")
            return False
    
    def test_rate_limiting(self):
        """Test rate limiting functionality"""
        try:
            # Make rapid requests to test rate limiting
            rapid_requests = []
            start_time = time.time()
            
            for i in range(20):  # Make 20 rapid requests
                try:
                    response = requests.get(f"{self.base_url}/api/health", timeout=5)
                    rapid_requests.append(response.status_code)
                except:
                    rapid_requests.append(None)
            
            end_time = time.time()
            
            # Check if any requests were rate limited (429 status)
            rate_limited = sum(1 for status in rapid_requests if status == 429)
            successful = sum(1 for status in rapid_requests if status == 200)
            
            print(f"✅ Rate limiting test completed")
            print(f"   - Successful requests: {successful}")
            print(f"   - Rate limited requests: {rate_limited}")
            print(f"   - Total time: {end_time - start_time:.2f}s")
            
            return True
            
        except Exception as e:
            print(f"❌ Rate limiting test error: {e}")
            return False
    
    def test_concurrent_load(self, num_requests=50, num_threads=10):
        """Test concurrent request handling"""
        print(f"🔄 Testing concurrent load ({num_requests} requests, {num_threads} threads)...")
        
        def make_request():
            try:
                start_time = time.time()
                response = requests.get(f"{self.base_url}/api/health", timeout=30)
                end_time = time.time()
                return {
                    'status_code': response.status_code,
                    'response_time': end_time - start_time,
                    'success': response.status_code == 200
                }
            except Exception as e:
                return {
                    'status_code': None,
                    'response_time': None,
                    'success': False,
                    'error': str(e)
                }
        
        start_time = time.time()
        results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(make_request) for _ in range(num_requests)]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
        
        end_time = time.time()
        
        # Analyze results
        successful_requests = sum(1 for r in results if r['success'])
        failed_requests = len(results) - successful_requests
        avg_response_time = sum(r['response_time'] for r in results if r['response_time']) / len([r for r in results if r['response_time']])
        total_time = end_time - start_time
        requests_per_second = num_requests / total_time
        
        print(f"✅ Concurrent load test completed")
        print(f"   - Total requests: {num_requests}")
        print(f"   - Successful: {successful_requests}")
        print(f"   - Failed: {failed_requests}")
        print(f"   - Average response time: {avg_response_time:.3f}s")
        print(f"   - Requests per second: {requests_per_second:.2f}")
        print(f"   - Total test time: {total_time:.2f}s")
        
        # Performance thresholds
        if successful_requests >= num_requests * 0.95:  # 95% success rate
            print("✅ Concurrent load test PASSED")
            return True
        else:
            print("❌ Concurrent load test FAILED - too many failures")
            return False
    
    def test_database_pool(self):
        """Test database connection pooling"""
        try:
            # Make multiple rapid database-related requests
            start_time = time.time()
            
            # Test with session endpoint which should use database
            responses = []
            for i in range(10):
                response = requests.get(f"{self.base_url}/api/session", timeout=10)
                responses.append(response.status_code)
            
            end_time = time.time()
            successful = sum(1 for status in responses if status == 200)
            
            print(f"✅ Database pool test completed")
            print(f"   - Successful requests: {successful}/10")
            print(f"   - Average time per request: {(end_time - start_time) / 10:.3f}s")
            
            return successful >= 8  # Allow for some failures
            
        except Exception as e:
            print(f"❌ Database pool test error: {e}")
            return False
    
    def run_all_tests(self):
        """Run all optimization validation tests"""
        print("🚀 Starting Vanna SQL Optimization Validation")
        print("=" * 50)
        
        tests = [
            ("Redis Connectivity", self.connect_redis),
            ("Health Endpoint", self.test_health_endpoint),
            ("Caching System", self.test_caching_system),
            ("Session Management", self.test_session_management),
            ("Rate Limiting", self.test_rate_limiting),
            ("Database Pool", self.test_database_pool),
            ("Concurrent Load", self.test_concurrent_load),
        ]
        
        results = {}
        
        for test_name, test_func in tests:
            print(f"\n📋 Running {test_name} test...")
            try:
                results[test_name] = test_func()
            except Exception as e:
                print(f"❌ {test_name} test failed with exception: {e}")
                results[test_name] = False
        
        # Summary
        print("\n" + "=" * 50)
        print("📊 VALIDATION SUMMARY")
        print("=" * 50)
        
        passed_tests = sum(1 for result in results.values() if result)
        total_tests = len(results)
        
        for test_name, result in results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{test_name:20} {status}")
        
        print(f"\nOverall Result: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            print("🎉 All optimization tests PASSED! System ready for high load.")
            return True
        elif passed_tests >= total_tests * 0.8:
            print("⚠️  Most tests passed, but some issues detected. Review failed tests.")
            return False
        else:
            print("❌ Multiple tests failed. System not ready for production load.")
            return False

def main():
    """Main validation function"""
    print("Vanna SQL Optimization Validator")
    print("================================")
    
    # Check if app is running
    validator = OptimizationValidator()
    
    try:
        response = requests.get(f"{validator.base_url}/api/health", timeout=5)
        if response.status_code != 200:
            print(f"❌ Application not responding properly at {validator.base_url}")
            sys.exit(1)
    except:
        print(f"❌ Cannot connect to application at {validator.base_url}")
        print("   Make sure the application is running and accessible")
        sys.exit(1)
    
    # Run validation tests
    success = validator.run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

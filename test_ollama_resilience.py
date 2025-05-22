#!/usr/bin/env python
# test_ollama_resilience.py - Test script for Ollama error handling

import os
import sys
import time
import subprocess
import requests
import logging
import signal
import argparse
from contextlib import contextmanager

# Setup logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import our utility functions
from ollama_fix import is_ollama_running, start_ollama

def make_api_request(endpoint="/api/health", method="GET", json_data=None):
    """Make a request to the Flask API"""
    base_url = "http://localhost:5000"
    url = f"{base_url}{endpoint}"
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, timeout=30)
        elif method.upper() == "POST":
            response = requests.post(url, json=json_data, timeout=30)
        else:
            logger.error(f"Unsupported method: {method}")
            return None
        
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        return None

def kill_ollama_process():
    """Forcefully kill the Ollama process"""
    logger.info("Attempting to kill Ollama process...")
    
    try:
        if sys.platform == 'win32':
            # Windows approach
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE)
        else:
            # Linux/Mac approach
            try:
                # Get PID of ollama process
                result = subprocess.run(["pgrep", "ollama"], 
                                      stdout=subprocess.PIPE, 
                                      text=True)
                pid = result.stdout.strip()
                if pid:
                    subprocess.run(["kill", "-9", pid], 
                                  stdout=subprocess.PIPE, 
                                  stderr=subprocess.PIPE)
            except Exception:
                # Fallback to pkill
                subprocess.run(["pkill", "-9", "ollama"], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.PIPE)
        
        # Wait to ensure the process is killed
        time.sleep(2)
        
        if not is_ollama_running():
            logger.info("Successfully killed Ollama process")
            return True
        else:
            logger.warning("Failed to kill Ollama process")
            return False
    except Exception as e:
        logger.error(f"Error killing Ollama process: {str(e)}")
        return False

def test_app_auto_restart():
    """Test if app automatically restarts Ollama after failure"""
    logger.info("TESTING AUTO-RESTART CAPABILITY")
    
    # Kill Ollama if it's running
    if is_ollama_running():
        kill_ollama_process()
    
    # Make a natural language query
    test_query = {"question": "What are the top 5 customers by revenue?"}
    logger.info("Sending query to API with Ollama down...")
    
    response = make_api_request("/api/ask", "POST", test_query)
    
    if response is None:
        logger.error("API request failed completely")
        return False
    
    logger.info(f"API Response: {response.status_code} - {response.text}")
    
    # Check if Ollama was restarted
    time.sleep(5)  # Give it time to restart
    
    if is_ollama_running():
        logger.info("SUCCESS: Ollama was automatically restarted!")
        return True
    else:
        logger.error("FAIL: Ollama was not restarted automatically")
        return False

def test_restart_endpoint():
    """Test the restart Ollama API endpoint"""
    logger.info("TESTING RESTART ENDPOINT")
    
    # Kill Ollama if it's running
    if is_ollama_running():
        kill_ollama_process()
    
    # Call the restart endpoint
    logger.info("Calling restart-ollama endpoint...")
    
    response = make_api_request("/api/restart-ollama", "POST")
    
    if response is None:
        logger.error("Restart API request failed")
        return False
    
    logger.info(f"API Response: {response.status_code} - {response.text}")
    
    # Check if Ollama is running now
    time.sleep(5)  # Give it time to start
    
    if is_ollama_running():
        logger.info("SUCCESS: Restart endpoint worked!")
        return True
    else:
        logger.error("FAIL: Restart endpoint did not start Ollama")
        return False

def test_retry_mechanism():
    """Test if the retry mechanism works during intermittent failures"""
    logger.info("TESTING RETRY MECHANISM")
    
    # Ensure Ollama is running
    if not is_ollama_running():
        start_ollama()
        time.sleep(5)  # Give it time to start
    
    # Prepare a simple query
    test_query = {"question": "List all tables in the database"}
    
    # Start request and kill Ollama mid-request
    logger.info("Sending query and killing Ollama mid-process...")
    
    # This is a bit tricky as we need to time it right
    # Start a thread to send request
    import threading
    
    def delayed_kill():
        time.sleep(1)  # Wait a second for request to start
        kill_ollama_process()
    
    # Start the kill thread
    kill_thread = threading.Thread(target=delayed_kill)
    kill_thread.start()
    
    # Send request
    response = make_api_request("/api/ask", "POST", test_query)
    
    # Wait for kill thread to finish
    kill_thread.join()
    
    if response is None:
        logger.info("API request failed but this might be expected")
    else:
        logger.info(f"API Response: {response.status_code} - {response.text}")
    
    # Check if Ollama was automatically restarted
    time.sleep(10)  # Give more time as retry has delays
    
    if is_ollama_running():
        logger.info("Ollama is running again after failure")
        
        # Try another request to see if system recovered
        logger.info("Trying another request to verify recovery...")
        response = make_api_request("/api/ask", "POST", test_query)
        
        if response and response.status_code == 200:
            logger.info("SUCCESS: System recovered and request succeeded!")
            return True
        else:
            logger.error("FAIL: System did not fully recover")
            return False
    else:
        logger.error("FAIL: Ollama was not restarted after failure")
        return False

def run_all_tests():
    """Run all tests in sequence"""
    results = {}
    
    # Test 1: Test restart endpoint
    logger.info("\n====== TEST 1: RESTART ENDPOINT ======")
    results["restart_endpoint"] = test_restart_endpoint()
    
    time.sleep(5)  # Add delay between tests
    
    # Test 2: Test auto-restart capability
    logger.info("\n====== TEST 2: AUTO-RESTART CAPABILITY ======")
    results["auto_restart"] = test_app_auto_restart()
    
    time.sleep(5)  # Add delay between tests
    
    # Test 3: Test retry mechanism
    logger.info("\n====== TEST 3: RETRY MECHANISM ======")
    results["retry_mechanism"] = test_retry_mechanism()
    
    # Print summary
    logger.info("\n====== TEST RESULTS SUMMARY ======")
    for test, result in results.items():
        status = "PASSED" if result else "FAILED"
        logger.info(f"{test}: {status}")
    
    if all(results.values()):
        logger.info("ALL TESTS PASSED! Ollama error handling is working correctly.")
        return True
    else:
        logger.warning("SOME TESTS FAILED! Ollama error handling needs improvement.")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Ollama error handling")
    parser.add_argument("--test", choices=["restart", "auto", "retry", "all"], 
                      default="all", help="Which test to run")
    
    args = parser.parse_args()
    
    if args.test == "restart":
        test_restart_endpoint()
    elif args.test == "auto":
        test_app_auto_restart()
    elif args.test == "retry":
        test_retry_mechanism()
    else:
        run_all_tests()

import os
import sys
import time
import logging
import subprocess
import requests
from functools import wraps

# Import our custom errors
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from errors import OllamaError, OllamaProcessError, OllamaConnectionError
except ImportError:
    # Define fallback error classes if imports fail
    class OllamaError(Exception):
        pass
    class OllamaProcessError(OllamaError):
        pass
    class OllamaConnectionError(OllamaError):
        pass

logger = logging.getLogger(__name__)

def is_ollama_running():
    """Check if Ollama is running"""
    ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    try:
        response = requests.get(f"{ollama_host}", timeout=5)
        return response.status_code == 200
    except requests.exceptions.ConnectionError:
        return False
    except Exception as e:
        logger.error(f"Error checking Ollama status: {str(e)}")
        return False

def start_ollama():
    """Start the Ollama server"""
    try:
        logger.info("Attempting to restart Ollama server...")
        
        if sys.platform == 'win32':
            # Windows-specific start command in background
            subprocess.Popen(
                ["start", "cmd", "/c", "ollama", "serve"],
                shell=True
            )
        else:
            # Linux/Mac start command
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        
        # Wait for Ollama to start
        logger.info("Waiting for Ollama to start...")
        for i in range(10):
            if is_ollama_running():
                logger.info("Ollama successfully restarted")
                return True
            time.sleep(2)
        
        logger.error("Failed to restart Ollama after 20 seconds")
        return False
    except Exception as e:
        logger.error(f"Error starting Ollama: {str(e)}")
        return False

def with_ollama_retry(max_retries=3, retry_delay=5):
    """
    Decorator to retry a function if an Ollama-related exception occurs.
    This will attempt to restart Ollama if it appears to be down.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_message = str(e).lower()
                    is_ollama_error = False
                    
                    # Check if the error is related to Ollama termination
                    if any(msg in error_message for msg in [
                        "terminated", "exit status", "exit code", "status code: 500"
                    ]):
                        logger.warning(f"Caught Ollama process error on attempt {attempt+1}/{max_retries}: {str(e)}")
                        is_ollama_error = True
                        
                        # Extract exit code if available
                        exit_code = None
                        if "exit status" in error_message:
                            try:
                                exit_code = int(error_message.split("exit status")[1].strip().split()[0])
                            except (ValueError, IndexError):
                                pass
                        
                        # Convert to our custom error for better handling
                        converted_error = OllamaProcessError(str(e), exit_code)
                    
                    # Check if the error is related to connection issues
                    elif any(msg in error_message for msg in [
                        "connection refused", "failed to connect", "timeout", "connection error"
                    ]):
                        logger.warning(f"Caught Ollama connection error on attempt {attempt+1}/{max_retries}: {str(e)}")
                        is_ollama_error = True
                        converted_error = OllamaConnectionError(str(e))
                    
                    # Handle Ollama errors
                    if is_ollama_error:
                        if not is_ollama_running():
                            logger.warning("Ollama appears to be down, attempting to restart...")
                            if start_ollama():
                                logger.info(f"Ollama restarted, retrying in {retry_delay} seconds...")
                                time.sleep(retry_delay)
                                continue
                        else:
                            logger.warning(f"Ollama is running but operation failed, retrying in {retry_delay} seconds...")
                            time.sleep(retry_delay)
                            continue
                        
                        # If we get here, we couldn't fix the issue automatically
                        if attempt == max_retries - 1:
                            logger.error(f"Failed to recover from Ollama error after {max_retries} attempts")
                            raise converted_error
                        continue
                    
                    # If it's not an Ollama connection issue, re-raise the exception
                    logger.error(f"Non-Ollama error encountered: {str(e)}")
                    raise
            
            # If we've exhausted all retries
            raise OllamaError(f"Function failed after {max_retries} attempts")
        return wrapper
    return decorator

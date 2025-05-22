import os
import sys
import time
import subprocess
import argparse
import logging
import requests
import psutil
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/ollama_troubleshoot.log", mode='a'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("ollama-troubleshooter")

def check_ollama_installed():
    """Check if Ollama is installed"""
    try:
        if sys.platform == 'win32':
            result = subprocess.run(['where', 'ollama'], capture_output=True, text=True)
        else:
            result = subprocess.run(['which', 'ollama'], capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info(f"Ollama is installed at: {result.stdout.strip()}")
            return True
        else:
            logger.error("Ollama is not installed or not in PATH")
            return False
    except Exception as e:
        logger.error(f"Error checking if Ollama is installed: {str(e)}")
        return False

def get_ollama_process():
    """Find running Ollama process"""
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            if proc.info['name'] and 'ollama' in proc.info['name'].lower():
                logger.info(f"Found Ollama process: PID {proc.info['pid']}")
                return proc
            elif proc.info['cmdline'] and any('ollama' in cmd.lower() for cmd in proc.info['cmdline'] if cmd):
                logger.info(f"Found Ollama process: PID {proc.info['pid']}")
                return proc
        
        logger.warning("No Ollama process found")
        return None
    except Exception as e:
        logger.error(f"Error finding Ollama process: {str(e)}")
        return None

def is_ollama_running():
    """Check if Ollama server is responsive"""
    ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    try:
        response = requests.get(f"{ollama_host}", timeout=5)
        if response.status_code == 200:
            logger.info("Ollama server is running and responsive")
            return True
        else:
            logger.warning(f"Ollama server returned unexpected status code: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        logger.warning("Ollama server is not responsive (connection error)")
        return False
    except Exception as e:
        logger.error(f"Error checking Ollama server: {str(e)}")
        return False

def check_model_exists(model_name):
    """Check if a model exists in Ollama"""
    ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    try:
        response = requests.get(f"{ollama_host}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get('models', [])
            model_names = [m.get('name') for m in models]
            
            # Check if our model is in the list (partial match)
            for name in model_names:
                if model_name in name:
                    logger.info(f"Model {model_name} is available")
                    return True
            
            logger.warning(f"Model {model_name} is not available")
            return False
        else:
            logger.warning(f"Ollama API returned unexpected status: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error checking if model exists: {str(e)}")
        return False

def kill_ollama_process():
    """Kill any running Ollama process"""
    proc = get_ollama_process()
    if proc:
        try:
            proc.terminate()
            logger.info(f"Terminated Ollama process with PID {proc.info['pid']}")
            time.sleep(2)
            
            # Check if it's still running
            if proc.is_running():
                proc.kill()
                logger.info(f"Forcefully killed Ollama process with PID {proc.info['pid']}")
            
            return True
        except Exception as e:
            logger.error(f"Error killing Ollama process: {str(e)}")
            return False
    else:
        logger.info("No Ollama process to kill")
        return True

def start_ollama_server():
    """Start the Ollama server"""
    try:
        logger.info("Starting Ollama server...")
        
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
            time.sleep(2)
            if is_ollama_running():
                logger.info("Ollama successfully started")
                return True
            logger.info(f"Waiting... ({i+1}/10)")
        
        logger.error("Failed to start Ollama after 20 seconds")
        return False
    except Exception as e:
        logger.error(f"Error starting Ollama: {str(e)}")
        return False

def pull_model(model_name):
    """Pull a model from Ollama registry"""
    try:
        logger.info(f"Pulling model {model_name}...")
        
        result = subprocess.run(
            ["ollama", "pull", model_name],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            logger.info(f"Successfully pulled model {model_name}")
            return True
        else:
            logger.error(f"Failed to pull model {model_name}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error pulling model {model_name}: {str(e)}")
        return False

def check_disk_space():
    """Check available disk space"""
    try:
        if sys.platform == 'win32':
            # Get disk where Ollama is installed (use system drive)
            drive = os.environ.get('SystemDrive', 'C:')
            total, used, free = psutil.disk_usage(drive)
        else:
            # For Linux/Mac, check the root filesystem
            total, used, free = psutil.disk_usage('/')
        
        free_gb = free / (1024 * 1024 * 1024)
        logger.info(f"Available disk space: {free_gb:.2f} GB")
        
        # Models can be large, so warn if less than 10GB is available
        if free_gb < 10:
            logger.warning(f"Low disk space: {free_gb:.2f} GB available. This may cause issues with Ollama.")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error checking disk space: {str(e)}")
        return False

def fix_ollama():
    """Attempt to fix common Ollama issues"""
    logger.info("Starting Ollama troubleshooting process...")
    
    # Step 1: Check if Ollama is installed
    if not check_ollama_installed():
        logger.error("Ollama is not installed. Please install it first.")
        return False
    
    # Step 2: Check disk space
    check_disk_space()
    
    # Step 3: Kill any existing Ollama process
    kill_ollama_process()
    
    # Step 4: Start Ollama server
    if not start_ollama_server():
        logger.error("Failed to start Ollama server. Try installing Ollama again.")
        return False
    
    # Step 5: Check if the model exists, if not pull it
    model_name = os.getenv('OLLAMA_MODEL', 'gemma3:12b')
    if not check_model_exists(model_name):
        logger.info(f"Model {model_name} not found, attempting to pull it")
        if not pull_model(model_name):
            logger.error(f"Failed to pull model {model_name}")
            return False
    
    logger.info("Ollama troubleshooting completed successfully!")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Troubleshoot Ollama issues')
    parser.add_argument('--restart', action='store_true', help='Restart Ollama service')
    parser.add_argument('--check', action='store_true', help='Check Ollama status')
    parser.add_argument('--model', help='Check or pull specific model')
    parser.add_argument('--fix', action='store_true', help='Fix common Ollama issues')
    
    args = parser.parse_args()
    
    if args.model:
        os.environ['OLLAMA_MODEL'] = args.model
    
    if args.check:
        installed = check_ollama_installed()
        running = is_ollama_running()
        disk_ok = check_disk_space()
        
        if args.model:
            model_ok = check_model_exists(args.model)
            print(f"Model {args.model} available: {'Yes' if model_ok else 'No'}")
        
        print(f"Ollama installed: {'Yes' if installed else 'No'}")
        print(f"Ollama running: {'Yes' if running else 'No'}")
        print(f"Disk space OK: {'Yes' if disk_ok else 'Warning: Low disk space'}")
        
        sys.exit(0 if (installed and running) else 1)
    
    elif args.restart:
        kill_ollama_process()
        success = start_ollama_server()
        sys.exit(0 if success else 1)
    
    elif args.fix or not any([args.check, args.restart]):
        # Default action: full troubleshooting
        success = fix_ollama()
        sys.exit(0 if success else 1)
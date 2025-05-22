import os
import sys
import time
import requests
import subprocess
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_ollama_running():
    """Check if Ollama is running on the specified host"""
    ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    try:
        response = requests.get(f"{ollama_host}")
        return response.status_code == 200
    except requests.exceptions.ConnectionError:
        return False

def check_model_available(model_name=None):
    """Check if the configured model is available in Ollama"""
    ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    if model_name is None:
        model_name = os.getenv('OLLAMA_MODEL', 'gemma3:12b')
    
    try:
        response = requests.get(f"{ollama_host}/api/tags")
        if response.status_code == 200:
            models = response.json().get('models', [])
            model_names = [m.get('name') for m in models]
            
            # Check if our model is in the list
            for name in model_names:
                if model_name in name:
                    return True
            return False
        return False
    except requests.exceptions.ConnectionError:
        return False

def pull_model(model_name=None):
    """Pull the model using Ollama CLI"""
    if model_name is None:
        model_name = os.getenv('OLLAMA_MODEL', 'gemma3:12b')
    
    print(f"Pulling model {model_name}. This may take a while...")
    
    try:
        process = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Print the output in real time
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                print(output.strip())
        
        # Wait for the process to complete
        process.communicate()
        
        return process.returncode == 0
    except Exception as e:
        print(f"Error pulling model: {str(e)}")
        return False

def attempt_start_ollama():
    """Try to start Ollama if it's not running"""
    try:
        print("Starting Ollama server...")
        
        # Start Ollama server as a background process
        if sys.platform == 'win32':
            process = subprocess.Popen(
                ["start", "cmd", "/c", "ollama", "serve"],
                shell=True
            )
        else:
            process = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        
        # Wait for Ollama to start
        print("Waiting for Ollama to start...")
        for i in range(10):
            if check_ollama_running():
                print("Ollama is now running")
                return True
            time.sleep(2)
            print(".", end="", flush=True)
        
        print("\nFailed to start Ollama after waiting")
        return False
    except Exception as e:
        print(f"Error starting Ollama: {str(e)}")
        return False

if __name__ == "__main__":
    print("Checking Ollama status...")
    
    if not check_ollama_running():
        print("Ollama is not running.")
        print("Attempting to start Ollama...")
        
        if not attempt_start_ollama():
            print("Could not start Ollama. Please start it manually with 'ollama serve'")
            sys.exit(1)
    else:
        print("Ollama is running.")
    
    model_name = os.getenv('OLLAMA_MODEL', 'gemma3:12b')
    print(f"Checking if model {model_name} is available...")
    
    if not check_model_available(model_name):
        print(f"Model {model_name} is not available. Attempting to pull...")
        if not pull_model(model_name):
            print(f"Failed to pull model {model_name}. Please pull it manually with 'ollama pull {model_name}'")
            sys.exit(1)
        else:
            print(f"Successfully pulled model {model_name}")
    else:
        print(f"Model {model_name} is available.")
    
    print("All checks passed! Ollama is ready to use with the application.")
    sys.exit(0)

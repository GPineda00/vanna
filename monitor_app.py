#!/usr/bin/env python
# monitor_app.py - System monitoring and health check tool

import os
import sys
import time
import json
import logging
import argparse
import datetime
import socket
import requests
import threading
import subprocess
from collections import deque
import psutil

# Setup logging
logging.basicConfig(level=logging.INFO,
                  format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Constants
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5000
DEFAULT_INTERVAL = 60
DEFAULT_METRICS_FILE = "app_metrics.json"
DEFAULT_METRICS_HISTORY = 1440  # 24 hours of data at 1-minute intervals

# Global variables
metrics_history = {}
for metric in ["cpu", "memory", "disk", "ollama_status", "api_latency", "api_errors"]:
    metrics_history[metric] = deque(maxlen=DEFAULT_METRICS_HISTORY)

def get_system_metrics():
    """Get system metrics (CPU, memory, disk)"""
    cpu_usage = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    memory_usage = memory.percent
    disk = psutil.disk_usage('/')
    disk_usage = disk.percent
    
    return {
        "timestamp": datetime.datetime.now().isoformat(),
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "disk_usage": disk_usage
    }

def check_api_health(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Check API health and get status information"""
    url = f"http://{host}:{port}/api/health"
    
    try:
        start_time = time.time()
        response = requests.get(url, timeout=10)
        latency = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            return {
                "status": "ok",
                "api_latency": latency,
                "ollama_status": data.get("ollama_status", "unknown"),
                "database_status": data.get("database_status", "unknown"),
                "ollama_model": data.get("ollama_model", "unknown"),
                "raw_response": data
            }
        else:
            return {
                "status": "error",
                "api_latency": latency,
                "error": f"Unexpected status code: {response.status_code}"
            }
    except requests.exceptions.ConnectionError:
        return {
            "status": "error",
            "api_latency": None,
            "error": "Connection refused - API may be down"
        }
    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "api_latency": None,
            "error": "Request timed out"
        }
    except Exception as e:
        return {
            "status": "error",
            "api_latency": None,
            "error": str(e)
        }

def check_ollama_process():
    """Check if Ollama process is running and get resource usage"""
    try:
        ollama_procs = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'ollama' in proc.info['name'].lower() or \
                   any('ollama' in cmd.lower() for cmd in (proc.info['cmdline'] or [])):
                    ollama_procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if ollama_procs:
            # Get info for the first Ollama process
            proc = ollama_procs[0]
            
            try:
                cpu = proc.cpu_percent(interval=0.5)
                memory = proc.memory_info().rss / 1024 / 1024  # MB
                
                return {
                    "status": "running",
                    "pid": proc.pid,
                    "cpu_percent": cpu,
                    "memory_mb": memory,
                    "create_time": datetime.datetime.fromtimestamp(proc.create_time()).isoformat()
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return {"status": "access_denied"}
        else:
            return {"status": "not_running"}
    except Exception as e:
        logger.error(f"Error checking Ollama process: {str(e)}")
        return {"status": "error", "error": str(e)}

def run_simple_query(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Run a simple query to test end-to-end functionality"""
    url = f"http://{host}:{port}/api/ask"
    
    simple_query = {
        "question": "Show me a list of tables"
    }
    
    try:
        start_time = time.time()
        response = requests.post(url, json=simple_query, timeout=30)
        latency = time.time() - start_time
        
        if response.status_code == 200:
            return {
                "status": "ok",
                "latency": latency,
                "has_sql": "sql" in response.json(),
                "has_data": "data" in response.json()
            }
        else:
            return {
                "status": "error",
                "latency": latency,
                "error": f"Unexpected status code: {response.status_code}",
                "error_message": response.text
            }
    except Exception as e:
        return {
            "status": "error",
            "latency": None,
            "error": str(e)
        }

def monitor_system(host=DEFAULT_HOST, port=DEFAULT_PORT, interval=DEFAULT_INTERVAL, 
                 output_file=DEFAULT_METRICS_FILE):
    """Continuously monitor the system and API health"""
    logger.info(f"Starting system monitoring (press Ctrl+C to stop)")
    logger.info(f"Monitoring API at http://{host}:{port} every {interval} seconds")
    
    try:
        run_count = 0
        while True:
            run_count += 1
            
            # Get metrics
            system_metrics = get_system_metrics()
            api_health = check_api_health(host, port)
            ollama_status = check_ollama_process()
            
            # Run query test every 10 intervals
            if run_count % 10 == 0:
                query_test = run_simple_query(host, port)
                logger.info(f"Query test: {'OK' if query_test['status'] == 'ok' else 'FAILED'}")
            
            # Update metrics history
            metrics_history["cpu"].append(system_metrics["cpu_usage"])
            metrics_history["memory"].append(system_metrics["memory_usage"])
            metrics_history["disk"].append(system_metrics["disk_usage"])
            metrics_history["ollama_status"].append(1 if ollama_status["status"] == "running" else 0)
            
            if api_health["api_latency"] is not None:
                metrics_history["api_latency"].append(api_health["api_latency"])
            metrics_history["api_errors"].append(1 if api_health["status"] != "ok" else 0)
            
            # Log current status
            logger.info(f"System: CPU={system_metrics['cpu_usage']}%, "
                       f"Mem={system_metrics['memory_usage']}%, "
                       f"Disk={system_metrics['disk_usage']}%")
            
            api_status = "OK" if api_health["status"] == "ok" else "ERROR"
            if api_health["api_latency"] is not None:
                logger.info(f"API: Status={api_status}, Latency={api_health['api_latency']:.3f}s")
            else:
                logger.info(f"API: Status={api_status}")
            
            ollama_running = ollama_status["status"] == "running"
            logger.info(f"Ollama: {'Running' if ollama_running else 'Not running'}")
            
            if ollama_running and "cpu_percent" in ollama_status:
                logger.info(f"Ollama Usage: CPU={ollama_status['cpu_percent']}%, "
                           f"Memory={ollama_status['memory_mb']:.1f}MB")
            
            # Save metrics to file
            current_metrics = {
                "timestamp": datetime.datetime.now().isoformat(),
                "system": system_metrics,
                "api": api_health,
                "ollama": ollama_status,
            }
            
            if run_count % 10 == 0:
                current_metrics["query_test"] = query_test
            
            with open(output_file, 'w') as f:
                json.dump(current_metrics, f, indent=2)
            
            # Sleep until next interval
            time.sleep(interval)
    
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.error(f"Error in monitoring: {str(e)}")

def restart_services(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Attempt to restart services if they're down"""
    logger.info("Checking services and attempting restarts if needed...")
    
    # Check API health
    api_health = check_api_health(host, port)
    if api_health["status"] != "ok":
        logger.warning("API is down or unresponsive")
        
        # Check if we need to restart Flask app
        # This would require a proper service setup, but here's a basic example
        if sys.platform == 'win32':
            logger.info("Attempting to restart Flask app (Windows)")
            try:
                # Kill any existing Python processes running app.py
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if 'python' in proc.name().lower() and \
                           any('app.py' in cmd.lower() for cmd in proc.cmdline()):
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                
                # Start app in background
                subprocess.Popen(
                    ["start", "cmd", "/c", "python", "app.py"],
                    shell=True
                )
                logger.info("Attempted to restart Flask app")
            except Exception as e:
                logger.error(f"Error restarting Flask: {str(e)}")
        else:
            logger.info("Attempting to restart Flask app (Linux/Mac)")
            try:
                # This would ideally use systemd or similar
                subprocess.run(
                    ["pkill", "-f", "python app.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                subprocess.Popen(
                    ["nohup", "python", "app.py", "&"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                logger.info("Attempted to restart Flask app")
            except Exception as e:
                logger.error(f"Error restarting Flask: {str(e)}")
    
    # Check Ollama status and restart if needed
    ollama_status = check_ollama_process()
    if ollama_status["status"] != "running":
        logger.warning("Ollama is not running")
        
        # Try restarting via API first
        try:
            restart_url = f"http://{host}:{port}/api/restart-ollama"
            response = requests.post(restart_url, timeout=10)
            
            if response.status_code == 200:
                logger.info("Successfully requested Ollama restart via API")
                time.sleep(5)  # Wait for restart to take effect
                
                # Check if it's running now
                if check_ollama_process()["status"] == "running":
                    logger.info("Ollama successfully restarted via API")
                    return
                else:
                    logger.warning("Ollama still not running after API restart")
            else:
                logger.warning(f"API restart request failed with status {response.status_code}")
        except Exception:
            logger.warning("Could not restart Ollama via API, trying direct restart")
        
        # Direct restart as fallback
        try:
            if sys.platform == 'win32':
                subprocess.Popen(
                    ["start", "cmd", "/c", "ollama", "serve"],
                    shell=True
                )
            else:
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            
            logger.info("Attempted to restart Ollama directly")
            
            # Check if it started
            time.sleep(5)
            if check_ollama_process()["status"] == "running":
                logger.info("Ollama successfully restarted directly")
            else:
                logger.error("Failed to restart Ollama")
        except Exception as e:
            logger.error(f"Error restarting Ollama: {str(e)}")

def generate_report(output_file="system_report.txt"):
    """Generate a system health report based on collected metrics"""
    if not all(len(metrics_history[m]) > 0 for m in metrics_history):
        logger.error("Not enough metrics collected for report")
        return
    
    # Generate report
    with open(output_file, "w") as f:
        f.write("=======================================\n")
        f.write("SYSTEM HEALTH REPORT\n")
        f.write(f"Generated: {datetime.datetime.now().isoformat()}\n")
        f.write("=======================================\n\n")
        
        # CPU stats
        cpu_values = list(metrics_history["cpu"])
        f.write(f"CPU Usage:\n")
        f.write(f"  Average: {sum(cpu_values) / len(cpu_values):.1f}%\n")
        f.write(f"  Maximum: {max(cpu_values):.1f}%\n")
        f.write(f"  Minimum: {min(cpu_values):.1f}%\n\n")
        
        # Memory stats
        mem_values = list(metrics_history["memory"])
        f.write(f"Memory Usage:\n")
        f.write(f"  Average: {sum(mem_values) / len(mem_values):.1f}%\n")
        f.write(f"  Maximum: {max(mem_values):.1f}%\n")
        f.write(f"  Minimum: {min(mem_values):.1f}%\n\n")
        
        # API latency stats
        latency_values = list(metrics_history["api_latency"])
        if latency_values:
            f.write(f"API Latency:\n")
            f.write(f"  Average: {sum(latency_values) / len(latency_values):.3f}s\n")
            f.write(f"  Maximum: {max(latency_values):.3f}s\n")
            f.write(f"  Minimum: {min(latency_values):.3f}s\n\n")
        
        # Ollama availability
        ollama_values = list(metrics_history["ollama_status"])
        ollama_uptime = sum(ollama_values) / len(ollama_values) * 100
        f.write(f"Ollama Uptime: {ollama_uptime:.1f}%\n\n")
        
        # API errors
        api_error_values = list(metrics_history["api_errors"])
        api_errors_count = sum(api_error_values)
        api_uptime = 100 - (api_errors_count / len(api_error_values) * 100)
        f.write(f"API Uptime: {api_uptime:.1f}%\n")
        f.write(f"API Error Count: {api_errors_count}\n\n")
        
        # Recommendations
        f.write("=== Recommendations ===\n\n")
        
        if max(cpu_values) > 90:
            f.write("- HIGH CPU USAGE DETECTED: Consider upgrading CPU resources\n")
        
        if max(mem_values) > 90:
            f.write("- HIGH MEMORY USAGE DETECTED: Consider adding more RAM\n")
        
        if ollama_uptime < 95:
            f.write("- OLLAMA STABILITY ISSUES: Ollama service needs attention\n")
        
        if api_uptime < 95:
            f.write("- API RELIABILITY ISSUES: Flask application may need optimization\n")
        
        if latency_values and max(latency_values) > 5:
            f.write("- HIGH API LATENCY: Consider optimizing database queries or LLM processing\n")
    
    logger.info(f"Report generated and saved to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Monitor system and API health")
    parser.add_argument("--host", default=DEFAULT_HOST, help="API host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="API port")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, 
                      help="Monitoring interval in seconds")
    parser.add_argument("--output", default=DEFAULT_METRICS_FILE, 
                      help="Output metrics file")
    parser.add_argument("--restart", action="store_true", 
                      help="Attempt to restart services if needed")
    parser.add_argument("--report", action="store_true", 
                      help="Generate a system health report")
    
    args = parser.parse_args()
    
    if args.restart:
        restart_services(args.host, args.port)
    elif args.report:
        generate_report()
    else:
        monitor_system(args.host, args.port, args.interval, args.output)

if __name__ == "__main__":
    main()

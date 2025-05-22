import os
import pyodbc
import socket
from dotenv import load_dotenv
import subprocess

def test_sql_connection():
    # Load environment variables
    load_dotenv()
    
    server = os.getenv('SQL_SERVER', 'localhost')
    database = os.getenv('SQL_DATABASE', '')
    username = os.getenv('SQL_USERNAME')
    password = os.getenv('SQL_PASSWORD')
    
    print(f"\n===== SQL SERVER CONNECTION TEST =====")
    print(f"Server: {server}")
    print(f"Database: {database}")
    print(f"Username provided: {'Yes' if username else 'No'}")
    
    # Test 1: Ping server
    print("\n----- Test 1: Ping Server -----")
    try:
        ping_process = subprocess.run(['ping', '-n', '1', server], 
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,
                                      text=True)
        if ping_process.returncode == 0:
            print(f"✓ Server {server} is reachable")
            print(ping_process.stdout)
        else:
            print(f"✗ Server {server} could not be reached")
            print(ping_process.stdout)
            print("\nThis suggests the server name might be incorrect or the server is down.")
    except Exception as e:
        print(f"✗ Error during ping test: {str(e)}")
    
    # Test 2: Socket connection to SQL port
    print("\n----- Test 2: SQL Server Port Test (1433) -----")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((server, 1433))
        if result == 0:
            print(f"✓ SQL Server port (1433) is open on {server}")
        else:
            print(f"✗ SQL Server port (1433) is not accessible on {server}")
            print("This suggests a firewall might be blocking the connection or SQL Server is not running.")
    except socket.gaierror:
        print(f"✗ Hostname {server} could not be resolved")
        print("Try using the IP address instead of the hostname.")
    except Exception as e:
        print(f"✗ Socket error: {str(e)}")
    finally:
        sock.close()
    
    # Test 3: SQL connection with standard settings
    print("\n----- Test 3: Standard SQL Connection -----")
    try:
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"Connect Timeout=30;"
        )
        print("Connection string:", conn_str.replace(f"PWD={password};", "PWD=********;"))
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0]
        print(f"✓ Connection successful!")
        print(f"SQL Server Version: {version}")
        conn.close()
    except pyodbc.Error as e:
        print(f"✗ Connection failed: {str(e)}")
    
    # Test 4: SQL connection with TCP explicit
    print("\n----- Test 4: TCP Explicit Connection -----")
    try:
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER=tcp:{server},1433;"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"Connect Timeout=30;"
        )
        print("Connection string:", conn_str.replace(f"PWD={password};", "PWD=********;"))
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0]
        print(f"✓ Connection successful!")
        print(f"SQL Server Version: {version}")
        conn.close()
    except pyodbc.Error as e:
        print(f"✗ Connection failed: {str(e)}")
    
    print("\n===== RECOMMENDATIONS =====")
    print("If all tests failed, here are some things to check:")
    print("1. Is the SQL Server name correct? Try using the IP address instead.")
    print("2. Is the SQL Server running and accepting connections?")
    print("3. Is there a firewall blocking port 1433?")
    print("4. Are your SQL credentials correct?")
    print("5. Is the ODBC driver installed correctly?")

if __name__ == "__main__":
    test_sql_connection()
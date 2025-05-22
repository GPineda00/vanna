import os
import sys
import json
import pandas as pd
from dotenv import load_dotenv

# Import custom Vanna class from app.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app import OllamaVanna, get_sql_connection

# Load environment variables
load_dotenv()

def load_training_data(file_path):
    """Load training data from a JSON file"""
    with open(file_path, 'r') as f:
        return json.load(f)

def run_sql(sql):
    """Run SQL and return results as DataFrame"""
    try:
        conn = get_sql_connection()
        df = pd.read_sql(sql, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Error executing SQL: {e}")
        return None

def main():
    # Initialize Vanna with Ollama model
    vn = OllamaVanna(config={
        'model': os.getenv('OLLAMA_MODEL', 'gemma3:12b'),
        'ollama_host': os.getenv('OLLAMA_HOST', 'http://localhost:11434'),
        'persist_directory': os.getenv('CHROMADB_DIR', './chromadb_data'),
        'ollama_timeout': float(os.getenv('OLLAMA_TIMEOUT', '300.0')),
    })
    
    # Set the run_sql function
    vn.run_sql = run_sql
    
    # Check command line arguments
    if len(sys.argv) < 2:
        print("Available commands:")
        print("  train <file.json> - Train with data from a JSON file")
        print("  test <question>   - Test a natural language question")
        print("  export <file.json> - Export training data to a JSON file")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "train" and len(sys.argv) >= 3:
        file_path = sys.argv[2]
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            sys.exit(1)
        
        training_data = load_training_data(file_path)
        count = 0
        
        print(f"Loading {len(training_data)} training examples...")
        for item in training_data:
            if "question" in item and "sql" in item:
                vn.add_question_sql(item["question"], item["sql"])
                count += 1
                print(f"Added ({count}/{len(training_data)}): {item['question'][:50]}...")
        
        print(f"Training complete. Added {count} examples.")
        
    elif command == "test" and len(sys.argv) >= 3:
        question = " ".join(sys.argv[2:])
        print(f"Testing question: {question}")
        
        try:
            sql = vn.generate_sql(question)
            print("\nGenerated SQL:")
            print(sql)
            
            print("\nExecuting SQL...")
            df = vn.run_sql(sql)
            
            if df is not None:
                print("\nResults:")
                print(df.head().to_string())
                if len(df) > 5:
                    print(f"... and {len(df) - 5} more rows")
        except Exception as e:
            print(f"Error: {str(e)}")
    
    elif command == "export" and len(sys.argv) >= 3:
        file_path = sys.argv[2]
        
        # This is a placeholder since the current Vanna API doesn't provide
        # a direct way to export training data
        print("Export functionality not implemented")
        print("Training data is stored in the ChromaDB directory")
    
    else:
        print("Unknown command or missing arguments")
        sys.exit(1)

if __name__ == "__main__":
    main()

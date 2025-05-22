#!/usr/bin/env python
# train_advanced.py - Advanced training script for Vanna and Ollama

import os
import sys
import json
import pandas as pd
import logging
import argparse
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import Vanna class
from app import OllamaVanna, get_sql_connection

# Initialize Vanna with Ollama
def get_vanna_instance():
    """Get a configured Vanna instance"""
    vn = OllamaVanna(config={
        'model': os.getenv('OLLAMA_MODEL', 'gemma3:12b'),
        'ollama_host': os.getenv('OLLAMA_HOST', 'http://localhost:11434'),
        'persist_directory': os.getenv('CHROMADB_DIR', './chromadb_data'),
        'ollama_timeout': float(os.getenv('OLLAMA_TIMEOUT', '300.0')),
        'options': {
            'num_ctx': int(os.getenv('OLLAMA_CONTEXT_SIZE', '8192')),
            'num_gpu': int(os.getenv('OLLAMA_NUM_GPU', '1')),
            'num_thread': int(os.getenv('OLLAMA_NUM_THREAD', '4')),
            'temperature': float(os.getenv('OLLAMA_TEMPERATURE', '0.1')),
        }
    })
    
    # Set the run_sql function
    def run_sql_query(sql):
        conn = get_sql_connection()
        df = pd.read_sql(sql, conn)
        conn.close()
        return df
    
    vn.run_sql = run_sql_query
    return vn

def load_database_schema(vn):
    """Load the database schema into Vanna"""
    logger.info("Loading database schema...")
    
    try:
        conn = get_sql_connection()
        
        # Get list of tables
        tables_query = """
        SELECT 
            TABLE_SCHEMA, 
            TABLE_NAME 
        FROM 
            INFORMATION_SCHEMA.TABLES 
        WHERE 
            TABLE_TYPE = 'BASE TABLE'
        """
        tables_df = pd.read_sql(tables_query, conn)
        logger.info(f"Found {len(tables_df)} tables")
        
        # Get column information for each table
        for _, row in tables_df.iterrows():
            schema = row['TABLE_SCHEMA']
            table = row['TABLE_NAME']
            full_name = f"{schema}.{table}"
            
            logger.info(f"Loading schema for {full_name}")
            
            # Get column information
            columns_query = f"""
            SELECT 
                COLUMN_NAME, 
                DATA_TYPE, 
                CHARACTER_MAXIMUM_LENGTH,
                IS_NULLABLE
            FROM 
                INFORMATION_SCHEMA.COLUMNS
            WHERE 
                TABLE_SCHEMA = '{schema}'
                AND TABLE_NAME = '{table}'
            ORDER BY 
                ORDINAL_POSITION
            """
            columns_df = pd.read_sql(columns_query, conn)
            
            # Create DDL statement
            ddl = f"CREATE TABLE {full_name} (\n"
            for i, col_row in columns_df.iterrows():
                col_name = col_row['COLUMN_NAME']
                data_type = col_row['DATA_TYPE']
                max_len = col_row['CHARACTER_MAXIMUM_LENGTH']
                nullable = col_row['IS_NULLABLE']
                
                if max_len:
                    data_type = f"{data_type}({max_len})"
                
                null_str = "NULL" if nullable == "YES" else "NOT NULL"
                
                ddl += f"    {col_name} {data_type} {null_str}"
                if i < len(columns_df) - 1:
                    ddl += ",\n"
                else:
                    ddl += "\n"
            
            ddl += ");"
            
            # Add DDL to Vanna
            vn.add_ddl(ddl)
            logger.info(f"Added DDL for {full_name}")
        
        # Get primary and foreign key information
        logger.info("Loading relationship information...")
        
        # Primary keys
        pk_query = """
        SELECT 
            TC.TABLE_SCHEMA, 
            TC.TABLE_NAME, 
            KC.COLUMN_NAME
        FROM 
            INFORMATION_SCHEMA.TABLE_CONSTRAINTS TC
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE KC
                ON TC.CONSTRAINT_NAME = KC.CONSTRAINT_NAME
                AND TC.TABLE_SCHEMA = KC.TABLE_SCHEMA
        WHERE 
            TC.CONSTRAINT_TYPE = 'PRIMARY KEY'
        """
        pk_df = pd.read_sql(pk_query, conn)
        
        # Foreign keys
        fk_query = """
        SELECT 
            FK.TABLE_SCHEMA, 
            FK.TABLE_NAME, 
            FK.COLUMN_NAME AS FK_COLUMN,
            PK.TABLE_SCHEMA AS PK_TABLE_SCHEMA,
            PK.TABLE_NAME AS PK_TABLE_NAME, 
            PK.COLUMN_NAME AS PK_COLUMN
        FROM 
            INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS RC
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE FK
                ON RC.CONSTRAINT_NAME = FK.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE PK
                ON RC.UNIQUE_CONSTRAINT_NAME = PK.CONSTRAINT_NAME
        """
        fk_df = pd.read_sql(fk_query, conn)
        
        # Create documentation about relationships
        relations_doc = "Database Relationships:\n\n"
        
        # Add primary key info
        relations_doc += "Primary Keys:\n"
        for _, row in pk_df.iterrows():
            relations_doc += f"- {row['TABLE_SCHEMA']}.{row['TABLE_NAME']} has primary key on column {row['COLUMN_NAME']}\n"
        
        relations_doc += "\nForeign Keys:\n"
        for _, row in fk_df.iterrows():
            relations_doc += (
                f"- {row['TABLE_SCHEMA']}.{row['TABLE_NAME']}.{row['FK_COLUMN']} references "
                f"{row['PK_TABLE_SCHEMA']}.{row['PK_TABLE_NAME']}.{row['PK_COLUMN']}\n"
            )
        
        # Add relationships documentation
        vn.add_documentation(relations_doc)
        logger.info("Added relationship documentation")
        
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error loading schema: {str(e)}")
        return False

def load_sample_queries(vn, sample_file=None):
    """Load sample queries from a JSON file"""
    if not sample_file:
        sample_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_queries.json")
    
    if not os.path.exists(sample_file):
        logger.warning(f"Sample file {sample_file} not found")
        return False
    
    try:
        with open(sample_file, 'r') as f:
            samples = json.load(f)
        
        logger.info(f"Loading {len(samples)} sample queries")
        
        for i, sample in enumerate(samples):
            question = sample.get('question')
            sql = sample.get('sql')
            
            if question and sql:
                logger.info(f"Adding sample {i+1}: {question}")
                vn.add_question_sql(question, sql)
        
        return True
    except Exception as e:
        logger.error(f"Error loading samples: {str(e)}")
        return False

def extract_queries_from_logs(log_file="logs/app.log", output_file="extracted_queries.json"):
    """Extract successful queries from logs to build training set"""
    if not os.path.exists(log_file):
        logger.warning(f"Log file {log_file} not found")
        return False
    
    try:
        queries = []
        with open(log_file, 'r') as f:
            for line in f:
                if "Generated SQL:" in line and "Processing question:" in line:
                    try:
                        # Extract question
                        question_part = line.split("Processing question:")[1].split("Generated SQL:")[0].strip()
                        # Extract SQL
                        sql_part = line.split("Generated SQL:")[1].strip()
                        
                        queries.append({
                            "question": question_part,
                            "sql": sql_part
                        })
                    except:
                        pass
        
        if queries:
            with open(output_file, 'w') as f:
                json.dump(queries, f, indent=2)
            
            logger.info(f"Extracted {len(queries)} queries from logs to {output_file}")
            return True
        else:
            logger.warning("No queries found in logs")
            return False
    except Exception as e:
        logger.error(f"Error extracting queries from logs: {str(e)}")
        return False

def auto_verify_training_samples(vn, samples_file):
    """Verify training samples by running them and checking for errors"""
    try:
        with open(samples_file, 'r') as f:
            samples = json.load(f)
        
        valid_samples = []
        invalid_samples = []
        
        for sample in samples:
            question = sample.get('question')
            sql = sample.get('sql')
            
            if not question or not sql:
                invalid_samples.append(sample)
                continue
            
            try:
                # Try to run the SQL
                df = vn.run_sql(sql)
                if df is not None:
                    valid_samples.append(sample)
                else:
                    invalid_samples.append(sample)
            except Exception:
                invalid_samples.append(sample)
        
        with open("verified_queries.json", 'w') as f:
            json.dump(valid_samples, f, indent=2)
        
        with open("invalid_queries.json", 'w') as f:
            json.dump(invalid_samples, f, indent=2)
        
        logger.info(f"Verified {len(valid_samples)} valid samples and found {len(invalid_samples)} invalid samples")
        return len(valid_samples), len(invalid_samples)
    except Exception as e:
        logger.error(f"Error verifying samples: {str(e)}")
        return 0, 0

def create_sample_queries():
    """Create a sample queries JSON file if it doesn't exist"""
    sample_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_queries.json")
    
    if os.path.exists(sample_file):
        logger.info(f"Sample file {sample_file} already exists")
        return
    
    sample_queries = [
        {
            "question": "Show me the top 10 customers by total sales",
            "sql": "SELECT TOP 10 c.CustomerName, SUM(o.TotalAmount) AS TotalSales FROM Customers c JOIN Orders o ON c.CustomerID = o.CustomerID GROUP BY c.CustomerName ORDER BY TotalSales DESC"
        },
        {
            "question": "What were the sales by region last month?",
            "sql": "SELECT r.RegionName, SUM(o.TotalAmount) AS TotalSales FROM Region r JOIN Customers c ON r.RegionID = c.RegionID JOIN Orders o ON c.CustomerID = o.CustomerID WHERE DATEDIFF(month, o.OrderDate, GETDATE()) = 1 GROUP BY r.RegionName ORDER BY TotalSales DESC"
        },
        {
            "question": "List all products that are out of stock",
            "sql": "SELECT ProductName, CategoryName FROM Products p JOIN Categories c ON p.CategoryID = c.CategoryID WHERE StockQuantity = 0"
        },
        {
            "question": "How many orders were placed each month this year?",
            "sql": "SELECT MONTH(OrderDate) AS Month, COUNT(*) AS OrderCount FROM Orders WHERE YEAR(OrderDate) = YEAR(GETDATE()) GROUP BY MONTH(OrderDate) ORDER BY Month"
        },
        {
            "question": "What is the average order value by category?",
            "sql": "SELECT c.CategoryName, AVG(od.UnitPrice * od.Quantity) AS AvgOrderValue FROM Categories c JOIN Products p ON c.CategoryID = p.CategoryID JOIN OrderDetails od ON p.ProductID = od.ProductID GROUP BY c.CategoryName ORDER BY AvgOrderValue DESC"
        }
    ]
    
    with open(sample_file, 'w') as f:
        json.dump(sample_queries, f, indent=2)
    
    logger.info(f"Created sample file {sample_file} with {len(sample_queries)} queries")

def main():
    parser = argparse.ArgumentParser(description="Advanced training for Vanna")
    parser.add_argument("--schema", action="store_true", help="Load database schema")
    parser.add_argument("--samples", action="store_true", help="Load sample queries")
    parser.add_argument("--extract-logs", action="store_true", help="Extract queries from logs")
    parser.add_argument("--verify", type=str, help="Verify samples in the specified file")
    parser.add_argument("--sample-file", type=str, help="Sample file to load")
    parser.add_argument("--create-samples", action="store_true", help="Create a sample queries template file")
    parser.add_argument("--all", action="store_true", help="Run all training steps")
    
    args = parser.parse_args()
    
    # Create sample file if requested
    if args.create_samples:
        create_sample_queries()
        return
    
    # Extract queries from logs if requested
    if args.extract_logs:
        extract_queries_from_logs()
        return
    
    # Get Vanna instance
    vn = get_vanna_instance()
    
    # Verify samples if requested
    if args.verify:
        auto_verify_training_samples(vn, args.verify)
        return
    
    # Run all training steps if requested
    if args.all or args.schema:
        load_database_schema(vn)
    
    if args.all or args.samples:
        load_sample_queries(vn, args.sample_file)
    
    logger.info("Training complete!")

if __name__ == "__main__":
    main()

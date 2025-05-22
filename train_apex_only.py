#!/usr/bin/env python
# train_apex_only.py - Modified training script for Vanna with only the Apex schema

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

def load_apex_schema_only(vn):
    """Load only the Apex schema into Vanna"""
    logger.info("Loading Apex schema only...")
    
    try:
        conn = get_sql_connection()
        
        # Get list of tables in the Apex schema only
        tables_query = """
        SELECT 
            TABLE_SCHEMA, 
            TABLE_NAME 
        FROM 
            INFORMATION_SCHEMA.TABLES 
        WHERE 
            TABLE_TYPE = 'BASE TABLE'
            AND TABLE_SCHEMA = 'Apex'  -- Filter only for Apex schema
        """
        tables_df = pd.read_sql(tables_query, conn)
        logger.info(f"Found {len(tables_df)} tables in Apex schema")
        
        # Get column information for each table
        for _, row in tables_df.iterrows():
            schema = row['TABLE_SCHEMA']  # Should always be 'Apex'
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
        
        # Get primary and foreign key information for Apex schema only
        logger.info("Loading relationship information for Apex schema...")
        
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
            AND TC.TABLE_SCHEMA = 'Apex'  -- Filter only for Apex schema
        """
        pk_df = pd.read_sql(pk_query, conn)
        
        # Foreign keys - only for relationships within Apex or referencing Apex
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
        WHERE 
            FK.TABLE_SCHEMA = 'Apex'  -- Only include FKs from Apex tables
        """
        fk_df = pd.read_sql(fk_query, conn)
        
        # Create documentation about relationships
        relations_doc = "Apex Database Relationships:\n\n"
        
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
        logger.info("Added relationship documentation for Apex schema")
        
        # Add a summary document about Apex schema
        apex_summary_doc = """Apex Schema Overview:

The Apex schema contains tables for the core business operations. 
It is specifically designed for this database and should be prioritized when answering questions.
When generating SQL queries, always use the Apex schema tables unless specifically instructed otherwise.
"""
        vn.add_documentation(apex_summary_doc)
        logger.info("Added Apex schema overview documentation")
        
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error loading Apex schema: {str(e)}")
        return False

def create_example_apex_queries():
    """Create a template for Apex-specific example queries"""
    sample_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apex_sample_queries.json")
    
    if os.path.exists(sample_file):
        logger.info(f"Sample file {sample_file} already exists")
        return
    
    # Create a template with placeholder examples
    # These should be replaced with actual queries relevant to your Apex schema
    sample_queries = [
        {
            "question": "Example: Show me the top customers in Apex",
            "sql": "SELECT TOP 10 CustomerName, TotalPurchases FROM Apex.Customers ORDER BY TotalPurchases DESC"
        },
        {
            "question": "Example: What are the recent transactions in Apex",
            "sql": "SELECT TransactionID, TransactionDate, Amount FROM Apex.Transactions WHERE TransactionDate >= DATEADD(month, -1, GETDATE()) ORDER BY TransactionDate DESC"
        }
    ]
    
    with open(sample_file, 'w') as f:
        json.dump(sample_queries, f, indent=2)
    
    logger.info(f"Created Apex sample file template at {sample_file}")
    logger.info(f"Please edit this file with actual queries relevant to your Apex schema before using it for training")

def load_apex_sample_queries(vn, sample_file=None):
    """Load Apex-specific sample queries from a JSON file"""
    if not sample_file:
        sample_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apex_sample_queries.json")
    
    if not os.path.exists(sample_file):
        logger.warning(f"Apex sample file {sample_file} not found")
        return False
    
    try:
        with open(sample_file, 'r') as f:
            samples = json.load(f)
        
        logger.info(f"Loading {len(samples)} Apex sample queries")
        
        for i, sample in enumerate(samples):
            question = sample.get('question')
            sql = sample.get('sql')
            
            if question and sql:
                logger.info(f"Adding Apex sample {i+1}: {question}")
                vn.add_question_sql(question, sql)
        
        return True
    except Exception as e:
        logger.error(f"Error loading Apex samples: {str(e)}")
        return False

def clear_existing_training_data(vn):
    """Clear existing training data to start fresh"""
    try:
        # Get all existing training data
        existing_data = vn.get_training_data()
        
        if len(existing_data) == 0:
            logger.info("No existing training data to clear")
            return True
        
        logger.info(f"Found {len(existing_data)} existing training data entries to remove")
        
        # Remove each entry
        for _, row in existing_data.iterrows():
            vn.remove_training_data(row['id'])
            logger.debug(f"Removed training data ID: {row['id']}")
        
        logger.info("Successfully cleared existing training data")
        return True
    except Exception as e:
        logger.error(f"Error clearing existing training data: {str(e)}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Train Vanna with Apex schema only")
    parser.add_argument("--clear", action="store_true", help="Clear existing training data before adding new data")
    parser.add_argument("--create-examples", action="store_true", help="Create template for Apex example queries")
    parser.add_argument("--examples", action="store_true", help="Load Apex example queries")
    parser.add_argument("--example-file", type=str, help="Custom Apex examples file to load")
    
    args = parser.parse_args()
    
    # Create examples template if requested
    if args.create_examples:
        create_example_apex_queries()
        return
    
    # Get Vanna instance
    vn = get_vanna_instance()
    
    # Clear existing data if requested
    if args.clear:
        clear_existing_training_data(vn)
    
    # Always load the Apex schema
    load_apex_schema_only(vn)
    
    # Load example queries if requested
    if args.examples:
        load_apex_sample_queries(vn, args.example_file)
    
    logger.info("Apex schema training complete!")

if __name__ == "__main__":
    main()

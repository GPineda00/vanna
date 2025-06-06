from dotenv import load_dotenv
load_dotenv()

from functools import wraps
from flask import Flask, jsonify, Response, request, redirect, url_for
import flask
import os
import pandas as pd
from cache import MemoryCache

app = Flask(__name__, static_folder='static', static_url_path='')

# SETUP
cache = MemoryCache()

from vanna.ollama import Ollama
from vanna.chromadb import ChromaDB_VectorStore
from vanna.remote import VannaDefault

class MyVanna(Ollama, ChromaDB_VectorStore):
    def __init__(self, config=None):
        Ollama.__init__(self, config=config)
        ChromaDB_VectorStore.__init__(self, config=config)


vn = MyVanna(config={
    'model': os.environ.get('OLLAMA_MODEL', 'llama3.2'),  # Default to llama3.2 if not specified
    'ollama_host': os.environ.get('OLLAMA_HOST', 'http://localhost:11434'),  # Default Ollama host
    'ollama_timeout': 600.0,  # 10 minutes timeout (default is 240 seconds)
    'options': {
        'num_ctx': 8192,
        'temperature': 0.0  # Increase context window for large schemas
    },
    'keep_alive': '10m'  # Keep model loaded for 10 minutes
})
# Connect to Microsoft SQL Server

odbc_conn_str = (
    f"DRIVER={{{os.getenv('MSSQL_DRIVER')}}};"
    f"SERVER={os.getenv('MSSQL_SERVER')};"
    f"DATABASE={os.getenv('MSSQL_DATABASE')};"
    f"UID={os.getenv('MSSQL_USERNAME')};"
    f"PWD={os.getenv('MSSQL_PASSWORD')};"
)

# Connect to SQL Server
try:
    vn.connect_to_mssql(odbc_conn_str=odbc_conn_str)
    print("✓ Connected to SQL Server successfully")
    
    # Test database connection
    test_df = vn.run_sql("SELECT 1 as test_connection")
    print("✓ Database connection test successful")
    
except Exception as e:
    print(f"❌ Database connection failed: {e}")
    exit(1)


# NO NEED TO CHANGE ANYTHING BELOW THIS LINE
def requires_cache(fields):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            id = request.args.get('id')

            if id is None:
                return jsonify({"type": "error", "error": "No id provided"})
            
            for field in fields:
                if cache.get(id=id, field=field) is None:
                    return jsonify({"type": "error", "error": f"No {field} found"})
            
            field_values = {field: cache.get(id=id, field=field) for field in fields}
            
            # Add the id to the field_values
            field_values['id'] = id

            return f(*args, **field_values, **kwargs)
        return decorated
    return decorator

@app.route('/api/v0/generate_questions', methods=['GET'])
def generate_questions():
    return jsonify({
        "type": "question_list", 
        "questions": vn.generate_questions(),
        "header": "Here are some questions you can ask:"
        })

@app.route('/api/v0/generate_sql', methods=['GET', 'POST'])
def generate_sql():
    if flask.request.method == 'POST':
        question = flask.request.json.get('question')
    else:
        question = flask.request.args.get('question')

    if question is None:
        return jsonify({"type": "error", "error": "No question provided"})

    try:
        id = cache.generate_id(question=question)
        
        # Generate SQL with better error handling
        sql = vn.generate_sql(question=question, allow_llm_to_see_data=True)
        
        # Validate that we got actual SQL, not explanatory text
        if not sql or sql.strip() == "":
            return jsonify({"type": "error", "error": "No SQL generated. Please rephrase your question."})
        
        # Basic SQL validation - check if it contains SQL keywords
        sql_lower = sql.lower().strip()
        sql_keywords = ['select', 'insert', 'update', 'delete', 'create', 'alter', 'drop', 'show', 'describe']
        
        if not any(keyword in sql_lower for keyword in sql_keywords):
            return jsonify({
                "type": "error", 
                "error": f"Generated response doesn't appear to be valid SQL: {sql[:100]}..."
            })

        cache.set(id=id, field='question', value=question)
        cache.set(id=id, field='sql', value=sql)

        return jsonify({
            "type": "sql", 
            "id": id,
            "text": sql,
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "type": "error", 
            "error": f"Error generating SQL: {str(e)}"
        })

@app.route('/api/v0/run_sql', methods=['GET'])
@requires_cache(['sql'])
def run_sql(id: str, sql: str):
    try:
        # Additional SQL validation before execution
        sql_clean = sql.strip()
        if not sql_clean:
            return jsonify({"type": "error", "error": "Empty SQL query"})
        
        # Check for potential dangerous operations
        dangerous_keywords = ['drop', 'delete', 'truncate', 'alter', 'create']
        sql_lower = sql_clean.lower()
        
        for keyword in dangerous_keywords:
            if keyword in sql_lower and not sql_lower.startswith('select'):
                return jsonify({
                    "type": "error", 
                    "error": f"Potentially dangerous SQL operation detected: {keyword}. Only SELECT queries are allowed."
                })
        
        print(f"Executing SQL: {sql_clean}")
        df = vn.run_sql(sql=sql_clean)
        
        if df is None or df.empty:
            return jsonify({
                "type": "df", 
                "id": id,
                "df": "[]",
                "message": "Query executed successfully but returned no data."
            })

        cache.set(id=id, field='df', value=df)

        return jsonify({
            "type": "df", 
            "id": id,
            "df": df.head(10).to_json(orient='records'),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        
        error_msg = str(e)
        
        # Provide more helpful error messages
        if "syntax error" in error_msg.lower():
            error_msg += " - The generated SQL has syntax errors. Try rephrasing your question."
        elif "does not exist" in error_msg.lower():
            error_msg += " - The table or column referenced doesn't exist. Check your database schema."
        elif "permission" in error_msg.lower():
            error_msg += " - Database permission denied. Check your database access rights."
            
        return jsonify({
            "type": "error", 
            "error": f"SQL execution error: {error_msg}"
        })

@app.route('/api/v0/download_csv', methods=['GET'])
@requires_cache(['df'])
def download_csv(id: str, df):
    csv = df.to_csv()

    return Response(
        csv,
        mimetype="text/csv",
        headers={"Content-disposition":
                 f"attachment; filename={id}.csv"})

@app.route('/api/v0/generate_plotly_figure', methods=['GET'])
@requires_cache(['df', 'question', 'sql'])
def generate_plotly_figure(id: str, df, question, sql):
    try:
        code = vn.generate_plotly_code(question=question, sql=sql, df_metadata=f"Running df.dtypes gives:\n {df.dtypes}")
        fig = vn.get_plotly_figure(plotly_code=code, df=df, dark_mode=False)
        fig_json = fig.to_json()

        cache.set(id=id, field='fig_json', value=fig_json)

        return jsonify(
            {
                "type": "plotly_figure", 
                "id": id,
                "fig": fig_json,
            })
    except Exception as e:
        # Print the stack trace
        import traceback
        traceback.print_exc()

        return jsonify({"type": "error", "error": str(e)})

@app.route('/api/v0/get_training_data', methods=['GET'])
def get_training_data():
    df = vn.get_training_data()

    return jsonify(
    {
        "type": "df", 
        "id": "training_data",
        "df": df.head(25).to_json(orient='records'),
    })

@app.route('/api/v0/remove_training_data', methods=['POST'])
def remove_training_data():
    # Get id from the JSON body
    id = flask.request.json.get('id')

    if id is None:
        return jsonify({"type": "error", "error": "No id provided"})

    if vn.remove_training_data(id=id):
        return jsonify({"success": True})
    else:
        return jsonify({"type": "error", "error": "Couldn't remove training data"})

@app.route('/api/v0/clear_all_training_data', methods=['POST'])
def clear_all_training_data():
    try:
        # Get all training data first
        training_df = vn.get_training_data()
        
        if training_df.empty:
            return jsonify({
                "type": "success",
                "message": "No training data to clear.",
                "cleared_count": 0
            })
        
        cleared_count = 0
        failed_count = 0
        
        # Remove each training data entry
        for _, row in training_df.iterrows():
            try:
                if 'id' in row and vn.remove_training_data(id=row['id']):
                    cleared_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                print(f"Failed to remove training data {row.get('id', 'unknown')}: {e}")
                failed_count += 1
        
        if failed_count == 0:
            return jsonify({
                "type": "success",
                "message": f"Successfully cleared all {cleared_count} training data entries.",
                "cleared_count": cleared_count
            })
        else:
            return jsonify({
                "type": "warning",
                "message": f"Cleared {cleared_count} entries, but {failed_count} entries failed to clear.",
                "cleared_count": cleared_count,
                "failed_count": failed_count
            })
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "type": "error", 
            "error": f"Failed to clear training data: {str(e)}"
        })

@app.route('/api/v0/train', methods=['POST'])
def add_training_data():
    question = flask.request.json.get('question')
    sql = flask.request.json.get('sql')
    ddl = flask.request.json.get('ddl')
    documentation = flask.request.json.get('documentation')

    try:
        id = vn.train(question=question, sql=sql, ddl=ddl, documentation=documentation)

        return jsonify({"id": id})
    except Exception as e:
        print("TRAINING ERROR", e)
        return jsonify({"type": "error", "error": str(e)})

@app.route('/api/v0/train_schema', methods=['POST'])
def train_schema():
    try:
        # Get the schema name from the request
        schema_name = flask.request.json.get('schema_name', '').strip()
        
        if not schema_name:
            return jsonify({"type": "error", "error": "Please provide a schema name"})
        
        print(f"Starting schema training for schema: {schema_name}")
          # Get all tables for the specific schema
        tables_query = f"""
        SELECT 
            TABLE_SCHEMA,
            TABLE_NAME,
            TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_TYPE = 'BASE TABLE' 
        AND TABLE_SCHEMA = '{schema_name}'
        ORDER BY TABLE_NAME
        """
        
        tables_df = vn.run_sql(tables_query)
        
        if tables_df.empty:
            return jsonify({
                "type": "error", 
                "error": f"No tables found in schema '{schema_name}'. Please check the schema name."
            })
          # Get detailed column information for tables in the specific schema
        columns_query = f"""
        SELECT 
            c.TABLE_SCHEMA,
            c.TABLE_NAME,
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.IS_NULLABLE,
            c.COLUMN_DEFAULT,
            c.CHARACTER_MAXIMUM_LENGTH,
            c.NUMERIC_PRECISION,
            c.NUMERIC_SCALE,
            c.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS c
        INNER JOIN INFORMATION_SCHEMA.TABLES t 
            ON c.TABLE_SCHEMA = t.TABLE_SCHEMA 
            AND c.TABLE_NAME = t.TABLE_NAME
        WHERE t.TABLE_TYPE = 'BASE TABLE'
        AND c.TABLE_SCHEMA = '{schema_name}'
        ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
        """
        
        columns_df = vn.run_sql(columns_query)
        
        # Generate DDL statements for each table
        trained_tables = 0
        
        for _, table_row in tables_df.iterrows():
            table_name = table_row['TABLE_NAME']
            full_table_name = f"{schema_name}.{table_name}"
            
            # Get columns for this specific table
            table_columns = columns_df[
                (columns_df['TABLE_SCHEMA'] == schema_name) & 
                (columns_df['TABLE_NAME'] == table_name)
            ].copy()
            
            if not table_columns.empty:
                # Generate CREATE TABLE statement
                ddl = f"-- Table: {full_table_name}\nCREATE TABLE {full_table_name} (\n"
                
                column_definitions = []
                for _, col in table_columns.iterrows():
                    col_def = f"    {col['COLUMN_NAME']} {col['DATA_TYPE']}"
                    
                    # Add length/precision info
                    if col['CHARACTER_MAXIMUM_LENGTH'] and not pd.isna(col['CHARACTER_MAXIMUM_LENGTH']):
                        col_def += f"({int(col['CHARACTER_MAXIMUM_LENGTH'])})"
                    elif col['NUMERIC_PRECISION'] and not pd.isna(col['NUMERIC_PRECISION']):
                        if col['NUMERIC_SCALE'] and not pd.isna(col['NUMERIC_SCALE']) and col['NUMERIC_SCALE'] > 0:
                            col_def += f"({int(col['NUMERIC_PRECISION'])},{int(col['NUMERIC_SCALE'])})"
                        else:
                            col_def += f"({int(col['NUMERIC_PRECISION'])})"
                    
                    # Add nullability
                    if col['IS_NULLABLE'] == 'NO':
                        col_def += " NOT NULL"
                    
                    # Add default value
                    if col['COLUMN_DEFAULT'] and not pd.isna(col['COLUMN_DEFAULT']):
                        col_def += f" DEFAULT {col['COLUMN_DEFAULT']}"
                    
                    column_definitions.append(col_def)
                
                ddl += ",\n".join(column_definitions)
                ddl += "\n);"
                
                # Train with the DDL
                vn.train(ddl=ddl)
                trained_tables += 1
                
                print(f"✓ Trained table: {full_table_name}")
        
        # Train with schema-specific queries
        schema_queries = [
            {
                "question": f"What tables are in the {schema_name} schema?",
                "sql": f"SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = '{schema_name}' ORDER BY TABLE_NAME"
            },
            {
                "question": f"Show me all columns in the {schema_name} schema",
                "sql": f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{schema_name}' ORDER BY TABLE_NAME, ORDINAL_POSITION"
            },
            {
                "question": f"What are the column names and data types in {schema_name}?",
                "sql": f"SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS c WHERE c.TABLE_SCHEMA = '{schema_name}' ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION"
            }
        ]
        
        for query in schema_queries:
            vn.train(question=query["question"], sql=query["sql"])
        
        # Create a summary documentation
        table_list = tables_df['TABLE_NAME'].tolist()
        
        documentation = f"""
        Database Schema Summary for '{schema_name}':
        - Schema name: {schema_name}
        - Total tables: {len(table_list)}
        - Tables: {', '.join(table_list)}
        - All table structures have been trained with complete column definitions
        - Use {schema_name}.TABLE_NAME format when referencing tables in this schema
        """
        
        vn.train(documentation=documentation)
        
        return jsonify({
            "type": "success",
            "message": f"Successfully trained {trained_tables} tables from schema '{schema_name}'",
            "schema_name": schema_name,
            "tables_trained": trained_tables,
            "table_list": table_list
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "type": "error", 
            "error": f"Schema training failed: {str(e)}"
        })

@app.route('/api/v0/load_question', methods=['GET'])
@requires_cache(['question', 'sql', 'df', 'fig_json'])
def load_question(id: str, question, sql, df, fig_json):
    try:
        return jsonify(
            {
                "type": "question_cache", 
                "id": id,
                "question": question,
                "sql": sql,
                "df": df.head(10).to_json(orient='records'),
                "fig": fig_json,
            })

    except Exception as e:
        return jsonify({"type": "error", "error": str(e)})

@app.route('/api/v0/get_question_history', methods=['GET'])
def get_question_history():
    return jsonify({"type": "question_history", "questions": cache.get_all(field_list=['question']) })

@app.route('/')
def root():
    return app.send_static_file('index.html')

if __name__ == '__main__':
    app.run(debug=True)

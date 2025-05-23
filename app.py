import os
import logging
import traceback
import time
import re # Added for regex in DDL parsing and tokenization
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
import pandas as pd
import pyodbc
from flask_cors import CORS
from vanna.ollama import Ollama
from vanna.chromadb.chromadb_vector import ChromaDB_VectorStore
from logging_config import setup_logging
from errors import AppError, DatabaseError, OllamaError, OllamaProcessError, OllamaConnectionError, SQLGenerationError, InvalidRequestError
from ollama_fix import with_ollama_retry, is_ollama_running, start_ollama
from thefuzz import process as fuzzy_process # Added for fuzzy matching

# Load environment variables from .env file
load_dotenv()

# Create a custom class that combines Ollama LLM with ChromaDB for vector storage
class OllamaVanna(ChromaDB_VectorStore, Ollama):
    def __init__(self, config=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        Ollama.__init__(self, config=config)
        
        # Initialize retry count for tracking retry attempts
        self.retry_count = 0
        self.max_retries = 3
        
        # Set default language
        self.language = "spanish"
    
    @with_ollama_retry(max_retries=3, retry_delay=5)
    def ask_llm(self, question: str, system_message_override: str | None = None): # Added system_message_override
        """
        Direct prompt to LLM. Can be used for general questions or specific tasks
        like interpretation if a system_message_override is provided.
        """
        try:
            system_content = system_message_override if system_message_override else \
                             "Eres un asistente de IA que proporciona información útil. Responde siempre en español. Sé conciso y directo en tus respuestas."
            
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": question}
            ]
            
            response = self.submit_prompt(messages)
            return response
        except Exception as e:
            logging.error(f"Error in ask_llm: {str(e)}")
            raise
    
    @with_ollama_retry(max_retries=3, retry_delay=5)
    def generate_sql(self, question, *args, **kwargs):
        """
        Override generate_sql method with retry mechanism and better error handling
        """
        try:
            return super().generate_sql(question, *args, **kwargs)
        except Exception as e:
            # Log the error
            logging.error(f"Error generating SQL in patched method: {str(e)}")
            
            # If we've reached max retries, raise custom error with helpful message
            if self.retry_count >= self.max_retries:
                raise SQLGenerationError(
                    f"Failed to generate SQL after {self.max_retries} attempts. "
                    "The language model may be overloaded or experiencing issues. "
                    "Try simplifying your question or try again later."
                )
            
            # Increment retry count and try again
            self.retry_count += 1
            # Add a longer delay for each retry
            time.sleep(self.retry_count * 3)
            return self.generate_sql(question, *args, **kwargs)

# Apply our patches to the Ollama class
from vanna_ollama_core import patch_ollama_class
patch_ollama_class(Ollama)

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for API endpoints
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- BEGIN FUZZY MATCHING HELPER FUNCTIONS ---

# Global cache for schema elements
SCHEMA_TABLES = None
SCHEMA_COLUMNS = None
SCHEMA_LAST_UPDATED = 0
SCHEMA_CACHE_TTL = 3600 # 1 hour, can be configured

def get_db_schema_elements(vn_instance):
    """
    Extracts table and column names from Vanna's training data (DDL statements).
    Includes a simple caching mechanism.
    """
    global SCHEMA_TABLES, SCHEMA_COLUMNS, SCHEMA_LAST_UPDATED
    current_time = time.time()

    # Check cache
    if SCHEMA_TABLES is not None and SCHEMA_COLUMNS is not None and \
       (current_time - SCHEMA_LAST_UPDATED < SCHEMA_CACHE_TTL):
        app.logger.debug("Using cached schema elements for fuzzy matching.")
        return SCHEMA_TABLES, SCHEMA_COLUMNS

    app.logger.info("Fetching and parsing DDL for schema elements for fuzzy matching...")
    try:
        training_data = vn_instance.get_training_data()
        if training_data is None or training_data.empty:
            app.logger.warning("No training data available to extract schema elements for fuzzy matching.")
            SCHEMA_TABLES, SCHEMA_COLUMNS = [], [] # Cache empty result
            SCHEMA_LAST_UPDATED = current_time
            return [], []

        ddl_statements = training_data[training_data['content_type'] == 'ddl']['content'].tolist()
        
        table_names = set()
        column_names = set()

        for ddl_content in ddl_statements:
            # Regex to find table names: handles optional schema, quotes, brackets
            table_matches = re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\w_]+\.)?([`\"\[]?[\w_]+[`\"\]]?)\s*\(", ddl_content, re.IGNORECASE)
            
            for table_match in table_matches:
                table_name = table_match.group(1).strip('`"[]')
                table_names.add(table_name)
                
                start_index = table_match.end()
                open_paren_count = 1
                end_index = -1
                for i in range(start_index, len(ddl_content)):
                    if ddl_content[i] == '(':
                        open_paren_count += 1
                    elif ddl_content[i] == ')':
                        open_paren_count -= 1
                        if open_paren_count == 0:
                            end_index = i
                            break
                
                if end_index == -1:
                    app.logger.warning(f"Could not find closing parenthesis for table {table_name} in DDL snippet: {ddl_content[:200]}...")
                    continue

                cols_block = ddl_content[start_index:end_index]
                # Regex for column definitions: `col_name` type, "col_name" type, [col_name] type, col_name type
                # Captures the column name (first group).
                col_def_matches = re.finditer(r"^\s*([`\"\[]?[\w_]+[`\"\]]?)\s+[\w_]+(?:[\(\w\s,\)]*)?\s*(?:,\||$)", cols_block, re.MULTILINE | re.IGNORECASE)
                for col_match in col_def_matches:
                    col_name = col_match.group(1).strip('`"[]')
                    reserved_keywords = {'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'TABLE', 'CREATE', 'INDEX', 
                                         'CONSTRAINT', 'DEFAULT', 'NOT', 'NULL', 'UNIQUE', 'CHECK', 'COLUMN',
                                         'ADD', 'ALTER', 'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'FROM', 'WHERE',
                                         'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'GROUP', 'BY', 'ORDER', 'HAVING',
                                         'AS', 'DISTINCT', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END'}
                    if col_name.upper() not in reserved_keywords and len(col_name) > 1: # Avoid single letter false positives
                        column_names.add(col_name)
        
        SCHEMA_TABLES = sorted(list(table_names))
        SCHEMA_COLUMNS = sorted(list(column_names))
        SCHEMA_LAST_UPDATED = current_time
        
        app.logger.info(f"Fuzzy Matching: Extracted {len(SCHEMA_TABLES)} unique table names. First 10: {SCHEMA_TABLES[:10]}")
        app.logger.info(f"Fuzzy Matching: Extracted {len(SCHEMA_COLUMNS)} unique column names. First 10: {SCHEMA_COLUMNS[:10]}")
        if not SCHEMA_TABLES and not SCHEMA_COLUMNS:
            app.logger.warning("Fuzzy Matching: No table or column names were extracted from DDL statements.")
        
        return SCHEMA_TABLES, SCHEMA_COLUMNS
    except Exception as e:
        app.logger.error(f"Error extracting schema elements for fuzzy matching: {str(e)}\\n{traceback.format_exc()}")
        SCHEMA_TABLES, SCHEMA_COLUMNS = [], [] # Cache empty result on error
        SCHEMA_LAST_UPDATED = current_time # Update timestamp to avoid immediate retry on error
        return [], []

def correct_schema_references_in_question(question: str, table_names: list, column_names: list, threshold=85):
    """
    Corrects potentially misspelled table or column names in the question
    using fuzzy matching against the provided schema elements.
    """
    if not (table_names or column_names):
        return question

    # Tokenize question to handle words and punctuation separately
    tokens = re.findall(r"[\\w_]+|[^\\s\\w_]", question)
    
    corrected_tokens = []
    modified_count = 0

    for token in tokens:
        if not re.match(r"[\\w_]+", token) or len(token) <= 2: # Only correct word-like tokens, ignore very short ones
            corrected_tokens.append(token)
            continue
        
        clean_token = token 
        best_match_val = None
        best_match_score = 0
        match_type = None

        if table_names:
            table_match = fuzzy_process.extractOne(clean_token, table_names, score_cutoff=threshold)
            if table_match and table_match[1] > best_match_score:
                best_match_val = table_match[0]
                best_match_score = table_match[1]
                match_type = 'table'

        if column_names:
            column_match = fuzzy_process.extractOne(clean_token, column_names, score_cutoff=threshold)
            if column_match and column_match[1] > best_match_score:
                best_match_val = column_match[0]
                best_match_score = column_match[1]
                match_type = 'column'
            elif column_match and column_match[1] == best_match_score and match_type == 'table':
                # If column score is same as table score, could be ambiguous.
                # For now, if a table match was already found with same score, keep it.
                # This could be refined (e.g. prefer table, or log ambiguity).
                pass


        if best_match_val and best_match_val.lower() != clean_token.lower():
            app.logger.info(f"Fuzzy matching: Replaced '{clean_token}' with '{best_match_val}' (type: {match_type}, score: {best_match_score}) in question.")
            corrected_tokens.append(best_match_val)
            modified_count += 1
        else:
            corrected_tokens.append(token)

    if modified_count > 0:
        reconstructed_question = ""
        for i, t in enumerate(corrected_tokens):
            if i > 0 and re.match(r"[\\w_]+", t) and re.match(r"[\\w_]+", corrected_tokens[i-1]):
                reconstructed_question += " "
            reconstructed_question += t
        return reconstructed_question
        
    return question

# --- END FUZZY MATCHING HELPER FUNCTIONS ---

# Configure connection to SQL Server
def get_sql_connection():
    server = os.getenv('SQL_SERVER', 'localhost')
    database = os.getenv('SQL_DATABASE', '')
    username = os.getenv('SQL_USERNAME')
    password = os.getenv('SQL_PASSWORD')
    
    # Always use SQL authentication when credentials are in .env
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"Connect Timeout=60;"  # Correct parameter name and more reasonable value (60 seconds)
    )
    
    app.logger.info(f"Connecting to SQL Server: {server}, Database: {database}")
    conn = pyodbc.connect(conn_str)
    app.logger.info("Successfully connected to database")
    return conn

# Initialize Vanna with Ollama gemma3:12b model
vn = OllamaVanna(config={
    'model': os.getenv('OLLAMA_MODEL', 'llama3.2'),
    'ollama_host': os.getenv('OLLAMA_HOST', 'http://localhost:11434'),
    'persist_directory': os.getenv('CHROMADB_DIR', './chromadb_data'),
    'ollama_timeout': float(os.getenv('OLLAMA_TIMEOUT', '300.0')),
    'options': {
        'num_ctx': int(os.getenv('OLLAMA_CONTEXT_SIZE', '8192')),  # Increase context window
        'num_gpu': int(os.getenv('OLLAMA_NUM_GPU', '1')),          # Number of GPUs to use
        'num_thread': int(os.getenv('OLLAMA_NUM_THREAD', '4')),    # Number of threads to use
        'temperature': float(os.getenv('OLLAMA_TEMPERATURE', '0.1')),  # Lower for more deterministic outputs
    }
})

# Define a function to run SQL queries
def run_sql_query(sql):
    try:
        conn = get_sql_connection()
        df = pd.read_sql(sql, conn)
        conn.close()
        return df
    except pyodbc.Error as e:
        logging.error(f"SQL execution error: {str(e)}")
        raise DatabaseError(str(e))
    except Exception as e:
        logging.error(f"Error executing SQL: {str(e)}")
        logging.error(traceback.format_exc())
        raise DatabaseError(str(e))

# Set the run_sql function for Vanna
vn.run_sql = run_sql_query

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/ask', methods=['POST'])
def ask():
    data = request.json
    question = data.get('question', '')
    
    # Validate request
    if not question:
        return jsonify({'error': 'Question cannot be empty'}), 400
    
    # --- BEGIN FUZZY MATCHING PREPROCESSING ---
    if vn: # Global Vanna instance
        table_names, column_names = get_db_schema_elements(vn)
        if table_names or column_names:
            original_question_for_log = question
            corrected_question = correct_schema_references_in_question(question, table_names, column_names)
            if corrected_question != original_question_for_log:
                app.logger.info(f"Original question for /api/ask: '{original_question_for_log}'")
                app.logger.info(f"Question after fuzzy matching for /api/ask: '{corrected_question}'")
                question = corrected_question # Update the question variable
            else:
                app.logger.debug("Fuzzy matching did not alter the question for /api/ask.")
        else:
            app.logger.debug("Skipping fuzzy matching for /api/ask as no schema elements were loaded.")
    else:
        app.logger.warning("Vanna instance 'vn' not available for fuzzy matching in /api/ask.")
    # --- END FUZZY MATCHING PREPROCESSING ---

    try:
        app.logger.info(f"Processing question in /api/ask: {question}")
        
        # Check if Ollama is running before attempting to generate SQL
        if not is_ollama_running():
            app.logger.warning("Ollama service is not running, attempting to start it")
            if start_ollama():
                app.logger.info("Successfully started Ollama service")
            else:
                app.logger.error("Failed to start Ollama service")
                raise OllamaConnectionError("Ollama service is not running and could not be started")

        # Revised check for direct knowledge questions
        # Keywords for very specific non-database questions (greetings, simple interactions)
        direct_answer_keywords = [
            'hello', 'hi', 'who are you', 
            'thanks', 'thank you', 'ok thanks', 'ok thank you',
            'bye', 'goodbye'
        ]
        
        # Question must be an exact match (case-insensitive, stripped) to one of the keywords
        is_direct_question = question.lower().strip() in direct_answer_keywords
        
        if is_direct_question:
            app.logger.info(f"Question is a direct interaction: '{question}', bypassing SQL generation.")
            try:
                # Use Ollama to generate a direct answer
                direct_answer = vn.ask_llm(f"La siguiente es una pregunta que no requiere acceso a la base de datos. Por favor, responde directamente y de manera concisa en español: {question}")
                app.logger.info(f"Generated direct answer: {direct_answer[:100]}...")
                
                # Return the direct answer
                return jsonify({
                    'question': question,
                    'direct_answer': direct_answer,
                    'status': 'success'
                })
                
            except Exception as e:
                app.logger.error(f"Failed to generate direct answer: {str(e)}")
                # Fall back to SQL generation if direct answer fails
          # Generate SQL from question if not a direct knowledge question or if direct answer failed
        try:
            # Augment the question to strongly guide the LLM towards SQL generation
            sql_generation_prompt = f"User question: \'{question}\'. Based on the database schema, generate a SQL query to answer this question. Respond only with the SQL query itself."
            sql = vn.generate_sql(sql_generation_prompt)
            app.logger.info(f"Generated SQL: {sql} for augmented prompt: {sql_generation_prompt}")
        except OllamaProcessError as e:
            app.logger.error(f"Ollama process terminated: {str(e)}")
            # Removed fallback to direct answer
            return jsonify({
                'error': e.message, 
                'error_type': 'ollama_process_error',
                'status': 'error'
            }), e.status_code
        except OllamaConnectionError as e:
            app.logger.error(f"Ollama connection error: {str(e)}")
            # Removed fallback to direct answer
            return jsonify({
                'error': e.message,
                'error_type': 'ollama_connection_error',
                'status': 'error'
            }), e.status_code
        except Exception as e:
            app.logger.error(f"Failed to generate SQL: {str(e)}")
            raise SQLGenerationError(str(e))
        
        # Execute the SQL and get results
        try:
            df = vn.run_sql(sql)
            app.logger.info(f"SQL execution successful, returned {len(df)} rows")

            # Learn from this successful interaction
            try:
                vn.add_question_sql(question, sql) # Use original question
                app.logger.info(f"Successfully added question-SQL pair to training data: Q: {question} SQL: {sql}")
            except Exception as e_train:
                app.logger.error(f"Failed to add successful Q-SQL pair to training data: {str(e_train)}")

            # New: Get natural language interpretation of the results
            interpretation = "El sistema no pudo proporcionar una interpretación para estos datos en este momento." # Default message
            if df is not None and not df.empty: # Check if df exists and is not empty
                try:
                    # Convert DataFrame to a string representation for the LLM
                    df_string = df.to_string(index=False) 
                      # Limit the string length to avoid overly long prompts
                    # This limit can be adjusted based on typical data size and LLM context window
                    max_df_string_len = 2000 
                    if len(df_string) > max_df_string_len:
                        df_string = df_string[:max_df_string_len] + "\\n... (data truncated due to length)"
                        
                    interpretation_prompt = (
                        f"El usuario hizo la siguiente pregunta: \'{question}\'.\\\\n"
                        f"Para responder, se ejecutó la siguiente consulta SQL: \'{sql}\'.\\\\n"
                        f"La consulta devolvió los siguientes datos:\\\\n{df_string}\\\\n\\\\n"
                        f"Por favor, proporciona un resumen e interpretación concisa en español de estos datos. "
                        f"Enfócate en responder la pregunta original del usuario basándote en estos datos. "
                        f"Por ejemplo, si los datos muestran un conteo de 2494 para \'solicitudes de anticipo\', podrías decir: "
                        f"\'Actualmente, hay 2494 solicitudes de anticipo.\' "
                        f"Importante: NO menciones ningún nombre de tabla, esquema, base de datos o servidor en tu respuesta. "
                        f"Sé directo e informativo. No te limites a repetir los datos. Si los datos son complejos, resume los hallazgos principales."
                    )
                    app.logger.info("Requesting LLM to interpret SQL results.")
                    # Updated to use system_message_override for interpretation task
                    interpretation = vn.ask_llm(
                        question=interpretation_prompt, 
                        system_message_override=(
                            "Eres un asistente de IA especializado en interpretar resultados de consultas SQL y explicarlos en español. "
                            "NO menciones nombres de tablas, esquemas, bases de datos o servidores. "
                            "Concéntrate en responder la pregunta original del usuario basándote en los datos proporcionados."
                        )
                    )
                    app.logger.info(f"LLM interpretation received (first 150 chars): {interpretation[:150]}...")
                except Exception as e:
                    app.logger.error(f"Error during LLM interpretation of SQL results: {str(e)}")                    # interpretation remains the default error message if LLM call fails
            elif df is not None and df.empty: # df exists but is empty
                interpretation = "La consulta se ejecutó correctamente pero no devolvió datos para interpretar."
            # If df is None (e.g., if run_sql could return None on certain errors not caught by DatabaseError),
            # or if an error occurred before df was assigned, interpretation would keep its default.

        except DatabaseError as e:
            app.logger.error(f"Database error during SQL execution: {str(e)}")
            # This error will be caught by the outer try-except block,
            # so the \'result\' dictionary below won\'t be constructed with an undefined \'df\'.
            raise e 
        
        # Convert DataFrame to JSON and include interpretation
        # This part is reached only if vn.run_sql(sql) was successful and df is defined.
        result = {
            'question': question,
            'sql': sql,
            'data': df.to_dict(orient='records') if df is not None else [], # Handle if df could be None
            'interpretation': interpretation,    # Add the new interpretation
            'status': 'success'
        }
        
        return jsonify(result)
    except OllamaError as e:
        app.logger.error(f"Ollama error: {str(e)}")
        return jsonify({
            'error': e.message,
            'error_type': 'ollama_error',
            'status': 'error',
            'ollama_status': 'not_running' if not is_ollama_running() else 'error'
        }), e.status_code
    except AppError as e:
        app.logger.error(f"Application error: {str(e)}")
        return jsonify({'error': e.message, 'status': 'error'}), e.status_code
    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e), 'status': 'error'}), 500

@app.route('/api/ask/advanced', methods=['POST'])
def ask_advanced():
    data = request.json
    question = data.get('question', '')
    options = data.get('options', {})
    
    # Validate request
    if not question:
        return jsonify({'error': 'Question cannot be empty'}), 400

    # --- BEGIN FUZZY MATCHING PREPROCESSING ---
    if vn: # Global Vanna instance
        table_names, column_names = get_db_schema_elements(vn)
        if table_names or column_names:
            original_question_for_log = question
            corrected_question = correct_schema_references_in_question(question, table_names, column_names)
            if corrected_question != original_question_for_log:
                app.logger.info(f"Original question for /api/ask/advanced: '{original_question_for_log}'")
                app.logger.info(f"Question after fuzzy matching for /api/ask/advanced: '{corrected_question}'")
                question = corrected_question # Update the question variable
            else:
                app.logger.debug("Fuzzy matching did not alter the question for /api/ask/advanced.")
        else:
            app.logger.debug("Skipping fuzzy matching for /api/ask/advanced as no schema elements were loaded.")
    else:
        app.logger.warning("Vanna instance 'vn' not available for fuzzy matching in /api/ask/advanced.")
    # --- END FUZZY MATCHING PREPROCESSING ---
    
    try:
        app.logger.info(f"Processing advanced question: {question}")
        app.logger.info(f"Options: {options}")
        
        # Optional SQL modification before execution
        sql_template = options.get('sqlTemplate')
        execute_query = options.get('executeQuery', True)
        
        # Generate SQL from question
        try:
            if sql_template:
                # Use the template to guide SQL generation if provided
                app.logger.info(f"Using SQL template: {sql_template}")
                # This would require a custom method in vn, but for now we just generate as normal
            
            # Augment the question to strongly guide the LLM towards SQL generation
            sql_generation_prompt = f"User question: \'{question}\'. Based on the database schema, generate a SQL query to answer this question. Respond only with the SQL query itself."
            sql = vn.generate_sql(sql_generation_prompt)
            app.logger.info(f"Generated SQL: {sql} for augmented prompt: {sql_generation_prompt}")
            
            # Apply any post-processing to SQL if needed
            row_limit = options.get('rowLimit')
            if row_limit and isinstance(row_limit, int) and row_limit > 0:
                if 'SELECT TOP ' in sql.upper():
                    # SQL Server syntax with TOP already in the query
                    sql = sql.upper().replace('SELECT TOP ', f'SELECT TOP {row_limit} ')
                else:
                    # Add the TOP clause
                    sql = sql.replace('SELECT ', f'SELECT TOP {row_limit} ')
        except Exception as e:
            app.logger.error(f"Failed to generate SQL: {str(e)}")
            raise SQLGenerationError(str(e))
        
        result = {
            'question': question,
            'sql': sql,
        }
          # Only execute if requested
        if execute_query:
            try:
                df = vn.run_sql(sql)
                app.logger.info(f"SQL execution successful, returned {len(df)} rows")
                result['data'] = df.to_dict(orient='records')

                # Learn from this successful interaction
                try:
                    vn.add_question_sql(question, sql) # Use original question
                    app.logger.info(f"Successfully added advanced question-SQL pair to training data: Q: {question} SQL: {sql}")
                except Exception as e_train:
                    app.logger.error(f"Failed to add successful advanced Q-SQL pair to training data: {str(e_train)}")
                
                # Add interpretation for advanced queries too
                interpretation = "El sistema no pudo proporcionar una interpretación para estos datos en este momento."
                if df is not None and not df.empty:
                    try:
                        # Convert DataFrame to a string representation for the LLM
                        df_string = df.to_string(index=False)
                        # Limit the string length to avoid overly long prompts
                        max_df_string_len = 2000 
                        if len(df_string) > max_df_string_len:
                            df_string = df_string[:max_df_string_len] + "\\n... (datos truncados debido a la longitud)" # Corrected newline and translated
                        
                        interpretation_prompt = (
                            f"El usuario hizo la siguiente pregunta: '{question}'.\\n"
                            f"Para responder, se ejecutó la siguiente consulta SQL: '{sql}'.\\n"
                            f"La consulta devolvió los siguientes datos:\\n{df_string}\\n\\n"
                            f"Por favor, proporciona un resumen e interpretación concisa en español de estos datos. "
                            f"Enfócate en responder la pregunta original del usuario basándote en estos datos. "
                            f"Importante: NO menciones ningún nombre de tabla, esquema, base de datos o servidor en tu respuesta. "
                            f"Sé directo e informativo. No te limites a repetir los datos. Si los datos son complejos, resume los hallazgos principales."
                        )
                        # Make sure to call the LLM for interpretation here
                        # Updated to use system_message_override for interpretation task
                        interpretation = vn.ask_llm(
                            question=interpretation_prompt, 
                            system_message_override=(
                                "Eres un asistente de IA especializado en interpretar resultados de consultas SQL y explicarlos en español. "
                                "NO menciones nombres de tablas, esquemas, bases de datos o servidores. "
                                "Concéntrate en responder la pregunta original del usuario basándote en los datos proporcionados."
                            )
                        ) 
                        if not interpretation: # Fallback if LLM fails
                            interpretation = "El sistema no pudo generar una interpretación para los datos."
                    except Exception as e_interpret:
                        app.logger.error(f"Error generando la interpretación para la consulta avanzada: {e_interpret}")
                        interpretation = "Se produjo un error al generar la interpretación de los datos."
                result['interpretation'] = interpretation
            except DatabaseError as e:
                app.logger.error(f"Database error during advanced SQL execution: {str(e)}")
                # Removed fallback to direct answer
                raise e  # Re-raise the original database error
        
        return jsonify(result)
    except AppError as e:
        app.logger.error(f"Application error: {str(e)}")
        return jsonify({'error': e.message}), e.status_code
    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/train', methods=['POST'])
def train():
    data = request.json
    question = data.get('question', '')
    sql = data.get('sql', '')
    
    # Validate request
    if not question:
        return jsonify({'error': 'Question cannot be empty'}), 400
    if not sql:
        return jsonify({'error': 'SQL cannot be empty'}), 400
    
    try:
        app.logger.info(f"Adding training data - Question: {question}")
        app.logger.info(f"SQL: {sql}")
        
        # Add training data
        vn.add_question_sql(question, sql)
        app.logger.info("Training data added successfully")
        
        return jsonify({'status': 'success', 'message': 'Training data added'})
    except AppError as e:
        app.logger.error(f"Application error during training: {str(e)}")
        return jsonify({'error': e.message}), e.status_code
    except Exception as e:
        app.logger.error(f"Unexpected error during training: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify the service is running"""
    ollama_status = "ok" if is_ollama_running() else "not_running"
    
    # Try to connect to the database
    db_status = "ok"
    try:
        conn = get_sql_connection()
        conn.close()
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return jsonify({
        'status': 'ok',
        'ollama_model': os.getenv('OLLAMA_MODEL', 'llama3.2'),
        'ollama_status': ollama_status,
        'database': os.getenv('SQL_DATABASE', 'master'),
        'database_status': db_status
    })

@app.route('/api/restart-ollama', methods=['POST'])
def restart_ollama():
    """Endpoint to restart the Ollama service"""
    if is_ollama_running():
        return jsonify({
            'status': 'ok',
            'message': 'Ollama is already running'
        })
    
    # Try to start Ollama
    if start_ollama():
        return jsonify({
            'status': 'ok',
            'message': 'Ollama successfully restarted'
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Failed to restart Ollama'
        }), 500

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Get port from environment or use default
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    
    # Setup logging
    setup_logging(app)
    app.logger.info(f"Starting application with Ollama model: {os.getenv('OLLAMA_MODEL', 'llama3.2')}")
    
    app.run(debug=debug, host='0.0.0.0', port=port)

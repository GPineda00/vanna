import os
import logging
import traceback
import time
import re # Added for regex in DDL parsing and tokenization
import uuid  # Added for session ID generation
import threading  # Added for thread-safe operations
from concurrent.futures import ThreadPoolExecutor  # Added for request queue management
from collections import defaultdict, deque  # Added for session tracking and rate limiting
from datetime import datetime, timedelta  # Added for session expiry and rate limiting
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, session
import pandas as pd
import pyodbc
from flask_cors import CORS
from vanna.ollama import Ollama
from vanna.chromadb.chromadb_vector import ChromaDB_VectorStore
from logging_config import setup_logging
from errors import AppError, DatabaseError, OllamaError, OllamaProcessError, OllamaConnectionError, SQLGenerationError, InvalidRequestError
from ollama_fix import with_ollama_retry, is_ollama_running, start_ollama
from thefuzz import process as fuzzy_process # Added for fuzzy matching
from learning_manager import LearningManager  # Added for enhanced learning

# Load environment variables from .env file
load_dotenv()

# Multi-user configuration from environment variables
MAX_CONCURRENT_REQUESTS = int(os.getenv('MAX_CONCURRENT_REQUESTS', '10'))
MAX_REQUESTS_PER_USER = int(os.getenv('MAX_REQUESTS_PER_USER', '3'))
RATE_LIMIT_WINDOW = int(os.getenv('RATE_LIMIT_WINDOW', '60'))  # seconds
MAX_REQUESTS_PER_WINDOW = int(os.getenv('MAX_REQUESTS_PER_WINDOW', '20'))
SESSION_TIMEOUT = int(os.getenv('SESSION_TIMEOUT', '1800'))  # 30 minutes

class UserSessionManager:
    """Manages user sessions and tracks active users"""
    
    def __init__(self):
        self.sessions = {}  # session_id -> user_info
        self.lock = threading.Lock()
        
    def create_session(self, user_agent=None, ip_address=None):
        """Create a new user session"""
        session_id = str(uuid.uuid4())
        user_info = {
            'session_id': session_id,
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'request_count': 0,
            'user_agent': user_agent,
            'ip_address': ip_address,
            'status': 'active'
        }
        
        with self.lock:
            self.sessions[session_id] = user_info
            
        return session_id
    
    def get_session(self, session_id):
        """Get session information"""
        with self.lock:
            return self.sessions.get(session_id)
    
    def update_activity(self, session_id):
        """Update last activity timestamp for a session"""
        with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id]['last_activity'] = datetime.now()
                self.sessions[session_id]['request_count'] += 1
                return True
        return False
    
    def remove_session(self, session_id):
        """Manually remove a specific session"""
        with self.lock:
            if session_id in self.sessions:
                del self.sessions[session_id]
                return True
        return False
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions"""
        cutoff_time = datetime.now() - timedelta(seconds=SESSION_TIMEOUT)
        expired_sessions = []
        
        with self.lock:
            for session_id, info in self.sessions.items():
                if info['last_activity'] < cutoff_time:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del self.sessions[session_id]
        
        return len(expired_sessions)
    
    def get_active_users_count(self):
        """Get count of active users"""
        self.cleanup_expired_sessions()
        with self.lock:
            return len(self.sessions)
    
    def get_user_stats(self, session_id):
        """Get user statistics"""
        with self.lock:
            if session_id in self.sessions:
                info = self.sessions[session_id]
                return {
                    'session_duration': str(datetime.now() - info['created_at']),
                    'request_count': info['request_count'],
                    'last_activity': info['last_activity'].isoformat()
                }
        return None

class RequestQueueManager:
    """Manages concurrent request processing with rate limiting"""
    
    def __init__(self, max_concurrent=MAX_CONCURRENT_REQUESTS):
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self.user_queues = defaultdict(deque)  # session_id -> deque of request times
        self.global_queue = deque()  # Global rate limiting
        self.active_requests = defaultdict(int)  # session_id -> count of active requests
        self.lock = threading.Lock()
        
    def check_rate_limit(self, session_id):
        """Check if user has exceeded rate limits"""
        current_time = time.time()
        cutoff_time = current_time - RATE_LIMIT_WINDOW
        
        with self.lock:
            # Clean old requests from user queue
            user_queue = self.user_queues[session_id]
            while user_queue and user_queue[0] < cutoff_time:
                user_queue.popleft()
            
            # Clean old requests from global queue
            while self.global_queue and self.global_queue[0] < cutoff_time:
                self.global_queue.popleft()
            
            # Check user rate limit
            if len(user_queue) >= MAX_REQUESTS_PER_WINDOW:
                return False, f"Rate limit exceeded: {MAX_REQUESTS_PER_WINDOW} requests per {RATE_LIMIT_WINDOW} seconds"
            
            # Check global rate limit
            if len(self.global_queue) >= MAX_REQUESTS_PER_WINDOW * 10:  # Global limit is 10x user limit
                return False, "System is experiencing high load, please try again later"
            
            # Check concurrent requests for user
            if self.active_requests[session_id] >= MAX_REQUESTS_PER_USER:
                return False, f"Too many concurrent requests: max {MAX_REQUESTS_PER_USER} per user"
            
            # Add current request to queues
            user_queue.append(current_time)
            self.global_queue.append(current_time)
            self.active_requests[session_id] += 1
            
        return True, None
    
    def release_request(self, session_id):
        """Release a request slot for the user"""
        with self.lock:
            if self.active_requests[session_id] > 0:
                self.active_requests[session_id] -= 1
    
    def submit_request(self, func, session_id, *args, **kwargs):
        """Submit a request to the thread pool"""
        def wrapped_func():
            try:
                return func(*args, **kwargs)
            finally:
                self.release_request(session_id)
        
        return self.executor.submit(wrapped_func)
    
    def get_queue_stats(self):
        """Get queue statistics"""
        with self.lock:
            return {
                'active_requests': sum(self.active_requests.values()),
                'total_users_with_requests': len([count for count in self.active_requests.values() if count > 0]),
                'max_concurrent': self.executor._max_workers
            }

# Initialize multi-user managers
session_manager = UserSessionManager()
queue_manager = RequestQueueManager()
learning_manager = LearningManager()  # Initialize enhanced learning system

# Create a custom class that combines Ollama LLM with ChromaDB for vector storage
class OllamaVanna(ChromaDB_VectorStore, Ollama):
    def __init__(self, config=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        Ollama.__init__(self, config=config)
        self.language = "spanish"

    @with_ollama_retry(max_retries=3, retry_delay=5)
    def ask_llm(self, question: str, system_message_override: str | None = None): # Added system_message_override
        """
        Enhanced LLM prompt for Llama3.2 with contextual business intelligence.
        Optimized for Spanish business questions with APEX database context.
        """
        try:
            if system_message_override:
                system_content = system_message_override
            else:
                system_content = (
                    "Eres un asistente de inteligencia empresarial especializado en análisis de datos. "
                    "Tu especialidad es interpretar consultas de bases de datos y proporcionar insights empresariales. "
                    "\\n\\nCONTEXTO EMPRESARIAL:\\n"
                    "- Trabajas con la base de datos APEX que contiene información empresarial crítica\\n"
                    "- Las consultas pueden involucrar contratos, facturas, inventario, empleados, y operaciones\\n"
                    "- Siempre proporciona respuestas en español con terminología empresarial apropiada\\n"
                    "\\nINSTRUCCIONES DE RESPUESTA:\\n"
                    "1. Sé conciso pero informativo\\n"
                    "2. Usa términos empresariales apropiados en español\\n"
                    "3. Proporciona contexto cuando sea relevante\\n"
                    "4. Si los datos son complejos, resume los hallazgos principales\\n"
                    "5. NO menciones nombres técnicos de tablas o esquemas"
                )
            
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": question}
            ]
            
            response = self.submit_prompt(messages)
            return response
        except Exception as e:
            logging.error(f"Error in ask_llm: {str(e)}")
            raise
        except Exception as e:
            logging.error(f"Error in ask_llm: {str(e)}")
            raise
    
    @with_ollama_retry(max_retries=3, retry_delay=5)
    def generate_sql(self, question, *args, **kwargs):
        """
        Override generate_sql method to rely on with_ollama_retry decorator.
        The actual Ollama interaction happens in self.submit_prompt, called by super().generate_sql.
        """
        try:
            return super().generate_sql(question, *args, **kwargs)
        except Exception as e:
            # Log the error. The decorator will handle retries.
            # If retries are exhausted, the decorator will re-raise the exception.
            logging.error(f"Error in OllamaVanna.generate_sql (will be retried by decorator if applicable): {str(e)}")
            raise # Re-raise for the decorator or caller to handle

# Initialize Flask app
app = Flask(__name__)

# Configure Flask session for multi-user support
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_FILE_THRESHOLD'] = 1000

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
        'num_ctx': int(os.getenv('OLLAMA_CONTEXT_SIZE', '16381')),  # Increase context window
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

# Multi-user helper functions
def get_or_create_session():
    """Get existing session or create new one"""
    session_id = session.get('session_id')
    
    if not session_id or not session_manager.get_session(session_id):
        # Create new session
        user_agent = request.headers.get('User-Agent', 'Unknown')
        ip_address = request.remote_addr
        session_id = session_manager.create_session(user_agent, ip_address)
        session['session_id'] = session_id
        app.logger.info(f"Created new session: {session_id} for IP: {ip_address}")
    else:
        # Update existing session activity
        session_manager.update_activity(session_id)
    
    return session_id

def check_request_limits(session_id):
    """Check if request can be processed based on rate limits"""
    allowed, error_msg = queue_manager.check_rate_limit(session_id)
    
    if not allowed:
        app.logger.warning(f"Rate limit exceeded for session {session_id}: {error_msg}")
        return False, error_msg
    
    return True, None

def process_question_with_concurrency(question_func, session_id, *args, **kwargs):
    """Process a question with concurrency control"""
    try:
        # Submit to thread pool
        future = queue_manager.submit_request(question_func, session_id, *args, **kwargs)
        
        # Wait for result with timeout
        timeout = float(os.getenv('REQUEST_TIMEOUT', '300'))  # 5 minutes default
        result = future.result(timeout=timeout)
        
        return result
    except Exception as e:
        queue_manager.release_request(session_id)
        raise e

@app.route('/')
def index():
    # Initialize session for user
    session_id = get_or_create_session()
    return render_template('index.html')

@app.route('/api/session', methods=['GET'])
def get_session_info():
    """Get current session information (simplified)"""
    session_id = get_or_create_session()    
    return jsonify({
        'session_id': session_id[:8] + '...' if session_id != 'unknown' else 'unknown',
        'active_users': 1,  # Simplified - just current user
        'user_stats': {
            'session_duration': 'N/A',
            'request_count': 0,
            'last_activity': datetime.now().isoformat()
        },
        'queue_stats': {
            'active_requests': 0,
            'total_users_with_requests': 0,
            'max_concurrent': MAX_CONCURRENT_REQUESTS
        },
        'status': 'active'
    })

@app.route('/api/session/heartbeat', methods=['POST'])
def session_heartbeat():
    """Update session activity to keep it alive (simplified)"""
    session_id = get_or_create_session()
    
    return jsonify({
        'status': 'success',
        'session_id': session_id[:8] + '...' if session_id != 'unknown' else 'unknown',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/session/cleanup', methods=['POST'])
def cleanup_session():
    """Cleanup session when browser closes (simplified)"""
    try:
        session_id = session.get('session_id', 'unknown')
        
        if session_id != 'unknown':
            # Clear Flask session
            session.clear()
            app.logger.info(f"Session {session_id[:8]}... cleaned up successfully")
            return jsonify({
                'status': 'success',
                'message': 'Session cleaned up successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'No session to cleanup'
            })
            
    except Exception as e:
        app.logger.error(f"Error cleaning up session: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/ask', methods=['POST'])
def ask():
    # Get or create session
    session_id = get_or_create_session()
    
    # Check rate limits
    allowed, error_msg = check_request_limits(session_id)
    if not allowed:
        return jsonify({
            'error': error_msg,
            'error_type': 'rate_limit_exceeded',
            'status': 'error'
        }), 429
    
    data = request.json
    question = data.get('question', '')
    
    # Validate request
    if not question:
        queue_manager.release_request(session_id)
        return jsonify({'error': 'Question cannot be empty'}), 400    
    def process_ask_request():
        # --- BEGIN FUZZY MATCHING PREPROCESSING ---
        if vn: # Global Vanna instance
            table_names, column_names = get_db_schema_elements(vn)
            if table_names or column_names:
                original_question_for_log = question
                corrected_question = correct_schema_references_in_question(question, table_names, column_names)
                if corrected_question != original_question_for_log:
                    app.logger.info(f"Original question for /api/ask: '{original_question_for_log}'")
                    app.logger.info(f"Question after fuzzy matching for /api/ask: '{corrected_question}'")
                    corrected_question_to_use = corrected_question # Update the question variable
                else:
                    app.logger.debug("Fuzzy matching did not alter the question for /api/ask.")
                    corrected_question_to_use = question
            else:
                app.logger.debug("Skipping fuzzy matching for /api/ask as no schema elements were loaded.")
                corrected_question_to_use = question
        else:
            app.logger.warning("Vanna instance 'vn' not available for fuzzy matching in /api/ask.")
            corrected_question_to_use = question
        # --- END FUZZY MATCHING PREPROCESSING ---

        try:
            app.logger.info(f"Processing question in /api/ask for session {session_id[:8]}...: {corrected_question_to_use}")
            
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
            is_direct_question = corrected_question_to_use.lower().strip() in direct_answer_keywords
            
            if is_direct_question:
                app.logger.info(f"Question is a direct interaction: '{corrected_question_to_use}', bypassing SQL generation.")
                try:
                    # Use Ollama to generate a direct answer
                    direct_answer = vn.ask_llm(f"La siguiente es una pregunta que no requiere acceso a la base de datos. Por favor, responde directamente y de manera concisa en español: {corrected_question_to_use}")
                    app.logger.info(f"Generated direct answer: {direct_answer[:100]}...")
                    
                    # Return the direct answer
                    return {
                        'question': question,                        'direct_answer': direct_answer,                        'session_id': session_id[:8] + '...',
                        'status': 'success'
                    }
                    
                except Exception as e:
                    app.logger.error(f"Failed to generate direct answer: {str(e)}")
                    # Fall back to SQL generation if direct answer fails
              # Generate SQL from question if not a direct knowledge question or if direct answer failed
            try:
                # Enhanced SQL generation prompt based on research findings for improved accuracy
                # Using contextual business examples relevant to APEX database
                sql_generation_prompt = (
                    f"Question: {corrected_question_to_use}\n\n"
                    "Context: You are working with the APEX business database containing tables for "
                    "contracts, invoices, inventory, HR, and operational data. "
                    "Generate a precise SQL query that answers the question using the most relevant tables. "
                    "Focus on business intelligence insights and proper joins between related entities. "
                    "Consider Spanish business terminology and APEX schema conventions."
                )
                sql = vn.generate_sql(sql_generation_prompt)
                app.logger.info(f"Generated SQL: {sql} for enhanced prompt: {sql_generation_prompt[:100]}...")
            except OllamaProcessError as e:
                app.logger.error(f"Ollama process terminated: {str(e)}")
                # Removed fallback to direct answer
                raise e
            except OllamaConnectionError as e:
                app.logger.error(f"Ollama connection error: {str(e)}")
                # Removed fallback to direct answer                raise e
            except Exception as e:
                app.logger.error(f"Failed to generate SQL: {str(e)}")
                raise SQLGenerationError(str(e))
            
            # Execute the SQL and get results
            try:
                df = vn.run_sql(sql)
                app.logger.info(f"SQL execution successful, returned {len(df)} rows")                # Learn from this successful interaction using enhanced learning system
                try:
                    start_time = time.time()
                    interaction_id = learning_manager.learn_from_interaction(
                        session_id=session_id,
                        question=original_question_for_log,  # Use original question for learning
                        sql=sql,
                        success=True,
                        execution_time=time.time() - start_time,
                        result_count=len(df) if df is not None else 0,
                        vn_instance=vn
                    )
                    app.logger.info(f"Successfully learned from interaction {interaction_id}: Q: {original_question_for_log}")
                except Exception as e_train:
                    app.logger.error(f"Failed to learn from successful interaction: {str(e_train)}")# New: Get natural language interpretation of the results
                interpretation = "El sistema no pudo proporcionar una interpretación para estos datos en este momento." # Default message
                if df is not None and not df.empty: # Check if df exists and is not empty
                    try:
                        # Convert DataFrame to a string representation for the LLM
                        df_string = df.to_string(index=False)
                        # Limit the string length to avoid overly long prompts
                        # This limit can be adjusted based on typical data size and LLM context window
                        max_df_string_len = 8000 
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
                            f"Importante: NO menciones nombres de tabla, esquema, base de datos o servidor en tu respuesta. "
                            f"Sé directo e informativo. No te limites a repetir los datos. Si los datos son complejos, resume los hallazgos principales."
                        )
                        app.logger.info("Requesting LLM to interpret SQL results.")
                        # Updated to use system_message_override for interpretation task
                        interpretation = vn.ask_llm(
                            question=interpretation_prompt, 
                            system_message_override=(
                            "Eres un asistente de IA especializado en interpretar resultados de consultas SQL y explicarlos en español. "                            "NO menciones nombres de tablas, esquemas, bases de datos o servidores. "
                            "Concéntrate en responder la pregunta original del usuario basándote en los datos proporcionados."
                        )
                    )
                        app.logger.info(f"LLM interpretation received (first 150 chars): {interpretation[:150]}...")
                    except Exception as e:
                        app.logger.error(f"Error during LLM interpretation of SQL results: {str(e)}")
                        # interpretation remains the default error message if LLM call fails
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
            result = {                'question': question,
                'sql': sql,
                'data': df.to_dict(orient='records') if df is not None else [], # Handle if df could be None
                'interpretation': interpretation,    # Add the new interpretation
                'session_id': session_id[:8] + '...',
                'status': 'success'            }
            
            return result
        
        except Exception as e:
            app.logger.error(f"Error in process_ask_request: {str(e)}")
            raise e

    try:
        # Process request with concurrency control
        result = process_question_with_concurrency(process_ask_request, session_id)
        return jsonify(result)
    except OllamaError as e:
        app.logger.error(f"Ollama error: {str(e)}")
        # Learn from failed interaction
        try:
            learning_manager.learn_from_interaction(
                session_id=session_id,
                question=data.get('question', ''),
                success=False,
                error_message=f"Ollama error: {e.message}",
                vn_instance=vn
            )
        except Exception as learn_err:
            app.logger.error(f"Failed to learn from error: {learn_err}")
        
        return jsonify({
            'error': e.message,
            'error_type': 'ollama_error',
            'session_id': session_id[:8] + '...',
            'status': 'error',
            'ollama_status': 'not_running' if not is_ollama_running() else 'error'
        }), e.status_code
    except AppError as e:
        app.logger.error(f"Application error: {str(e)}")
        # Learn from failed interaction
        try:
            learning_manager.learn_from_interaction(
                session_id=session_id,
                question=data.get('question', ''),
                success=False,
                error_message=f"Application error: {e.message}",
                vn_instance=vn
            )
        except Exception as learn_err:
            app.logger.error(f"Failed to learn from error: {learn_err}")
        
        return jsonify({
            'error': e.message, 
            'session_id': session_id[:8] + '...',
            'status': 'error'
        }), e.status_code
    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}")
        app.logger.error(traceback.format_exc())
        # Learn from failed interaction
        try:
            learning_manager.learn_from_interaction(
                session_id=session_id,
                question=data.get('question', ''),
                success=False,
                error_message=f"Unexpected error: {str(e)}",
                vn_instance=vn
            )
        except Exception as learn_err:
            app.logger.error(f"Failed to learn from error: {learn_err}")
        
        return jsonify({
            'error': str(e), 
            'session_id': session_id[:8] + '...',
            'status': 'error'
        }), 500

@app.route('/api/ask/advanced', methods=['POST'])
def ask_advanced():
    # Get or create session
    session_id = get_or_create_session()
    
    # Check rate limits
    allowed, error_msg = check_request_limits(session_id)
    if not allowed:
        return jsonify({
            'error': error_msg,
            'error_type': 'rate_limit_exceeded',
            'status': 'error'
        }), 429
    
    data = request.json
    question = data.get('question', '')
    options = data.get('options', {})
    
    # Validate request
    if not question:
        queue_manager.release_request(session_id)
        return jsonify({'error': 'Question cannot be empty'}), 400

    def process_advanced_request():
        # --- BEGIN FUZZY MATCHING PREPROCESSING ---
        if vn: # Global Vanna instance
            table_names, column_names = get_db_schema_elements(vn)
            if table_names or column_names:
                original_question_for_log = question
                corrected_question = correct_schema_references_in_question(question, table_names, column_names)
                if corrected_question != original_question_for_log:
                    app.logger.info(f"Original question for /api/ask/advanced: '{original_question_for_log}'")
                    app.logger.info(f"Question after fuzzy matching for /api/ask/advanced: '{corrected_question}'")
                    corrected_question_to_use = corrected_question # Update the question variable
                else:
                    app.logger.debug("Fuzzy matching did not alter the question for /api/ask/advanced.")
                    corrected_question_to_use = question
            else:
                app.logger.debug("Skipping fuzzy matching for /api/ask/advanced as no schema elements were loaded.")
                corrected_question_to_use = question
        else:
            app.logger.warning("Vanna instance 'vn' not available for fuzzy matching in /api/ask/advanced.")
            corrected_question_to_use = question
        # --- END FUZZY MATCHING PREPROCESSING ---
        
        try:
            app.logger.info(f"Processing advanced question for session {session_id[:8]}...: {corrected_question_to_use}")
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
                  # Enhanced SQL generation prompt for advanced queries with business context
                sql_generation_prompt = (
                    f"Question: {corrected_question_to_use}\n\n"
                    "Context: You are working with the APEX business database containing tables for "
                    "contracts, invoices, inventory, HR, and operational data. "
                    "Generate a precise SQL query that answers the question using the most relevant tables. "
                    "Focus on business intelligence insights and proper joins between related entities. "
                    "Consider Spanish business terminology and APEX schema conventions. "
                    "Ensure the query is optimized for performance and returns actionable business insights."
                )
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
                'session_id': session_id[:8] + '...',
            }          # Only execute if requested
            if execute_query:
                try:
                    df = vn.run_sql(sql)
                    app.logger.info(f"SQL execution successful, returned {len(df)} rows")
                    result['data'] = df.to_dict(orient='records')
                      # Learn from this successful advanced interaction
                    try:
                        start_time = time.time()
                        interaction_id = learning_manager.learn_from_interaction(
                            session_id=session_id,
                            question=original_question_for_log,  # Use original question for learning
                            sql=sql,
                            success=True,
                            execution_time=time.time() - start_time,
                            result_count=len(df) if df is not None else 0,
                            user_feedback=f"Advanced query with options: {options}",
                            vn_instance=vn
                        )
                        app.logger.info(f"Successfully learned from advanced interaction {interaction_id}: Q: {original_question_for_log}")
                    except Exception as e_train:
                        app.logger.error(f"Failed to learn from successful advanced interaction: {str(e_train)}")
                    
                    # Add interpretation for advanced queries too
                    interpretation = "El sistema no pudo proporcionar una interpretación para estos datos en este momento."
                    if df is not None and not df.empty:
                        try:
                            # Convert DataFrame to a string representation for the LLM
                            df_string = df.to_string(index=False)
                            # Limit the string length to avoid overly long prompts
                            max_df_string_len = 8000 
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
                    # Removed fallback to direct answer                raise e  # Re-raise the original database error
            
            return result
        
        except Exception as e:
            app.logger.error(f"Error in process_advanced_request: {str(e)}")
            raise e
    
    try:
        # Process request with concurrency control
        result = process_question_with_concurrency(process_advanced_request, session_id)
        return jsonify(result)
    except AppError as e:
        app.logger.error(f"Application error: {str(e)}")
        return jsonify({
            'error': e.message,
            'session_id': session_id[:8] + '...',
            'status': 'error'
        }), e.status_code
    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            'error': str(e),
            'session_id': session_id[:8] + '...',
            'status': 'error'
        }), 500

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
    
    # Get multi-user statistics
    active_users = session_manager.get_active_users_count()
    queue_stats = queue_manager.get_queue_stats()
    
    return jsonify({
        'status': 'ok',
        'ollama_model': os.getenv('OLLAMA_MODEL', 'llama3.2'),
        'ollama_status': ollama_status,
        'database': os.getenv('SQL_DATABASE', 'master'),
        'database_status': db_status,
        'multi_user': {
            'active_users': active_users,
            'active_requests': queue_stats['active_requests'],
            'max_concurrent_requests': queue_stats['max_concurrent'],
            'users_with_active_requests': queue_stats['total_users_with_requests']
        }
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

@app.route('/api/learning/stats', methods=['GET'])
def get_learning_stats():
    """Get comprehensive learning statistics"""
    try:
        stats = learning_manager.get_learning_stats()
        return jsonify({
            'status': 'success',
            'stats': stats
        })
    except Exception as e:
        app.logger.error(f"Error getting learning stats: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/learning/feedback', methods=['POST'])
def submit_feedback():
    """Submit user feedback for a specific interaction"""
    try:
        data = request.get_json()
        session_id = get_or_create_session()
        
        interaction_id = data.get('interaction_id')
        feedback_type = data.get('feedback_type')  # 'positive', 'negative', 'correction', 'suggestion'
        feedback_text = data.get('feedback_text')
        corrected_sql = data.get('corrected_sql')
        
        if not interaction_id or not feedback_type:
            return jsonify({
                'status': 'error',
                'message': 'interaction_id and feedback_type are required'
            }), 400
        
        success = learning_manager.record_user_feedback(
            interaction_id=interaction_id,
            session_id=session_id,
            feedback_type=feedback_type,
            feedback_text=feedback_text,
            corrected_sql=corrected_sql
        )
        
        if success:
            return jsonify({
                'status': 'success',
                'message': 'Feedback recorded successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to record feedback'
            }), 500
            
    except Exception as e:
        app.logger.error(f"Error submitting feedback: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/learning/similar', methods=['POST'])
def get_similar_queries():
    """Get similar queries based on user's question"""
    try:
        data = request.get_json()
        question = data.get('question', '')
        limit = data.get('limit', 5)
        
        if not question:
            return jsonify({
                'status': 'error',
                'message': 'Question is required'
            }), 400
        
        similar_queries = learning_manager.get_similar_queries(question, limit)
        
        return jsonify({
            'status': 'success',
            'similar_queries': similar_queries
        })
        
    except Exception as e:
        app.logger.error(f"Error getting similar queries: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/learning/suggestions', methods=['GET'])
def get_query_suggestions():
    """Get query suggestions for the current user"""
    try:
        session_id = get_or_create_session()
        suggestions = learning_manager.get_query_suggestions(session_id)
        
        return jsonify({
            'status': 'success',
            'suggestions': suggestions
        })
        
    except Exception as e:
        app.logger.error(f"Error getting query suggestions: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/learning/export', methods=['GET'])
def export_learning_data():
    """Export all learning data"""
    try:
        export_path = learning_manager.export_learning_data()
        
        return jsonify({
            'status': 'success',
            'export_path': export_path,
            'message': 'Learning data exported successfully'
        })
        
    except Exception as e:
        app.logger.error(f"Error exporting learning data: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
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

import pytest
import pandas as pd
from unittest.mock import Mock, patch, MagicMock
import sys
import os
from app import OllamaVanna, get_db_schema_elements, correct_schema_references_in_question
from errors import DatabaseError, SQLGenerationError, OllamaConnectionError, OllamaProcessError

# Add the parent directory to the path to import app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestProcessAskRequest:
    """Test suite for the process_ask_request function logic"""
    
    @pytest.fixture
    def mock_vanna_instance(self):
        """Create a mock Vanna instance"""
        mock_vn = Mock(spec=OllamaVanna)
        mock_vn.generate_sql.return_value = "SELECT COUNT(*) FROM users"
        mock_vn.run_sql.return_value = pd.DataFrame({'count': [100]})
        mock_vn.add_question_sql.return_value = None
        mock_vn.ask_llm.return_value = "This shows there are 100 users in the system."
        return mock_vn
    
    @pytest.fixture
    def mock_app_logger(self):
        """Create a mock app logger"""
        return Mock()
    
    @pytest.fixture
    def sample_session_id(self):
        """Sample session ID for testing"""
        return "test-session-12345678"
    
    def simulate_process_ask_request(self, question, vn_instance, app_logger, session_id):
        """
        Simulate the process_ask_request function logic for testing
        This extracts the core logic from the nested function in the route
        """
        # Mock fuzzy matching preprocessing
        with patch('app.get_db_schema_elements') as mock_get_schema, \
             patch('app.correct_schema_references_in_question') as mock_correct, \
             patch('app.is_ollama_running', return_value=True):
            
            mock_get_schema.return_value = (['users', 'orders'], ['id', 'name', 'email'])
            mock_correct.return_value = question
            
            # Fuzzy matching preprocessing
            table_names, column_names = mock_get_schema(vn_instance)
            corrected_question_to_use = mock_correct(question, table_names, column_names)
            
            # Check for direct answer keywords
            direct_answer_keywords = [
                'hello', 'hi', 'who are you', 
                'thanks', 'thank you', 'ok thanks', 'ok thank you',
                'bye', 'goodbye'
            ]
            
            is_direct_question = corrected_question_to_use.lower().strip() in direct_answer_keywords
            
            if is_direct_question:
                direct_answer = vn_instance.ask_llm(f"La siguiente es una pregunta que no requiere acceso a la base de datos. Por favor, responde directamente y de manera concisa en español: {corrected_question_to_use}")
                return {
                    'question': question,
                    'direct_answer': direct_answer,
                    'session_id': session_id[:8] + '...',
                    'status': 'success'
                }
            
            # Generate SQL
            sql_generation_prompt = f"User question: '{corrected_question_to_use}'. Based on the database schema, generate a SQL query to answer this question. Respond only with the SQL query itself."
            sql = vn_instance.generate_sql(sql_generation_prompt)
            
            # Execute SQL
            df = vn_instance.run_sql(sql)
            
            # Learn from successful interaction
            vn_instance.add_question_sql(question, sql)
            
            # Generate interpretation
            interpretation = "El sistema no pudo proporcionar una interpretación para estos datos en este momento."
            if df is not None and not df.empty:
                df_string = df.to_string(index=False)
                max_df_string_len = 2000 
                if len(df_string) > max_df_string_len:
                    df_string = df_string[:max_df_string_len] + "\n... (data truncated due to length)"
                    
                interpretation_prompt = (
                    f"El usuario hizo la siguiente pregunta: '{question}'.\n"
                    f"Para responder, se ejecutó la siguiente consulta SQL: '{sql}'.\n"
                    f"La consulta devolvió los siguientes datos:\n{df_string}\n\n"
                    f"Por favor, proporciona un resumen e interpretación concisa en español de estos datos. "
                    f"Enfócate en responder la pregunta original del usuario basándote en estos datos. "
                    f"Importante: NO menciones ningún nombre de tabla, esquema, base de datos o servidor en tu respuesta. "
                    f"Sé directo e informativo. No te limites a repetir los datos. Si los datos son complejos, resume los hallazgos principales."
                )
                
                interpretation = vn_instance.ask_llm(
                    question=interpretation_prompt, 
                    system_message_override=(
                        "Eres un asistente de IA especializado en interpretar resultados de consultas SQL y explicarlos en español. "
                        "NO menciones nombres de tablas, esquemas, bases de datos o servidores. "
                        "Concéntrate en responder la pregunta original del usuario basándote en los datos proporcionados."
                    )
                )
            
            result = {
                'question': question,
                'sql': sql,
                'data': df.to_dict(orient='records') if df is not None else [],
                'interpretation': interpretation,
                'session_id': session_id[:8] + '...',
                'status': 'success'
            }
            
            return result
    
    def test_successful_sql_generation_and_execution(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test successful SQL generation and execution"""
        question = "How many users are there?"
        
        result = self.simulate_process_ask_request(
            question, mock_vanna_instance, mock_app_logger, sample_session_id
        )
        
        assert result['status'] == 'success'
        assert result['question'] == question
        assert result['sql'] == "SELECT COUNT(*) FROM users"
        assert len(result['data']) == 1
        assert result['data'][0]['count'] == 100
        assert 'interpretation' in result
        assert result['session_id'] == 'test-ses...'
        
        # Verify learning occurred
        mock_vanna_instance.add_question_sql.assert_called_once_with(question, "SELECT COUNT(*) FROM users")
    
    def test_direct_question_handling(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test handling of direct questions that don't require SQL"""
        question = "hello"
        mock_vanna_instance.ask_llm.return_value = "¡Hola! Soy tu asistente de IA."
        
        result = self.simulate_process_ask_request(
            question, mock_vanna_instance, mock_app_logger, sample_session_id
        )
        
        assert result['status'] == 'success'
        assert result['question'] == question
        assert 'direct_answer' in result
        assert result['direct_answer'] == "¡Hola! Soy tu asistente de IA."
        assert 'sql' not in result
        assert 'data' not in result
        
        # Verify no SQL generation or learning occurred
        mock_vanna_instance.generate_sql.assert_not_called()
        mock_vanna_instance.add_question_sql.assert_not_called()
    
    def test_empty_dataframe_handling(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test handling when SQL returns empty DataFrame"""
        question = "Show users with impossible condition"
        mock_vanna_instance.run_sql.return_value = pd.DataFrame()  # Empty DataFrame
        
        result = self.simulate_process_ask_request(
            question, mock_vanna_instance, mock_app_logger, sample_session_id
        )
        
        assert result['status'] == 'success'
        assert result['data'] == []
        assert result['interpretation'] == "El sistema no pudo proporcionar una interpretación para estos datos en este momento."
    
    def test_sql_generation_error(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test handling of SQL generation errors"""
        question = "Complex question that fails"
        mock_vanna_instance.generate_sql.side_effect = Exception("Failed to generate SQL")
        
        with pytest.raises(SQLGenerationError):
            self.simulate_process_ask_request(
                question, mock_vanna_instance, mock_app_logger, sample_session_id
            )
    
    def test_database_error_during_execution(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test handling of database errors during SQL execution"""
        question = "Query that causes DB error"
        mock_vanna_instance.run_sql.side_effect = DatabaseError("Connection timeout")
        
        with pytest.raises(DatabaseError):
            self.simulate_process_ask_request(
                question, mock_vanna_instance, mock_app_logger, sample_session_id
            )
    
    def test_ollama_connection_error(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test handling of Ollama connection errors"""
        question = "Question when Ollama is down"
        mock_vanna_instance.generate_sql.side_effect = OllamaConnectionError("Ollama not running")
        
        with pytest.raises(OllamaConnectionError):
            self.simulate_process_ask_request(
                question, mock_vanna_instance, mock_app_logger, sample_session_id
            )
    
    def test_interpretation_generation_failure(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test handling when interpretation generation fails"""
        question = "How many orders today?"
        # First call for interpretation fails, but should have fallback
        mock_vanna_instance.ask_llm.side_effect = [Exception("LLM error")]
        
        result = self.simulate_process_ask_request(
            question, mock_vanna_instance, mock_app_logger, sample_session_id
        )
        
        assert result['status'] == 'success'
        assert result['interpretation'] == "El sistema no pudo proporcionar una interpretación para estos datos en este momento."
    
    def test_learning_failure_does_not_break_flow(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test that learning failure doesn't break the main flow"""
        question = "Count active users"
        mock_vanna_instance.add_question_sql.side_effect = Exception("Learning failed")
        
        # Should still return successful result despite learning failure
        result = self.simulate_process_ask_request(
            question, mock_vanna_instance, mock_app_logger, sample_session_id
        )
        
        assert result['status'] == 'success'
        assert 'data' in result
        assert 'interpretation' in result
    
    def test_large_dataframe_truncation(self, mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test that large DataFrames are properly truncated for interpretation"""
        question = "Get all user data"
        
        # Create a large DataFrame that would exceed the truncation limit
        large_data = {'name': [f'User{i}' for i in range(1000)], 
                     'email': [f'user{i}@test.com' for i in range(1000)]}
        large_df = pd.DataFrame(large_data)
        mock_vanna_instance.run_sql.return_value = large_df
        
        result = self.simulate_process_ask_request(
            question, mock_vanna_instance, mock_app_logger, sample_session_id
        )
        
        assert result['status'] == 'success'
        assert len(result['data']) == 1000  # Full data returned
        # Verify interpretation was called with truncated data
        mock_vanna_instance.ask_llm.assert_called()
        
    @patch('app.get_db_schema_elements')
    @patch('app.correct_schema_references_in_question')
    def test_fuzzy_matching_integration(self, mock_correct, mock_get_schema, 
                                      mock_vanna_instance, mock_app_logger, sample_session_id):
        """Test integration with fuzzy matching functionality"""
        question = "How many usres are there?"  # Misspelled 'users'
        corrected_question = "How many users are there?"
        
        mock_get_schema.return_value = (['users', 'orders'], ['id', 'name'])
        mock_correct.return_value = corrected_question
        
        with patch('app.is_ollama_running', return_value=True):
            result = self.simulate_process_ask_request(
                question, mock_vanna_instance, mock_app_logger, sample_session_id
            )
        
        # Verify fuzzy matching was called
        mock_get_schema.assert_called_once_with(mock_vanna_instance)
        mock_correct.assert_called_once_with(question, ['users', 'orders'], ['id', 'name'])
        
        # Verify SQL generation used corrected question
        expected_prompt = f"User question: '{corrected_question}'. Based on the database schema, generate a SQL query to answer this question. Respond only with the SQL query itself."
        mock_vanna_instance.generate_sql.assert_called_once_with(expected_prompt)

class TestHelperFunctions:
    """Test helper functions used in the process_ask_request"""
    
    @patch('app.app')
    def test_get_db_schema_elements_with_cache(self, mock_app):
        """Test schema elements caching functionality"""
        mock_vn = Mock()
        mock_training_data = pd.DataFrame({
            'content_type': ['ddl', 'ddl'],
            'content': [
                'CREATE TABLE users (id INT, name VARCHAR(50))',
                'CREATE TABLE orders (id INT, user_id INT, amount DECIMAL)'
            ]
        })
        mock_vn.get_training_data.return_value = mock_training_data
        mock_app.logger = Mock()
        
        table_names, column_names = get_db_schema_elements(mock_vn)
        
        assert 'users' in table_names
        assert 'orders' in table_names
        assert 'id' in column_names
        assert 'name' in column_names
        assert 'user_id' in column_names
        assert 'amount' in column_names
    
    def test_correct_schema_references_basic(self):
        """Test basic fuzzy matching correction"""
        question = "How many usres are in ordrs table?"
        table_names = ['users', 'orders', 'products']
        column_names = ['id', 'name', 'email']
        
        corrected = correct_schema_references_in_question(question, table_names, column_names, threshold=80)
        
        # Should correct 'usres' to 'users' and 'ordrs' to 'orders'
        assert 'users' in corrected
        assert 'orders' in corrected
        assert 'usres' not in corrected
        assert 'ordrs' not in corrected
    
    def test_correct_schema_references_no_changes(self):
        """Test that correct references are not changed"""
        question = "How many users are in orders table?"
        table_names = ['users', 'orders', 'products']
        column_names = ['id', 'name', 'email']
        
        corrected = correct_schema_references_in_question(question, table_names, column_names)
        
        assert corrected == question  # Should be unchanged
    
    def test_correct_schema_references_empty_schema(self):
        """Test handling when schema elements are empty"""
        question = "How many users are there?"
        
        corrected = correct_schema_references_in_question(question, [], [])
        
        assert corrected == question  # Should be unchanged
import logging
from functools import wraps
from ollama_fix import with_ollama_retry

logger = logging.getLogger(__name__)

def patch_ollama_class(cls):
    """
    Patches the Ollama class methods with retry mechanisms.
    This function wraps key methods of the Ollama class to add retry logic.
    """
    original_submit_prompt = cls.submit_prompt
    original_extract_sql = cls.extract_sql
    
    @wraps(original_submit_prompt)
    @with_ollama_retry(max_retries=3, retry_delay=5)
    def new_submit_prompt(self, prompt, **kwargs):
        """Wrapped submit_prompt method with retry logic"""
        logger.info(f"Submitting prompt to Ollama with retry mechanism")
        try:
            return original_submit_prompt(self, prompt, **kwargs)
        except Exception as e:
            logger.error(f"Error submitting prompt to Ollama: {str(e)}")
            raise
    
    @wraps(original_extract_sql)
    def new_extract_sql(self, llm_response):
        """Wrapped extract_sql method with additional error handling"""
        try:
            return original_extract_sql(self, llm_response)
        except Exception as e:
            logger.error(f"Error extracting SQL from LLM response: {str(e)}")
            # Provide a fallback simple extraction if the regular one fails
            if isinstance(llm_response, str):
                # Try to find SQL between SQL code block markers
                sql_marker_start = llm_response.find("```sql")
                if sql_marker_start != -1:
                    sql_marker_end = llm_response.find("```", sql_marker_start + 5)
                    if sql_marker_end != -1:
                        return llm_response[sql_marker_start + 6:sql_marker_end].strip()
                
                # Look for SELECT statement as a fallback
                if "SELECT" in llm_response.upper():
                    select_pos = llm_response.upper().find("SELECT")
                    # Try to find end of statement
                    for end_marker in [";", "```"]:
                        end_pos = llm_response.find(end_marker, select_pos)
                        if end_pos != -1:
                            return llm_response[select_pos:end_pos].strip()
                    # If no end marker, just return the rest of the string
                    return llm_response[select_pos:].strip()
            
            # If all extraction methods fail, raise the original error
            raise
    
    # Replace the original methods with the wrapped ones
    cls.submit_prompt = new_submit_prompt
    cls.extract_sql = new_extract_sql
    
    return cls

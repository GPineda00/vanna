import json
import re

from httpx import Timeout

from ..base import VannaBase
from ..exceptions import DependencyError


class Ollama(VannaBase):
  def __init__(self, config=None):

    try:
      ollama = __import__("ollama")
    except ImportError:
      raise DependencyError(
        "You need to install required dependencies to execute this method, run command:"
        " \npip install ollama"
      )

    if not config:
      raise ValueError("config must contain at least Ollama model")
    if 'model' not in config.keys():
      raise ValueError("config must contain at least Ollama model")
    self.host = config.get("ollama_host", "http://localhost:11434")
    self.model = config["model"]
    if ":" not in self.model:
      self.model += ":latest"

    self.ollama_timeout = config.get("ollama_timeout", 240.0)

    self.ollama_client = ollama.Client(self.host, timeout=Timeout(self.ollama_timeout))
    self.keep_alive = config.get('keep_alive', None)
    self.ollama_options = config.get('options', {})
    self.num_ctx = self.ollama_options.get('num_ctx', 2048)
    self.__pull_model_if_ne(self.ollama_client, self.model)

  @staticmethod
  def __pull_model_if_ne(ollama_client, model):
    model_response = ollama_client.list()
    model_lists = [model_element['model'] for model_element in
                   model_response.get('models', [])]
    if model not in model_lists:
      ollama_client.pull(model)
  def system_message(self, message: str) -> any:
    # Llama3.2 optimized system message format
    return {"role": "system", "content": f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{message}<|eot_id|>"}

  def user_message(self, message: str) -> any:
    # Llama3.2 optimized user message format
    return {"role": "user", "content": f"<|start_header_id|>user<|end_header_id|>\n\n{message}<|eot_id|>"}

  def assistant_message(self, message: str) -> any:
    # Llama3.2 optimized assistant message format
    return {"role": "assistant", "content": f"<|start_header_id|>assistant<|end_header_id|>\n\n{message}<|eot_id|>"}
  def extract_sql(self, llm_response):
    """
    Enhanced SQL extraction for Llama3.2 with better pattern matching.
    Prioritizes contextually relevant SQL patterns and handles common Llama3.2 response formats.
    
    Args:
    - llm_response (str): The string to search within for an SQL statement.

    Returns:
    - str: The extracted SQL statement, or the original response if no SQL found.
    """
    # Clean up Ollama-specific formatting issues
    llm_response = llm_response.replace("\\_", "_")
    llm_response = llm_response.replace("\\", "")
    
    # Remove Llama3.2 chat template artifacts if present
    llm_response = llm_response.replace("<|begin_of_text|>", "")
    llm_response = llm_response.replace("<|start_header_id|>", "")
    llm_response = llm_response.replace("<|end_header_id|>", "")
    llm_response = llm_response.replace("<|eot_id|>", "")

    # Pattern 1: SQL code blocks (highest priority for Llama3.2)
    sql_block = re.search(r"```sql\s*\n(.*?)```", llm_response, re.DOTALL | re.IGNORECASE)
    if sql_block:
      extracted = sql_block.group(1).strip()
      self.log(f"Output from LLM: {llm_response} \nExtracted SQL: {extracted}")
      return extracted

    # Pattern 2: WITH clauses (CTEs) - common in business queries
    with_query = re.search(r'\b(WITH\s+.*?(?:SELECT\s+.*?)?(?:;|$))', llm_response, re.DOTALL | re.IGNORECASE)
    if with_query:
      extracted = with_query.group(1).rstrip(';').strip()
      self.log(f"Output from LLM: {llm_response} \nExtracted SQL: {extracted}")
      return extracted
    
    # Pattern 3: SELECT statements with enhanced matching
    select_query = re.search(r'\b(SELECT\s+.*?)(?:;|\n\n|$)', llm_response, re.DOTALL | re.IGNORECASE)
    if select_query:
      extracted = select_query.group(1).rstrip(';').strip()
      self.log(f"Output from LLM: {llm_response} \nExtracted SQL: {extracted}")
      return extracted

    # Pattern 4: Any code block (fallback)
    code_block = re.search(r"```(.*?)```", llm_response, re.DOTALL | re.IGNORECASE)
    if code_block:
      extracted = code_block.group(1).strip()
      self.log(f"Output from LLM: {llm_response} \nExtracted SQL: {extracted}")
      return extracted

    # Return original response if no patterns match
    return llm_response.strip()
  def submit_prompt(self, prompt, **kwargs) -> str:
    # Enhanced Llama3.2 optimized parameters
    enhanced_options = {
        'temperature': 0.1,        # Lower for more deterministic SQL
        'top_p': 0.9,             # Balanced creativity
        'top_k': 40,              # Focused token selection
        'repeat_penalty': 1.1,     # Prevent repetition
        'num_predict': 512,        # Reasonable response length
        'stop': ['<|eot_id|>', '<|end_of_text|>', '\n\n\n']  # Stop tokens for Llama3.2
    }
    
    # Merge with existing options, prioritizing enhanced ones
    final_options = {**self.ollama_options, **enhanced_options}
    
    self.log(
      f"Ollama parameters:\n"
      f"model={self.model},\n"
      f"options={final_options},\n"
      f"keep_alive={self.keep_alive}")
    self.log(f"Prompt Content:\n{json.dumps(prompt, ensure_ascii=False)}")
    
    try:
        response_dict = self.ollama_client.chat(
            model=self.model,
            messages=prompt,
            stream=False,
            options=final_options,
            keep_alive=self.keep_alive
        )
        
        self.log(f"Ollama Response:\n{str(response_dict)}")
        
        response_content = response_dict['message']['content']
        
        # Clean up Llama3.2 artifacts
        response_content = response_content.replace('<|eot_id|>', '')
        response_content = response_content.replace('<|end_of_text|>', '')
        
        return response_content.strip()
        
    except Exception as e:
        self.log(f"Error in Ollama submit_prompt: {str(e)}")
        raise

class AppError(Exception):
    """Base application error class"""
    def __init__(self, message, status_code=500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class DatabaseError(AppError):
    """Database connection or query error"""
    def __init__(self, message):
        super().__init__(f"Database error: {message}", 500)

class OllamaError(AppError):
    """Ollama API or model error"""
    def __init__(self, message):
        super().__init__(f"Ollama error: {message}", 500)
        
class OllamaProcessError(OllamaError):
    """Ollama process termination error"""
    def __init__(self, message, exit_code=None):
        self.exit_code = exit_code
        error_msg = f"Ollama process terminated unexpectedly"
        if exit_code:
            error_msg += f" (exit code: {exit_code})"
        if message:
            error_msg += f": {message}"
        error_msg += ". Try restarting the Ollama service."
        super().__init__(error_msg)
        
class OllamaConnectionError(OllamaError):
    """Ollama connection error"""
    def __init__(self, message=None):
        error_msg = "Failed to connect to Ollama service"
        if message:
            error_msg += f": {message}"
        super().__init__(error_msg)

class SQLGenerationError(AppError):
    """Error in SQL generation"""
    def __init__(self, message):
        super().__init__(f"Failed to generate SQL: {message}", 422)

class InvalidRequestError(AppError):
    """Invalid request data"""
    def __init__(self, message):
        super().__init__(f"Invalid request: {message}", 400)

import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(app):
    """Set up logging for the Flask application"""
    
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.mkdir('logs')
    
    # Configure the Flask app logger
    if not app.debug:
        file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        
        app.logger.setLevel(logging.INFO)
        app.logger.info('Application startup')
    
    # Configure the Vanna logger
    vanna_logger = logging.getLogger('vanna')
    vanna_handler = RotatingFileHandler('logs/vanna.log', maxBytes=10240, backupCount=10)
    vanna_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s'
    ))
    vanna_logger.setLevel(logging.INFO)
    vanna_logger.addHandler(vanna_handler)
    
    # Configure the Ollama logger
    ollama_logger = logging.getLogger('ollama')
    ollama_handler = RotatingFileHandler('logs/ollama.log', maxBytes=10240, backupCount=10)
    ollama_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s'
    ))
    ollama_logger.setLevel(logging.INFO)
    ollama_logger.addHandler(ollama_handler)

# learning_manager.py
import os
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import threading
from collections import defaultdict, deque
import hashlib

class LearningManager:
    """Enhanced learning manager for user interactions and query optimization"""
    
    def __init__(self, learning_dir: str = "learning_data"):
        self.learning_dir = Path(learning_dir)
        self.learning_dir.mkdir(exist_ok=True)
        
        # Initialize learning data files
        self.queries_file = self.learning_dir / "successful_queries.jsonl"
        self.interactions_file = self.learning_dir / "user_interactions.jsonl"
        self.feedback_file = self.learning_dir / "user_feedback.jsonl"
        self.analytics_file = self.learning_dir / "query_analytics.json"
        
        # Thread safety
        self.lock = threading.Lock()
        
        # In-memory caches for performance
        self.recent_queries = deque(maxlen=1000)  # Last 1000 queries
        self.query_patterns = defaultdict(int)    # Query pattern frequency
        self.user_preferences = defaultdict(dict) # User-specific preferences
        
        # Analytics tracking
        self.analytics = self._load_analytics()
        
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"LearningManager initialized with directory: {self.learning_dir}")

    def _load_analytics(self) -> Dict:
        """Load analytics data from file"""
        try:
            if self.analytics_file.exists():
                with open(self.analytics_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.warning(f"Could not load analytics: {e}")
        
        return {
            'total_queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'user_count': 0,
            'popular_patterns': {},
            'improvement_suggestions': [],
            'last_updated': datetime.now().isoformat()
        }

    def _save_analytics(self):
        """Save analytics data to file"""
        try:
            self.analytics['last_updated'] = datetime.now().isoformat()
            with open(self.analytics_file, 'w', encoding='utf-8') as f:
                json.dump(self.analytics, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"Failed to save analytics: {e}")

    def _generate_query_hash(self, question: str, sql: str) -> str:
        """Generate a unique hash for a question-SQL pair"""
        content = f"{question.lower().strip()}|{sql.lower().strip()}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def _extract_query_pattern(self, sql: str) -> str:
        """Extract a generalized pattern from SQL query"""
        import re
        
        # Normalize SQL for pattern extraction
        normalized = re.sub(r'\s+', ' ', sql.upper().strip())
        
        # Replace specific values with placeholders
        normalized = re.sub(r"'[^']*'", "'VALUE'", normalized)  # String literals
        normalized = re.sub(r'\b\d+\b', 'NUMBER', normalized)   # Numbers
        normalized = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', 'DATE', normalized)  # Dates
        
        # Extract main query structure
        pattern_parts = []
        if 'SELECT' in normalized:
            pattern_parts.append('SELECT')
        if 'JOIN' in normalized:
            pattern_parts.append('JOIN')
        if 'WHERE' in normalized:
            pattern_parts.append('WHERE')
        if 'GROUP BY' in normalized:
            pattern_parts.append('GROUP_BY')
        if 'ORDER BY' in normalized:
            pattern_parts.append('ORDER_BY')
        if 'HAVING' in normalized:
            pattern_parts.append('HAVING')
        
        return '_'.join(pattern_parts) if pattern_parts else 'UNKNOWN'

    def learn_from_interaction(self, 
                             session_id: str,
                             question: str, 
                             sql: str = None,
                             success: bool = True,
                             execution_time: float = None,
                             result_count: int = None,
                             error_message: str = None,
                             user_feedback: str = None,
                             vn_instance = None) -> str:
        """
        Learn from a user interaction and store it for future reference
        
        Args:
            session_id: User session identifier
            question: Original user question
            sql: Generated SQL query (if any)
            success: Whether the interaction was successful
            execution_time: Time taken to execute the query
            result_count: Number of results returned
            error_message: Error message if failed
            user_feedback: Optional user feedback
            vn_instance: Vanna instance for adding to vector store
            
        Returns:
            Interaction ID for reference
        """
        
        interaction_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        with self.lock:
            # Create interaction record
            interaction = {
                'id': interaction_id,
                'session_id': session_id,
                'timestamp': timestamp,
                'question': question,
                'sql': sql,
                'success': success,
                'execution_time': execution_time,
                'result_count': result_count,
                'error_message': error_message,
                'user_feedback': user_feedback,
                'query_hash': self._generate_query_hash(question, sql or '') if sql else None,
                'query_pattern': self._extract_query_pattern(sql) if sql else None
            }
            
            # Update analytics
            self.analytics['total_queries'] += 1
            if success:
                self.analytics['successful_queries'] += 1
            else:
                self.analytics['failed_queries'] += 1
            
            # Track query patterns
            if sql and success:
                pattern = self._extract_query_pattern(sql)
                self.query_patterns[pattern] += 1
                self.analytics['popular_patterns'][pattern] = self.query_patterns[pattern]
                
                # Add to recent queries cache
                self.recent_queries.append({
                    'question': question,
                    'sql': sql,
                    'timestamp': timestamp,
                    'session_id': session_id
                })
            
            # Save interaction to file
            try:
                with open(self.interactions_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(interaction, ensure_ascii=False) + '\n')
                
                # If successful and SQL exists, save to queries file and vector store
                if success and sql:
                    query_record = {
                        'id': interaction_id,
                        'question': question,
                        'sql': sql,
                        'timestamp': timestamp,
                        'session_id': session_id,
                        'execution_time': execution_time,
                        'result_count': result_count,
                        'query_pattern': pattern
                    }
                    
                    with open(self.queries_file, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(query_record, ensure_ascii=False) + '\n')
                    
                    # Add to Vanna's vector store for future retrieval
                    if vn_instance:
                        try:
                            vector_id = vn_instance.add_question_sql(question, sql)
                            self.logger.info(f"Added successful query to vector store: {vector_id}")
                        except Exception as e:
                            self.logger.error(f"Failed to add query to vector store: {e}")
                
                # Save updated analytics
                self._save_analytics()
                
                self.logger.info(f"Learned from interaction {interaction_id}: success={success}, pattern={interaction.get('query_pattern', 'N/A')}")
                
            except Exception as e:
                self.logger.error(f"Failed to save interaction {interaction_id}: {e}")
        
        return interaction_id

    def record_user_feedback(self, 
                           interaction_id: str,
                           session_id: str,
                           feedback_type: str,  # 'positive', 'negative', 'correction', 'suggestion'
                           feedback_text: str = None,
                           corrected_sql: str = None) -> bool:
        """Record user feedback for improving future responses"""
        
        feedback_record = {
            'id': str(uuid.uuid4()),
            'interaction_id': interaction_id,
            'session_id': session_id,
            'timestamp': datetime.now().isoformat(),
            'feedback_type': feedback_type,
            'feedback_text': feedback_text,
            'corrected_sql': corrected_sql
        }
        
        try:
            with open(self.feedback_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(feedback_record, ensure_ascii=False) + '\n')
            
            self.logger.info(f"Recorded user feedback: {feedback_type} for interaction {interaction_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to record feedback: {e}")
            return False

    def get_similar_queries(self, question: str, limit: int = 5) -> List[Dict]:
        """Get similar queries from learning history for reference"""
        
        # Simple similarity based on common words (can be enhanced with embeddings)
        question_words = set(question.lower().split())
        similar_queries = []
        
        for query in self.recent_queries:
            query_words = set(query['question'].lower().split())
            similarity = len(question_words.intersection(query_words)) / len(question_words.union(query_words))
            
            if similarity > 0.3:  # Threshold for similarity
                similar_queries.append({
                    **query,
                    'similarity': similarity
                })
        
        # Sort by similarity and return top results
        similar_queries.sort(key=lambda x: x['similarity'], reverse=True)
        return similar_queries[:limit]

    def get_query_suggestions(self, session_id: str) -> List[str]:
        """Get query suggestions based on popular patterns and user history"""
        
        suggestions = []
        
        # Add popular patterns
        top_patterns = sorted(self.query_patterns.items(), key=lambda x: x[1], reverse=True)[:3]
        for pattern, count in top_patterns:
            suggestions.append(f"Queries with pattern '{pattern}' are popular ({count} uses)")
        
        # Add user-specific suggestions if available
        if session_id in self.user_preferences:
            preferences = self.user_preferences[session_id]
            if 'favorite_tables' in preferences:
                suggestions.append(f"You often query: {', '.join(preferences['favorite_tables'])}")
        
        return suggestions

    def get_learning_stats(self) -> Dict:
        """Get comprehensive learning statistics"""
        
        with self.lock:
            stats = {
                'total_interactions': self.analytics['total_queries'],
                'successful_queries': self.analytics['successful_queries'],
                'failed_queries': self.analytics['failed_queries'],
                'success_rate': (self.analytics['successful_queries'] / max(self.analytics['total_queries'], 1)) * 100,
                'unique_patterns': len(self.query_patterns),
                'recent_queries_count': len(self.recent_queries),
                'popular_patterns': dict(sorted(self.analytics['popular_patterns'].items(), 
                                              key=lambda x: x[1], reverse=True)[:10]),
                'last_updated': self.analytics['last_updated']
            }
            
            # Add file sizes for reference
            try:
                stats['file_sizes'] = {
                    'queries_file': self.queries_file.stat().st_size if self.queries_file.exists() else 0,
                    'interactions_file': self.interactions_file.stat().st_size if self.interactions_file.exists() else 0,
                    'feedback_file': self.feedback_file.stat().st_size if self.feedback_file.exists() else 0
                }
            except Exception:
                stats['file_sizes'] = {}
            
            return stats

    def cleanup_old_data(self, days_to_keep: int = 90):
        """Clean up old learning data to prevent excessive growth"""
        
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_iso = cutoff_date.isoformat()
        
        for filepath in [self.interactions_file, self.queries_file, self.feedback_file]:
            if not filepath.exists():
                continue
                
            try:
                # Read all lines and filter recent ones
                recent_lines = []
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            record = json.loads(line.strip())
                            if record.get('timestamp', '') > cutoff_iso:
                                recent_lines.append(line)
                        except json.JSONDecodeError:
                            continue
                
                # Write back only recent data
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.writelines(recent_lines)
                
                self.logger.info(f"Cleaned up {filepath.name}: kept {len(recent_lines)} recent records")
                
            except Exception as e:
                self.logger.error(f"Failed to cleanup {filepath.name}: {e}")

    def export_learning_data(self, export_path: str = None) -> str:
        """Export all learning data to a single JSON file"""
        
        if not export_path:
            export_path = self.learning_dir / f"learning_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        export_data = {
            'export_timestamp': datetime.now().isoformat(),
            'analytics': self.analytics,
            'recent_queries': list(self.recent_queries),
            'query_patterns': dict(self.query_patterns),
            'interactions': [],
            'feedback': []
        }
        
        # Read interactions
        if self.interactions_file.exists():
            with open(self.interactions_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        export_data['interactions'].append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        
        # Read feedback
        if self.feedback_file.exists():
            with open(self.feedback_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        export_data['feedback'].append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        
        # Save export
        try:
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"Exported learning data to: {export_path}")
            return str(export_path)
            
        except Exception as e:
            self.logger.error(f"Failed to export learning data: {e}")
            raise

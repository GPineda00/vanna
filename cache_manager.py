#!/usr/bin/env python
# cache_manager.py - Redis-based caching system for Vanna SQL optimization

import redis
import json
import hashlib
import logging
import pickle
import gzip
from typing import Any, Optional, Dict, List
from datetime import datetime, timedelta
import pandas as pd
from contextlib import contextmanager
import threading
import time

logger = logging.getLogger(__name__)

class CacheManager:
    """Redis-based cache manager for SQL queries, results, and interpretations"""
    
    def __init__(self, 
                 redis_host: str = 'localhost',
                 redis_port: int = 6379,
                 redis_db: int = 0,
                 redis_password: Optional[str] = None,
                 default_ttl: int = 3600,  # 1 hour default TTL
                 max_retries: int = 3,
                 retry_delay: float = 0.1):
        """
        Initialize cache manager
        
        Args:
            redis_host: Redis server host
            redis_port: Redis server port
            redis_db: Redis database number
            redis_password: Redis password if required
            default_ttl: Default time-to-live for cache entries (seconds)
            max_retries: Maximum retry attempts for Redis operations
            retry_delay: Delay between retry attempts (seconds)
        """
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_password = redis_password
        self.default_ttl = default_ttl
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Redis connection pool
        self.pool = redis.ConnectionPool(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=False,  # We'll handle encoding ourselves
            max_connections=20,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True
        )
        
        self.redis_client = redis.Redis(connection_pool=self.pool)
        
        # Cache prefixes for different data types
        self.prefixes = {
            'sql_query': 'vanna:sql:',
            'query_result': 'vanna:result:',
            'interpretation': 'vanna:interpret:',
            'training_data': 'vanna:training:',
            'session': 'vanna:session:',
            'rate_limit': 'vanna:rate:',
            'analytics': 'vanna:analytics:'
        }
        
        # Statistics tracking
        self.stats = {
            'hits': 0,
            'misses': 0,
            'errors': 0,
            'total_requests': 0
        }
        self.stats_lock = threading.Lock()
        
        # Test connection
        self._test_connection()
    
    def _test_connection(self):
        """Test Redis connection"""
        try:
            self.redis_client.ping()
            logger.info(f"Connected to Redis at {self.redis_host}:{self.redis_port}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def _generate_key(self, prefix: str, data: str) -> str:
        """Generate a cache key with hash for consistent length"""
        hash_value = hashlib.sha256(data.encode('utf-8')).hexdigest()[:16]
        return f"{prefix}{hash_value}"
    
    def _serialize_data(self, data: Any) -> bytes:
        """Serialize data for Redis storage with compression"""
        try:
            # Handle pandas DataFrames specially
            if isinstance(data, pd.DataFrame):
                # Convert DataFrame to dict format that preserves types
                serialized = {
                    'type': 'dataframe',
                    'data': data.to_dict('records'),
                    'columns': list(data.columns),
                    'dtypes': {col: str(dtype) for col, dtype in data.dtypes.items()}
                }
            else:
                serialized = {'type': 'generic', 'data': data}
            
            # Pickle and compress
            pickled = pickle.dumps(serialized)
            compressed = gzip.compress(pickled)
            return compressed
        except Exception as e:
            logger.error(f"Error serializing data: {e}")
            raise
    
    def _deserialize_data(self, data: bytes) -> Any:
        """Deserialize data from Redis storage"""
        try:
            # Decompress and unpickle
            decompressed = gzip.decompress(data)
            deserialized = pickle.loads(decompressed)
            
            # Handle pandas DataFrames
            if deserialized.get('type') == 'dataframe':
                df = pd.DataFrame(deserialized['data'])
                # Restore column order
                if deserialized.get('columns'):
                    df = df[deserialized['columns']]
                return df
            else:
                return deserialized['data']
        except Exception as e:
            logger.error(f"Error deserializing data: {e}")
            raise
    
    def _execute_with_retry(self, operation, *args, **kwargs):
        """Execute Redis operation with retry logic"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return operation(*args, **kwargs)
            except redis.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
                    logger.warning(f"Redis operation failed, retrying (attempt {attempt + 1})")
                else:
                    logger.error(f"Redis operation failed after {self.max_retries} attempts")
            except Exception as e:
                logger.error(f"Unexpected error in Redis operation: {e}")
                raise
        
        raise last_exception
    
    def _update_stats(self, hit: bool = False, error: bool = False):
        """Update cache statistics"""
        with self.stats_lock:
            self.stats['total_requests'] += 1
            if hit:
                self.stats['hits'] += 1
            else:
                self.stats['misses'] += 1
            if error:
                self.stats['errors'] += 1
    
    # SQL Query Caching
    def cache_sql_generation(self, question: str, sql: str, ttl: Optional[int] = None) -> bool:
        """Cache generated SQL for a question"""
        try:
            key = self._generate_key(self.prefixes['sql_query'], question.lower().strip())
            data = {
                'sql': sql,
                'question': question,
                'timestamp': datetime.utcnow().isoformat(),
                'generated_by': 'vanna'
            }
            
            serialized = self._serialize_data(data)
            ttl = ttl or self.default_ttl
            
            result = self._execute_with_retry(self.redis_client.setex, key, ttl, serialized)
            logger.debug(f"Cached SQL generation for question: {question[:100]}...")
            return bool(result)
        except Exception as e:
            logger.error(f"Error caching SQL generation: {e}")
            self._update_stats(error=True)
            return False
    
    def get_cached_sql(self, question: str) -> Optional[Dict]:
        """Get cached SQL for a question"""
        try:
            key = self._generate_key(self.prefixes['sql_query'], question.lower().strip())
            cached_data = self._execute_with_retry(self.redis_client.get, key)
            
            if cached_data:
                result = self._deserialize_data(cached_data)
                self._update_stats(hit=True)
                logger.debug(f"Cache hit for SQL question: {question[:100]}...")
                return result
            else:
                self._update_stats(hit=False)
                return None
        except Exception as e:
            logger.error(f"Error retrieving cached SQL: {e}")
            self._update_stats(error=True)
            return None
    
    # Query Result Caching
    def cache_query_result(self, sql: str, result: pd.DataFrame, ttl: Optional[int] = None) -> bool:
        """Cache query execution result"""
        try:
            # Normalize SQL for consistent caching
            normalized_sql = ' '.join(sql.strip().split())
            key = self._generate_key(self.prefixes['query_result'], normalized_sql)
            
            data = {
                'result': result,
                'sql': sql,
                'timestamp': datetime.utcnow().isoformat(),
                'row_count': len(result),
                'columns': list(result.columns)
            }
            
            serialized = self._serialize_data(data)
            ttl = ttl or self.default_ttl
            
            result_bool = self._execute_with_retry(self.redis_client.setex, key, ttl, serialized)
            logger.debug(f"Cached query result for SQL: {sql[:100]}...")
            return bool(result_bool)
        except Exception as e:
            logger.error(f"Error caching query result: {e}")
            self._update_stats(error=True)
            return False
    
    def get_cached_result(self, sql: str) -> Optional[Dict]:
        """Get cached query result"""
        try:
            normalized_sql = ' '.join(sql.strip().split())
            key = self._generate_key(self.prefixes['query_result'], normalized_sql)
            cached_data = self._execute_with_retry(self.redis_client.get, key)
            
            if cached_data:
                result = self._deserialize_data(cached_data)
                self._update_stats(hit=True)
                logger.debug(f"Cache hit for query result: {sql[:100]}...")
                return result
            else:
                self._update_stats(hit=False)
                return None
        except Exception as e:
            logger.error(f"Error retrieving cached result: {e}")
            self._update_stats(error=True)
            return None
    
    # Interpretation Caching
    def cache_interpretation(self, result_data: str, interpretation: str, ttl: Optional[int] = None) -> bool:
        """Cache result interpretation"""
        try:
            key = self._generate_key(self.prefixes['interpretation'], result_data)
            data = {
                'interpretation': interpretation,
                'timestamp': datetime.utcnow().isoformat(),
                'result_data_hash': hashlib.sha256(result_data.encode()).hexdigest()
            }
            
            serialized = self._serialize_data(data)
            ttl = ttl or self.default_ttl
            
            result = self._execute_with_retry(self.redis_client.setex, key, ttl, serialized)
            logger.debug("Cached result interpretation")
            return bool(result)
        except Exception as e:
            logger.error(f"Error caching interpretation: {e}")
            self._update_stats(error=True)
            return False
    
    def get_cached_interpretation(self, result_data: str) -> Optional[str]:
        """Get cached interpretation"""
        try:
            key = self._generate_key(self.prefixes['interpretation'], result_data)
            cached_data = self._execute_with_retry(self.redis_client.get, key)
            
            if cached_data:
                result = self._deserialize_data(cached_data)
                self._update_stats(hit=True)
                logger.debug("Cache hit for interpretation")
                return result.get('interpretation')
            else:
                self._update_stats(hit=False)
                return None
        except Exception as e:
            logger.error(f"Error retrieving cached interpretation: {e}")
            self._update_stats(error=True)
            return None
    
    # Session Management
    def store_session_data(self, session_id: str, data: Dict, ttl: Optional[int] = None) -> bool:
        """Store session data"""
        try:
            key = f"{self.prefixes['session']}{session_id}"
            serialized = self._serialize_data(data)
            ttl = ttl or (24 * 3600)  # 24 hours default for sessions
            
            result = self._execute_with_retry(self.redis_client.setex, key, ttl, serialized)
            return bool(result)
        except Exception as e:
            logger.error(f"Error storing session data: {e}")
            return False
    
    def get_session_data(self, session_id: str) -> Optional[Dict]:
        """Get session data"""
        try:
            key = f"{self.prefixes['session']}{session_id}"
            cached_data = self._execute_with_retry(self.redis_client.get, key)
            
            if cached_data:
                return self._deserialize_data(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error retrieving session data: {e}")
            return None
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session data"""
        try:
            key = f"{self.prefixes['session']}{session_id}"
            result = self._execute_with_retry(self.redis_client.delete, key)
            return bool(result)
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return False
    
    # Rate Limiting
    def check_rate_limit(self, identifier: str, limit: int, window: int) -> Dict:
        """Check rate limit for an identifier"""
        try:
            key = f"{self.prefixes['rate_limit']}{identifier}"
            current_time = int(time.time())
            window_start = current_time - window
            
            # Use Redis pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)  # Remove old entries
            pipe.zcard(key)  # Count current entries
            pipe.zadd(key, {str(current_time): current_time})  # Add current request
            pipe.expire(key, window)  # Set expiration
            
            results = self._execute_with_retry(pipe.execute)
            current_count = results[1]
            
            return {
                'allowed': current_count < limit,
                'current_count': current_count + 1,  # +1 for the current request
                'limit': limit,
                'window': window,
                'reset_time': current_time + window
            }
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            # Allow request on error
            return {
                'allowed': True,
                'current_count': 0,
                'limit': limit,
                'window': window,
                'reset_time': int(time.time()) + window
            }
    
    # Analytics and Monitoring
    def record_analytics(self, event_type: str, data: Dict, ttl: Optional[int] = None) -> bool:
        """Record analytics event"""
        try:
            timestamp = datetime.utcnow().isoformat()
            key = f"{self.prefixes['analytics']}{event_type}:{timestamp}"
            
            analytics_data = {
                'event_type': event_type,
                'timestamp': timestamp,
                'data': data
            }
            
            serialized = self._serialize_data(analytics_data)
            ttl = ttl or (7 * 24 * 3600)  # 7 days default for analytics
            
            result = self._execute_with_retry(self.redis_client.setex, key, ttl, serialized)
            return bool(result)
        except Exception as e:
            logger.error(f"Error recording analytics: {e}")
            return False
    
    # Cache Management
    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate cache entries matching pattern"""
        try:
            keys = self._execute_with_retry(self.redis_client.keys, pattern)
            if keys:
                deleted = self._execute_with_retry(self.redis_client.delete, *keys)
                logger.info(f"Invalidated {deleted} cache entries matching pattern: {pattern}")
                return deleted
            return 0
        except Exception as e:
            logger.error(f"Error invalidating cache pattern: {e}")
            return 0
    
    def clear_cache(self, cache_type: Optional[str] = None) -> bool:
        """Clear cache entries"""
        try:
            if cache_type and cache_type in self.prefixes:
                pattern = f"{self.prefixes[cache_type]}*"
                deleted = self.invalidate_pattern(pattern)
                logger.info(f"Cleared {deleted} {cache_type} cache entries")
            else:
                # Clear all vanna-related cache
                pattern = "vanna:*"
                deleted = self.invalidate_pattern(pattern)
                logger.info(f"Cleared {deleted} total cache entries")
            return True
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return False
    
    def get_cache_stats(self) -> Dict:
        """Get cache statistics"""
        with self.stats_lock:
            stats = self.stats.copy()
        
        # Calculate hit rate
        total = stats['hits'] + stats['misses']
        hit_rate = (stats['hits'] / total * 100) if total > 0 else 0
        
        # Get Redis info
        try:
            redis_info = self._execute_with_retry(self.redis_client.info, 'memory')
            redis_stats = {
                'memory_used': redis_info.get('used_memory_human', 'Unknown'),
                'connected_clients': redis_info.get('connected_clients', 0),
                'total_commands_processed': redis_info.get('total_commands_processed', 0)
            }
        except Exception:
            redis_stats = {}
        
        return {
            'cache_stats': stats,
            'hit_rate_percent': round(hit_rate, 2),
            'redis_stats': redis_stats
        }
    
    def health_check(self) -> Dict:
        """Perform health check"""
        try:
            start_time = time.time()
            self.redis_client.ping()
            response_time = (time.time() - start_time) * 1000  # ms
            
            return {
                'status': 'healthy',
                'response_time_ms': round(response_time, 2),
                'connection': 'ok'
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'connection': 'failed'
            }
    
    @contextmanager
    def pipeline(self):
        """Context manager for Redis pipeline operations"""
        pipe = self.redis_client.pipeline()
        try:
            yield pipe
            pipe.execute()
        except Exception as e:
            logger.error(f"Pipeline operation failed: {e}")
            raise
        finally:
            pipe.reset()


# Global cache manager instance
_cache_manager: Optional[CacheManager] = None
_cache_lock = threading.Lock()

def get_cache_manager(**kwargs) -> CacheManager:
    """Get global cache manager instance"""
    global _cache_manager
    
    if _cache_manager is None:
        with _cache_lock:
            if _cache_manager is None:
                _cache_manager = CacheManager(**kwargs)
    
    return _cache_manager

def initialize_cache(redis_host: str = 'localhost',
                    redis_port: int = 6379,
                    redis_db: int = 0,
                    redis_password: Optional[str] = None,
                    **kwargs) -> CacheManager:
    """Initialize global cache manager"""
    global _cache_manager
    
    with _cache_lock:
        _cache_manager = CacheManager(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
            **kwargs
        )
    
    return _cache_manager

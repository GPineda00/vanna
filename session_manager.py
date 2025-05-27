#!/usr/bin/env python
# session_manager.py - Redis-based session management for Vanna SQL optimization

import redis
import json
import uuid
import logging
import threading
import time
from typing import Dict, Optional, Any, List, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import hashlib
import secrets
from contextlib import contextmanager

logger = logging.getLogger(__name__)

@dataclass
class SessionData:
    """Session data structure"""
    session_id: str
    user_id: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime = None
    last_accessed: datetime = None
    data: Dict[str, Any] = None
    is_active: bool = True
    expires_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.last_accessed is None:
            self.last_accessed = datetime.utcnow()
        if self.data is None:
            self.data = {}
    
    def to_dict(self) -> Dict:
        """Convert session to dictionary"""
        data = asdict(self)
        # Convert datetime objects to ISO strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat() if value else None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'SessionData':
        """Create session from dictionary"""
        # Convert ISO strings back to datetime objects
        for key in ['created_at', 'last_accessed', 'expires_at']:
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        
        return cls(**data)
    
    def is_expired(self) -> bool:
        """Check if session is expired"""
        if self.expires_at:
            return datetime.utcnow() > self.expires_at
        return False
    
    def touch(self):
        """Update last accessed time"""
        self.last_accessed = datetime.utcnow()

class SessionManager:
    """Redis-based session manager for horizontal scaling"""
    
    def __init__(self,
                 redis_host: str = 'localhost',
                 redis_port: int = 6379,
                 redis_db: int = 2,  # Different DB from cache and async
                 redis_password: Optional[str] = None,
                 session_timeout: int = 3600,  # 1 hour default
                 cleanup_interval: int = 300,  # 5 minutes
                 max_sessions_per_user: int = 5,
                 secure_cookies: bool = True):
        """
        Initialize session manager
        
        Args:
            redis_host: Redis server host
            redis_port: Redis server port
            redis_db: Redis database number
            redis_password: Redis password if required
            session_timeout: Default session timeout in seconds
            cleanup_interval: Interval for cleanup operations in seconds
            max_sessions_per_user: Maximum sessions per user
            secure_cookies: Use secure session tokens
        """
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_password = redis_password
        self.session_timeout = session_timeout
        self.cleanup_interval = cleanup_interval
        self.max_sessions_per_user = max_sessions_per_user
        self.secure_cookies = secure_cookies
        
        # Redis connection
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        
        # Redis keys
        self.session_prefix = "vanna:session:"
        self.user_sessions_prefix = "vanna:user_sessions:"
        self.active_sessions_key = "vanna:active_sessions"
        self.session_stats_key = "vanna:session_stats"
        
        # Cleanup thread
        self.cleanup_thread: Optional[threading.Thread] = None
        self.running = False
        
        # Statistics
        self.stats = {
            'total_sessions': 0,
            'active_sessions': 0,
            'expired_sessions': 0,
            'average_session_duration': 0.0
        }
        self.stats_lock = threading.Lock()
        
        # Test connection
        self._test_connection()
    
    def _test_connection(self):
        """Test Redis connection"""
        try:
            self.redis_client.ping()
            logger.info(f"Connected to Redis session store at {self.redis_host}:{self.redis_port}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis session store: {e}")
            raise
    
    def _generate_session_id(self) -> str:
        """Generate a secure session ID"""
        if self.secure_cookies:
            # Use cryptographically secure random token
            return secrets.token_urlsafe(32)
        else:
            # Use UUID for development
            return str(uuid.uuid4())
    
    def _get_session_key(self, session_id: str) -> str:
        """Get Redis key for session"""
        return f"{self.session_prefix}{session_id}"
    
    def _get_user_sessions_key(self, user_id: str) -> str:
        """Get Redis key for user sessions"""
        return f"{self.user_sessions_prefix}{user_id}"
    
    def create_session(self,
                      user_id: Optional[str] = None,
                      user_agent: Optional[str] = None,
                      ip_address: Optional[str] = None,
                      timeout: Optional[int] = None,
                      initial_data: Optional[Dict] = None) -> str:
        """
        Create a new session
        
        Args:
            user_id: User identifier
            user_agent: User agent string
            ip_address: Client IP address
            timeout: Session timeout in seconds
            initial_data: Initial session data
            
        Returns:
            Session ID
        """
        session_id = self._generate_session_id()
        timeout = timeout or self.session_timeout
        expires_at = datetime.utcnow() + timedelta(seconds=timeout)
        
        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
            data=initial_data or {}
        )
        
        try:
            # Store session data
            session_key = self._get_session_key(session_id)
            session_data = json.dumps(session.to_dict())
            
            self.redis_client.setex(session_key, timeout, session_data)
            
            # Add to active sessions
            self.redis_client.sadd(self.active_sessions_key, session_id)
            
            # Track user sessions if user_id provided
            if user_id:
                user_sessions_key = self._get_user_sessions_key(user_id)
                self.redis_client.sadd(user_sessions_key, session_id)
                self.redis_client.expire(user_sessions_key, timeout)
                
                # Enforce max sessions per user
                self._enforce_user_session_limit(user_id)
            
            # Update statistics
            with self.stats_lock:
                self.stats['total_sessions'] += 1
                self.stats['active_sessions'] = self.redis_client.scard(self.active_sessions_key)
            
            logger.info(f"Created session {session_id} for user {user_id or 'anonymous'}")
            return session_id
            
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            raise
    
    def get_session(self, session_id: str, touch: bool = True) -> Optional[SessionData]:
        """
        Get session data
        
        Args:
            session_id: Session identifier
            touch: Update last accessed time
            
        Returns:
            Session data or None if not found/expired
        """
        try:
            session_key = self._get_session_key(session_id)
            session_data = self.redis_client.get(session_key)
            
            if not session_data:
                return None
            
            session = SessionData.from_dict(json.loads(session_data))
            
            # Check if expired
            if session.is_expired():
                self.delete_session(session_id)
                return None
            
            # Touch session if requested
            if touch:
                session.touch()
                updated_data = json.dumps(session.to_dict())
                
                # Get current TTL and update
                ttl = self.redis_client.ttl(session_key)
                if ttl > 0:
                    self.redis_client.setex(session_key, ttl, updated_data)
            
            return session
            
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            return None
    
    def update_session(self, session_id: str, data: Dict[str, Any], merge: bool = True) -> bool:
        """
        Update session data
        
        Args:
            session_id: Session identifier
            data: Data to update
            merge: Merge with existing data or replace
            
        Returns:
            True if successful
        """
        try:
            session = self.get_session(session_id, touch=False)
            if not session:
                return False
            
            # Update data
            if merge:
                session.data.update(data)
            else:
                session.data = data
            
            session.touch()
            
            # Store updated session
            session_key = self._get_session_key(session_id)
            updated_data = json.dumps(session.to_dict())
            
            ttl = self.redis_client.ttl(session_key)
            if ttl > 0:
                self.redis_client.setex(session_key, ttl, updated_data)
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"Error updating session {session_id}: {e}")
            return False
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        try:
            session_key = self._get_session_key(session_id)
            
            # Get session to find user_id
            session_data = self.redis_client.get(session_key)
            if session_data:
                session = SessionData.from_dict(json.loads(session_data))
                
                # Remove from user sessions if applicable
                if session.user_id:
                    user_sessions_key = self._get_user_sessions_key(session.user_id)
                    self.redis_client.srem(user_sessions_key, session_id)
            
            # Delete session data
            deleted = self.redis_client.delete(session_key)
            
            # Remove from active sessions
            self.redis_client.srem(self.active_sessions_key, session_id)
            
            # Update statistics
            with self.stats_lock:
                self.stats['active_sessions'] = self.redis_client.scard(self.active_sessions_key)
            
            if deleted:
                logger.info(f"Deleted session {session_id}")
            
            return bool(deleted)
            
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")
            return False
    
    def extend_session(self, session_id: str, additional_time: int) -> bool:
        """Extend session timeout"""
        try:
            session = self.get_session(session_id, touch=False)
            if not session:
                return False
            
            # Update expiration time
            session.expires_at = datetime.utcnow() + timedelta(seconds=additional_time)
            session.touch()
            
            # Store updated session
            session_key = self._get_session_key(session_id)
            updated_data = json.dumps(session.to_dict())
            
            self.redis_client.setex(session_key, additional_time, updated_data)
            
            logger.info(f"Extended session {session_id} by {additional_time} seconds")
            return True
            
        except Exception as e:
            logger.error(f"Error extending session {session_id}: {e}")
            return False
    
    def get_user_sessions(self, user_id: str) -> List[SessionData]:
        """Get all active sessions for a user"""
        try:
            user_sessions_key = self._get_user_sessions_key(user_id)
            session_ids = self.redis_client.smembers(user_sessions_key)
            
            sessions = []
            for session_id in session_ids:
                session = self.get_session(session_id, touch=False)
                if session and session.user_id == user_id:
                    sessions.append(session)
                else:
                    # Clean up invalid session reference
                    self.redis_client.srem(user_sessions_key, session_id)
            
            return sessions
            
        except Exception as e:
            logger.error(f"Error getting user sessions for {user_id}: {e}")
            return []
    
    def delete_user_sessions(self, user_id: str, exclude_session: Optional[str] = None) -> int:
        """Delete all sessions for a user"""
        try:
            sessions = self.get_user_sessions(user_id)
            deleted_count = 0
            
            for session in sessions:
                if exclude_session and session.session_id == exclude_session:
                    continue
                    
                if self.delete_session(session.session_id):
                    deleted_count += 1
            
            logger.info(f"Deleted {deleted_count} sessions for user {user_id}")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error deleting user sessions for {user_id}: {e}")
            return 0
    
    def _enforce_user_session_limit(self, user_id: str):
        """Enforce maximum sessions per user"""
        try:
            sessions = self.get_user_sessions(user_id)
            
            if len(sessions) > self.max_sessions_per_user:
                # Sort by last accessed time, delete oldest
                sessions.sort(key=lambda s: s.last_accessed)
                
                sessions_to_delete = len(sessions) - self.max_sessions_per_user
                for i in range(sessions_to_delete):
                    self.delete_session(sessions[i].session_id)
                    logger.info(f"Deleted oldest session for user {user_id} due to limit")
                    
        except Exception as e:
            logger.error(f"Error enforcing session limit for user {user_id}: {e}")
    
    def start_cleanup(self):
        """Start background cleanup thread"""
        if self.running:
            logger.warning("Cleanup thread is already running")
            return
        
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop)
        self.cleanup_thread.daemon = True
        self.cleanup_thread.start()
        
        logger.info("Started session cleanup thread")
    
    def stop_cleanup(self):
        """Stop background cleanup thread"""
        self.running = False
        
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        logger.info("Stopped session cleanup thread")
    
    def _cleanup_loop(self):
        """Background cleanup loop"""
        logger.info("Session cleanup thread started")
        
        while self.running:
            try:
                self._cleanup_expired_sessions()
                
                # Sleep for cleanup interval
                for _ in range(self.cleanup_interval):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Session cleanup error: {e}")
                time.sleep(60)  # Wait before retrying
        
        logger.info("Session cleanup thread stopped")
    
    def _cleanup_expired_sessions(self):
        """Clean up expired sessions"""
        try:
            # Get all active session IDs
            active_session_ids = self.redis_client.smembers(self.active_sessions_key)
            expired_sessions = []
            
            for session_id in active_session_ids:
                session_key = self._get_session_key(session_id)
                
                # Check if session still exists in Redis
                if not self.redis_client.exists(session_key):
                    expired_sessions.append(session_id)
                    continue
                
                # Check if session data is expired
                session_data = self.redis_client.get(session_key)
                if session_data:
                    try:
                        session = SessionData.from_dict(json.loads(session_data))
                        if session.is_expired():
                            expired_sessions.append(session_id)
                    except Exception as e:
                        logger.error(f"Error parsing session {session_id}: {e}")
                        expired_sessions.append(session_id)
            
            # Remove expired sessions
            for session_id in expired_sessions:
                self.redis_client.srem(self.active_sessions_key, session_id)
                # Also clean up session data if it still exists
                session_key = self._get_session_key(session_id)
                self.redis_client.delete(session_key)
            
            # Update statistics
            if expired_sessions:
                with self.stats_lock:
                    self.stats['expired_sessions'] += len(expired_sessions)
                    self.stats['active_sessions'] = self.redis_client.scard(self.active_sessions_key)
                
                logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")
                
        except Exception as e:
            logger.error(f"Error in cleanup_expired_sessions: {e}")
    
    @contextmanager
    def session_context(self, session_id: str):
        """Context manager for session operations"""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found or expired")
        
        try:
            yield session
        finally:
            # Update session after context
            self.update_session(session_id, session.data)
    
    def get_active_sessions_count(self) -> int:
        """Get count of active sessions"""
        try:
            return self.redis_client.scard(self.active_sessions_key)
        except Exception as e:
            logger.error(f"Error getting active sessions count: {e}")
            return 0
    
    def get_stats(self) -> Dict:
        """Get session manager statistics"""
        with self.stats_lock:
            stats = self.stats.copy()
        
        # Add current active sessions count
        stats['active_sessions'] = self.get_active_sessions_count()
        
        # Calculate average session duration
        try:
            # This would require tracking session durations
            # For now, return the stored average
            pass
        except Exception:
            pass
        
        return stats
    
    def health_check(self) -> Dict:
        """Perform health check"""
        try:
            start_time = time.time()
            self.redis_client.ping()
            response_time = (time.time() - start_time) * 1000  # ms
            
            active_count = self.get_active_sessions_count()
            
            return {
                'status': 'healthy',
                'response_time_ms': round(response_time, 2),
                'active_sessions': active_count,
                'cleanup_running': self.running
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'active_sessions': 0,
                'cleanup_running': self.running
            }
    
    def export_session(self, session_id: str) -> Optional[Dict]:
        """Export session data for backup/migration"""
        session = self.get_session(session_id, touch=False)
        if session:
            return session.to_dict()
        return None
    
    def import_session(self, session_data: Dict, timeout: Optional[int] = None) -> bool:
        """Import session data from backup/migration"""
        try:
            session = SessionData.from_dict(session_data)
            session_key = self._get_session_key(session.session_id)
            
            timeout = timeout or self.session_timeout
            data = json.dumps(session.to_dict())
            
            self.redis_client.setex(session_key, timeout, data)
            self.redis_client.sadd(self.active_sessions_key, session.session_id)
            
            if session.user_id:
                user_sessions_key = self._get_user_sessions_key(session.user_id)
                self.redis_client.sadd(user_sessions_key, session.session_id)
                self.redis_client.expire(user_sessions_key, timeout)
            
            return True
            
        except Exception as e:
            logger.error(f"Error importing session: {e}")
            return False


# Global session manager instance
_session_manager: Optional[SessionManager] = None
_session_lock = threading.Lock()

def get_session_manager(**kwargs) -> SessionManager:
    """Get global session manager instance"""
    global _session_manager
    
    if _session_manager is None:
        with _session_lock:
            if _session_manager is None:
                _session_manager = SessionManager(**kwargs)
    
    return _session_manager

def initialize_session_manager(redis_host: str = 'localhost',
                              redis_port: int = 6379,
                              redis_db: int = 2,
                              redis_password: Optional[str] = None,
                              **kwargs) -> SessionManager:
    """Initialize global session manager"""
    global _session_manager
    
    with _session_lock:
        _session_manager = SessionManager(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
            **kwargs
        )
    
    return _session_manager

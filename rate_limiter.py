#!/usr/bin/env python
# rate_limiter.py - Redis-based rate limiting system for Vanna SQL optimization

import redis
import time
import logging
import threading
from typing import Dict, Optional, List, Tuple, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import json
import hashlib

logger = logging.getLogger(__name__)

class RateLimitType(Enum):
    """Rate limit types"""
    REQUESTS_PER_MINUTE = "requests_per_minute"
    REQUESTS_PER_HOUR = "requests_per_hour"
    REQUESTS_PER_DAY = "requests_per_day"
    QUERIES_PER_MINUTE = "queries_per_minute"
    QUERIES_PER_HOUR = "queries_per_hour"
    DATA_TRANSFER_PER_MINUTE = "data_transfer_per_minute"
    CONCURRENT_CONNECTIONS = "concurrent_connections"

@dataclass
class RateLimitRule:
    """Rate limit rule definition"""
    name: str
    limit_type: RateLimitType
    limit: int
    window: int  # Window size in seconds
    burst: int = 0  # Burst allowance
    description: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'limit_type': self.limit_type.value,
            'limit': self.limit,
            'window': self.window,
            'burst': self.burst,
            'description': self.description
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'RateLimitRule':
        data['limit_type'] = RateLimitType(data['limit_type'])
        return cls(**data)

@dataclass
class RateLimitResult:
    """Rate limit check result"""
    allowed: bool
    limit: int
    remaining: int
    reset_time: float
    retry_after: Optional[int] = None
    rule_name: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'allowed': self.allowed,
            'limit': self.limit,
            'remaining': self.remaining,
            'reset_time': self.reset_time,
            'retry_after': self.retry_after,
            'rule_name': self.rule_name
        }

class RateLimiter:
    """Redis-based rate limiter with multiple algorithms"""
    
    def __init__(self,
                 redis_host: str = 'localhost',
                 redis_port: int = 6379,
                 redis_db: int = 3,  # Different DB from cache, async, and sessions
                 redis_password: Optional[str] = None,
                 default_window: int = 3600,  # 1 hour default
                 cleanup_interval: int = 300):  # 5 minutes
        """
        Initialize rate limiter
        
        Args:
            redis_host: Redis server host
            redis_port: Redis server port
            redis_db: Redis database number
            redis_password: Redis password if required
            default_window: Default window size in seconds
            cleanup_interval: Cleanup interval in seconds
        """
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_password = redis_password
        self.default_window = default_window
        self.cleanup_interval = cleanup_interval
        
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
        self.rate_limit_prefix = "vanna:rate_limit:"
        self.rules_key = "vanna:rate_limit_rules"
        self.stats_key = "vanna:rate_limit_stats"
        self.blocked_ips_key = "vanna:blocked_ips"
        
        # Rate limit rules
        self.rules: Dict[str, RateLimitRule] = {}
        self.rules_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            'total_requests': 0,
            'blocked_requests': 0,
            'rules_triggered': {},
            'top_blocked_ips': {}
        }
        self.stats_lock = threading.Lock()
        
        # Cleanup thread
        self.cleanup_thread: Optional[threading.Thread] = None
        self.running = False
        
        # Test connection
        self._test_connection()
        
        # Load existing rules
        self._load_rules()
        
        # Setup default rules
        self._setup_default_rules()
    
    def _test_connection(self):
        """Test Redis connection"""
        try:
            self.redis_client.ping()
            logger.info(f"Connected to Redis rate limiter at {self.redis_host}:{self.redis_port}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis rate limiter: {e}")
            raise
    
    def _setup_default_rules(self):
        """Setup default rate limiting rules"""
        default_rules = [
            RateLimitRule(
                name="global_requests_per_minute",
                limit_type=RateLimitType.REQUESTS_PER_MINUTE,
                limit=100,
                window=60,
                burst=20,
                description="Global requests per minute limit"
            ),
            RateLimitRule(
                name="sql_queries_per_minute",
                limit_type=RateLimitType.QUERIES_PER_MINUTE,
                limit=30,
                window=60,
                burst=10,
                description="SQL queries per minute limit"
            ),
            RateLimitRule(
                name="requests_per_hour",
                limit_type=RateLimitType.REQUESTS_PER_HOUR,
                limit=1000,
                window=3600,
                burst=100,
                description="Requests per hour limit"
            ),
            RateLimitRule(
                name="requests_per_day",
                limit_type=RateLimitType.REQUESTS_PER_DAY,
                limit=10000,
                window=86400,
                burst=500,
                description="Requests per day limit"
            )
        ]
        
        for rule in default_rules:
            if rule.name not in self.rules:
                self.add_rule(rule)
    
    def _load_rules(self):
        """Load rules from Redis"""
        try:
            rules_data = self.redis_client.hgetall(self.rules_key)
            
            with self.rules_lock:
                for rule_name, rule_json in rules_data.items():
                    try:
                        rule_dict = json.loads(rule_json)
                        rule = RateLimitRule.from_dict(rule_dict)
                        self.rules[rule_name] = rule
                        logger.debug(f"Loaded rate limit rule: {rule_name}")
                    except Exception as e:
                        logger.error(f"Error loading rule {rule_name}: {e}")
                        
        except Exception as e:
            logger.error(f"Error loading rate limit rules: {e}")
    
    def _save_rule(self, rule: RateLimitRule):
        """Save rule to Redis"""
        try:
            rule_json = json.dumps(rule.to_dict())
            self.redis_client.hset(self.rules_key, rule.name, rule_json)
        except Exception as e:
            logger.error(f"Error saving rule {rule.name}: {e}")
    
    def add_rule(self, rule: RateLimitRule):
        """Add a rate limiting rule"""
        with self.rules_lock:
            self.rules[rule.name] = rule
            self._save_rule(rule)
        
        logger.info(f"Added rate limit rule: {rule.name}")
    
    def remove_rule(self, rule_name: str) -> bool:
        """Remove a rate limiting rule"""
        with self.rules_lock:
            if rule_name in self.rules:
                del self.rules[rule_name]
                self.redis_client.hdel(self.rules_key, rule_name)
                logger.info(f"Removed rate limit rule: {rule_name}")
                return True
        return False
    
    def get_rules(self) -> Dict[str, RateLimitRule]:
        """Get all rate limiting rules"""
        with self.rules_lock:
            return self.rules.copy()
    
    def _get_key(self, identifier: str, rule_name: str) -> str:
        """Generate Redis key for rate limiting"""
        # Hash the identifier to ensure consistent key length
        id_hash = hashlib.sha256(identifier.encode()).hexdigest()[:16]
        return f"{self.rate_limit_prefix}{rule_name}:{id_hash}"
    
    def check_rate_limit(self, 
                        identifier: str, 
                        rule_names: Optional[List[str]] = None,
                        increment: int = 1,
                        data_size: Optional[int] = None) -> RateLimitResult:
        """
        Check rate limit for an identifier
        
        Args:
            identifier: Unique identifier (IP, user ID, etc.)
            rule_names: Specific rules to check (if None, checks all applicable)
            increment: Number to increment the counter by
            data_size: Size of data for transfer limits
            
        Returns:
            Rate limit result
        """
        current_time = time.time()
        
        # Update stats
        with self.stats_lock:
            self.stats['total_requests'] += 1
        
        # Check if IP is blocked
        if self._is_ip_blocked(identifier):
            with self.stats_lock:
                self.stats['blocked_requests'] += 1
            
            return RateLimitResult(
                allowed=False,
                limit=0,
                remaining=0,
                reset_time=current_time + 3600,  # 1 hour block
                retry_after=3600,
                rule_name="ip_blocked"
            )
        
        # Get rules to check
        rules_to_check = []
        with self.rules_lock:
            if rule_names:
                rules_to_check = [self.rules[name] for name in rule_names if name in self.rules]
            else:
                rules_to_check = list(self.rules.values())
        
        # Check each rule
        for rule in rules_to_check:
            result = self._check_single_rule(identifier, rule, current_time, increment, data_size)
            
            if not result.allowed:
                # Update blocked stats
                with self.stats_lock:
                    self.stats['blocked_requests'] += 1
                    if rule.name not in self.stats['rules_triggered']:
                        self.stats['rules_triggered'][rule.name] = 0
                    self.stats['rules_triggered'][rule.name] += 1
                    
                    # Track blocked IPs
                    if identifier not in self.stats['top_blocked_ips']:
                        self.stats['top_blocked_ips'][identifier] = 0
                    self.stats['top_blocked_ips'][identifier] += 1
                
                logger.warning(f"Rate limit exceeded for {identifier} on rule {rule.name}")
                return result
        
        # All rules passed
        return RateLimitResult(
            allowed=True,
            limit=0,
            remaining=0,
            reset_time=current_time,
            rule_name="all_passed"
        )
    
    def _check_single_rule(self, 
                          identifier: str, 
                          rule: RateLimitRule, 
                          current_time: float,
                          increment: int = 1,
                          data_size: Optional[int] = None) -> RateLimitResult:
        """Check a single rate limiting rule"""
        try:
            # Handle concurrent connections differently
            if rule.limit_type == RateLimitType.CONCURRENT_CONNECTIONS:
                return self._check_concurrent_connections(identifier, rule, current_time)
            
            # Handle data transfer limits
            if rule.limit_type == RateLimitType.DATA_TRANSFER_PER_MINUTE and data_size:
                increment = data_size
            
            key = self._get_key(identifier, rule.name)
            window_start = current_time - rule.window
            
            # Use sliding window log algorithm
            pipe = self.redis_client.pipeline()
            
            # Remove old entries
            pipe.zremrangebyscore(key, 0, window_start)
            
            # Count current entries
            pipe.zcard(key)
            
            # Add current request
            pipe.zadd(key, {str(current_time): current_time})
            
            # Set expiration
            pipe.expire(key, rule.window)
            
            results = pipe.execute()
            current_count = results[1]
            
            # Check if limit exceeded (considering burst)
            effective_limit = rule.limit + rule.burst
            allowed = current_count < effective_limit
            remaining = max(0, effective_limit - current_count - increment)
            reset_time = current_time + rule.window
            
            # Calculate retry after if blocked
            retry_after = None
            if not allowed:
                # Find the oldest entry that would need to expire
                oldest_entries = self.redis_client.zrange(key, 0, current_count - rule.limit)
                if oldest_entries:
                    oldest_time = float(oldest_entries[0])
                    retry_after = int(oldest_time + rule.window - current_time)
                    retry_after = max(1, retry_after)
            
            return RateLimitResult(
                allowed=allowed,
                limit=effective_limit,
                remaining=remaining,
                reset_time=reset_time,
                retry_after=retry_after,
                rule_name=rule.name
            )
            
        except Exception as e:
            logger.error(f"Error checking rate limit rule {rule.name}: {e}")
            # Allow on error to prevent blocking legitimate requests
            return RateLimitResult(
                allowed=True,
                limit=rule.limit,
                remaining=rule.limit,
                reset_time=current_time + rule.window,
                rule_name=rule.name
            )
    
    def _check_concurrent_connections(self, 
                                    identifier: str, 
                                    rule: RateLimitRule, 
                                    current_time: float) -> RateLimitResult:
        """Check concurrent connections limit"""
        try:
            key = self._get_key(identifier, rule.name)
            
            # Get current connection count
            current_count = self.redis_client.get(f"{key}:count")
            current_count = int(current_count) if current_count else 0
            
            allowed = current_count < rule.limit
            remaining = max(0, rule.limit - current_count)
            
            return RateLimitResult(
                allowed=allowed,
                limit=rule.limit,
                remaining=remaining,
                reset_time=current_time,
                rule_name=rule.name
            )
            
        except Exception as e:
            logger.error(f"Error checking concurrent connections: {e}")
            return RateLimitResult(
                allowed=True,
                limit=rule.limit,
                remaining=rule.limit,
                reset_time=current_time,
                rule_name=rule.name
            )
    
    def increment_concurrent_connections(self, identifier: str, rule_name: str = "concurrent_connections"):
        """Increment concurrent connections counter"""
        try:
            rule = self.rules.get(rule_name)
            if not rule or rule.limit_type != RateLimitType.CONCURRENT_CONNECTIONS:
                return
            
            key = self._get_key(identifier, rule_name)
            count_key = f"{key}:count"
            
            pipe = self.redis_client.pipeline()
            pipe.incr(count_key)
            pipe.expire(count_key, 300)  # 5 minute expiration for safety
            pipe.execute()
            
        except Exception as e:
            logger.error(f"Error incrementing concurrent connections: {e}")
    
    def decrement_concurrent_connections(self, identifier: str, rule_name: str = "concurrent_connections"):
        """Decrement concurrent connections counter"""
        try:
            rule = self.rules.get(rule_name)
            if not rule or rule.limit_type != RateLimitType.CONCURRENT_CONNECTIONS:
                return
            
            key = self._get_key(identifier, rule_name)
            count_key = f"{key}:count"
            
            # Decrement but don't go below 0
            current = self.redis_client.get(count_key)
            if current and int(current) > 0:
                self.redis_client.decr(count_key)
            
        except Exception as e:
            logger.error(f"Error decrementing concurrent connections: {e}")
    
    def block_ip(self, ip_address: str, duration: int = 3600, reason: str = "Rate limit exceeded"):
        """Block an IP address"""
        try:
            block_data = {
                'blocked_at': datetime.utcnow().isoformat(),
                'duration': duration,
                'reason': reason
            }
            
            self.redis_client.hset(self.blocked_ips_key, ip_address, json.dumps(block_data))
            self.redis_client.expire(f"{self.blocked_ips_key}:{ip_address}", duration)
            
            logger.warning(f"Blocked IP {ip_address} for {duration} seconds: {reason}")
            
        except Exception as e:
            logger.error(f"Error blocking IP {ip_address}: {e}")
    
    def unblock_ip(self, ip_address: str):
        """Unblock an IP address"""
        try:
            removed = self.redis_client.hdel(self.blocked_ips_key, ip_address)
            if removed:
                logger.info(f"Unblocked IP {ip_address}")
            return bool(removed)
        except Exception as e:
            logger.error(f"Error unblocking IP {ip_address}: {e}")
            return False
    
    def _is_ip_blocked(self, ip_address: str) -> bool:
        """Check if an IP address is blocked"""
        try:
            blocked_data = self.redis_client.hget(self.blocked_ips_key, ip_address)
            if blocked_data:
                block_info = json.loads(blocked_data)
                blocked_at = datetime.fromisoformat(block_info['blocked_at'])
                duration = block_info['duration']
                
                # Check if block has expired
                if datetime.utcnow() > blocked_at + timedelta(seconds=duration):
                    self.redis_client.hdel(self.blocked_ips_key, ip_address)
                    return False
                
                return True
            return False
        except Exception as e:
            logger.error(f"Error checking IP block status: {e}")
            return False
    
    def get_blocked_ips(self) -> Dict[str, Dict]:
        """Get all blocked IPs"""
        try:
            blocked_data = self.redis_client.hgetall(self.blocked_ips_key)
            result = {}
            
            for ip, data_json in blocked_data.items():
                try:
                    data = json.loads(data_json)
                    result[ip] = data
                except Exception as e:
                    logger.error(f"Error parsing blocked IP data for {ip}: {e}")
            
            return result
        except Exception as e:
            logger.error(f"Error getting blocked IPs: {e}")
            return {}
    
    def reset_limits(self, identifier: str, rule_names: Optional[List[str]] = None):
        """Reset rate limits for an identifier"""
        try:
            rules_to_reset = rule_names or list(self.rules.keys())
            
            for rule_name in rules_to_reset:
                if rule_name in self.rules:
                    key = self._get_key(identifier, rule_name)
                    self.redis_client.delete(key)
                    
                    # Also reset concurrent connections count
                    rule = self.rules[rule_name]
                    if rule.limit_type == RateLimitType.CONCURRENT_CONNECTIONS:
                        count_key = f"{key}:count"
                        self.redis_client.delete(count_key)
            
            logger.info(f"Reset rate limits for {identifier}")
            
        except Exception as e:
            logger.error(f"Error resetting limits for {identifier}: {e}")
    
    def start_cleanup(self):
        """Start background cleanup thread"""
        if self.running:
            logger.warning("Cleanup thread is already running")
            return
        
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop)
        self.cleanup_thread.daemon = True
        self.cleanup_thread.start()
        
        logger.info("Started rate limiter cleanup thread")
    
    def stop_cleanup(self):
        """Stop background cleanup thread"""
        self.running = False
        
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        logger.info("Stopped rate limiter cleanup thread")
    
    def _cleanup_loop(self):
        """Background cleanup loop"""
        logger.info("Rate limiter cleanup thread started")
        
        while self.running:
            try:
                self._cleanup_expired_limits()
                self._cleanup_expired_blocks()
                
                # Sleep for cleanup interval
                for _ in range(self.cleanup_interval):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Rate limiter cleanup error: {e}")
                time.sleep(60)  # Wait before retrying
        
        logger.info("Rate limiter cleanup thread stopped")
    
    def _cleanup_expired_limits(self):
        """Clean up expired rate limit entries"""
        try:
            current_time = time.time()
            
            # Get all rate limit keys
            pattern = f"{self.rate_limit_prefix}*"
            keys = self.redis_client.keys(pattern)
            
            cleaned_count = 0
            for key in keys:
                if ':count' in key:  # Skip concurrent connection counters
                    continue
                
                # Remove old entries from sorted sets
                rule_name = key.split(':')[-2] if ':' in key else ''
                rule = self.rules.get(rule_name)
                
                if rule:
                    window_start = current_time - rule.window
                    removed = self.redis_client.zremrangebyscore(key, 0, window_start)
                    if removed:
                        cleaned_count += removed
            
            if cleaned_count > 0:
                logger.debug(f"Cleaned up {cleaned_count} expired rate limit entries")
                
        except Exception as e:
            logger.error(f"Error in cleanup_expired_limits: {e}")
    
    def _cleanup_expired_blocks(self):
        """Clean up expired IP blocks"""
        try:
            blocked_ips = self.get_blocked_ips()
            expired_ips = []
            
            for ip, block_info in blocked_ips.items():
                try:
                    blocked_at = datetime.fromisoformat(block_info['blocked_at'])
                    duration = block_info['duration']
                    
                    if datetime.utcnow() > blocked_at + timedelta(seconds=duration):
                        expired_ips.append(ip)
                        
                except Exception as e:
                    logger.error(f"Error checking IP {ip} expiration: {e}")
                    expired_ips.append(ip)  # Remove invalid entries
            
            # Remove expired blocks
            if expired_ips:
                for ip in expired_ips:
                    self.redis_client.hdel(self.blocked_ips_key, ip)
                logger.info(f"Cleaned up {len(expired_ips)} expired IP blocks")
                
        except Exception as e:
            logger.error(f"Error in cleanup_expired_blocks: {e}")
    
    def get_stats(self) -> Dict:
        """Get rate limiter statistics"""
        with self.stats_lock:
            stats = self.stats.copy()
        
        # Add current info
        stats['active_rules'] = len(self.rules)
        stats['blocked_ips_count'] = len(self.get_blocked_ips())
        
        # Calculate block rate
        total = stats['total_requests']
        blocked = stats['blocked_requests']
        stats['block_rate_percent'] = (blocked / total * 100) if total > 0 else 0
        
        return stats
    
    def health_check(self) -> Dict:
        """Perform health check"""
        try:
            start_time = time.time()
            self.redis_client.ping()
            response_time = (time.time() - start_time) * 1000  # ms
            
            return {
                'status': 'healthy',
                'response_time_ms': round(response_time, 2),
                'active_rules': len(self.rules),
                'cleanup_running': self.running
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'active_rules': len(self.rules),
                'cleanup_running': self.running
            }


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()

def get_rate_limiter(**kwargs) -> RateLimiter:
    """Get global rate limiter instance"""
    global _rate_limiter
    
    if _rate_limiter is None:
        with _limiter_lock:
            if _rate_limiter is None:
                _rate_limiter = RateLimiter(**kwargs)
    
    return _rate_limiter

def initialize_rate_limiter(redis_host: str = 'localhost',
                           redis_port: int = 6379,
                           redis_db: int = 3,
                           redis_password: Optional[str] = None,
                           **kwargs) -> RateLimiter:
    """Initialize global rate limiter"""
    global _rate_limiter
    
    with _limiter_lock:
        _rate_limiter = RateLimiter(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
            **kwargs
        )
    
    return _rate_limiter

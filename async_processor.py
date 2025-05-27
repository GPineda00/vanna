#!/usr/bin/env python
# async_processor.py - Async query processing system for Vanna SQL optimization

import asyncio
import redis
import json
import uuid
import logging
import threading
import time
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import traceback
import signal
import sys

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    """Task status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class TaskPriority(Enum):
    """Task priority enumeration"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4

@dataclass
class Task:
    """Task data structure"""
    id: str
    type: str
    payload: Dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: datetime = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    timeout: int = 300  # 5 minutes default
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
    
    def to_dict(self) -> Dict:
        """Convert task to dictionary"""
        data = asdict(self)
        # Convert datetime objects to ISO strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat() if value else None
            elif isinstance(value, (TaskStatus, TaskPriority)):
                data[key] = value.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Task':
        """Create task from dictionary"""
        # Convert ISO strings back to datetime objects
        for key in ['created_at', 'started_at', 'completed_at']:
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        
        # Convert enum values back to enums
        if 'status' in data:
            data['status'] = TaskStatus(data['status'])
        if 'priority' in data:
            data['priority'] = TaskPriority(data['priority'])
        
        return cls(**data)

class AsyncProcessor:
    """Async query processor with Redis queue backend"""
    
    def __init__(self,
                 redis_host: str = 'localhost',
                 redis_port: int = 6379,
                 redis_db: int = 1,  # Different DB from cache
                 redis_password: Optional[str] = None,
                 max_workers: int = 10,
                 queue_name: str = 'vanna_tasks',
                 result_ttl: int = 3600,  # 1 hour
                 cleanup_interval: int = 300):  # 5 minutes
        """
        Initialize async processor
        
        Args:
            redis_host: Redis server host
            redis_port: Redis server port
            redis_db: Redis database number
            redis_password: Redis password if required
            max_workers: Maximum number of worker threads
            queue_name: Name of the task queue
            result_ttl: TTL for task results in seconds
            cleanup_interval: Interval for cleanup operations in seconds
        """
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_password = redis_password
        self.max_workers = max_workers
        self.queue_name = queue_name
        self.result_ttl = result_ttl
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
        
        # Queue keys
        self.queue_key = f"{queue_name}:queue"
        self.processing_key = f"{queue_name}:processing"
        self.results_key = f"{queue_name}:results"
        self.tasks_key = f"{queue_name}:tasks"
        
        # Task handlers
        self.task_handlers: Dict[str, Callable] = {}
        
        # Worker management
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.running = False
        self.workers: List[threading.Thread] = []
        self.cleanup_thread: Optional[threading.Thread] = None
        
        # Statistics
        self.stats = {
            'tasks_processed': 0,
            'tasks_failed': 0,
            'tasks_cancelled': 0,
            'average_processing_time': 0.0,
            'active_workers': 0
        }
        self.stats_lock = threading.Lock()
        
        # Test connection
        self._test_connection()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _test_connection(self):
        """Test Redis connection"""
        try:
            self.redis_client.ping()
            logger.info(f"Connected to Redis queue at {self.redis_host}:{self.redis_port}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis queue: {e}")
            raise
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()
        sys.exit(0)
    
    def register_handler(self, task_type: str, handler: Callable):
        """Register a task handler"""
        self.task_handlers[task_type] = handler
        logger.info(f"Registered handler for task type: {task_type}")
    
    def submit_task(self, 
                   task_type: str,
                   payload: Dict[str, Any],
                   priority: TaskPriority = TaskPriority.NORMAL,
                   timeout: int = 300,
                   max_retries: int = 3,
                   session_id: Optional[str] = None,
                   user_id: Optional[str] = None) -> str:
        """Submit a task to the queue"""
        task_id = str(uuid.uuid4())
        
        task = Task(
            id=task_id,
            type=task_type,
            payload=payload,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            session_id=session_id,
            user_id=user_id
        )
        
        try:
            # Store task data
            task_data = json.dumps(task.to_dict())
            self.redis_client.hset(self.tasks_key, task_id, task_data)
            
            # Add to priority queue
            priority_score = priority.value * 1000000 + int(time.time())
            self.redis_client.zadd(self.queue_key, {task_id: priority_score})
            
            logger.info(f"Submitted task {task_id} of type {task_type}")
            return task_id
            
        except Exception as e:
            logger.error(f"Error submitting task: {e}")
            raise
    
    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """Get task status and result"""
        try:
            # Check if task exists
            task_data = self.redis_client.hget(self.tasks_key, task_id)
            if not task_data:
                return None
            
            task_dict = json.loads(task_data)
            
            # Get result if completed
            if task_dict['status'] in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value]:
                result_data = self.redis_client.hget(self.results_key, task_id)
                if result_data:
                    result = json.loads(result_data)
                    task_dict['result'] = result.get('result')
                    task_dict['error'] = result.get('error')
            
            return task_dict
            
        except Exception as e:
            logger.error(f"Error getting task status: {e}")
            return None
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending task"""
        try:
            # Remove from queue if pending
            removed = self.redis_client.zrem(self.queue_key, task_id)
            
            # Update task status
            task_data = self.redis_client.hget(self.tasks_key, task_id)
            if task_data:
                task_dict = json.loads(task_data)
                if task_dict['status'] == TaskStatus.PENDING.value:
                    task_dict['status'] = TaskStatus.CANCELLED.value
                    task_dict['completed_at'] = datetime.utcnow().isoformat()
                    
                    self.redis_client.hset(self.tasks_key, task_id, json.dumps(task_dict))
                    logger.info(f"Cancelled task {task_id}")
                    return True
            
            return bool(removed)
            
        except Exception as e:
            logger.error(f"Error cancelling task: {e}")
            return False
    
    def start(self, num_workers: Optional[int] = None):
        """Start the async processor"""
        if self.running:
            logger.warning("Processor is already running")
            return
        
        self.running = True
        num_workers = num_workers or self.max_workers
        
        # Start worker threads
        for i in range(num_workers):
            worker = threading.Thread(target=self._worker_loop, args=(i,))
            worker.daemon = True
            worker.start()
            self.workers.append(worker)
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop)
        self.cleanup_thread.daemon = True
        self.cleanup_thread.start()
        
        logger.info(f"Started async processor with {num_workers} workers")
    
    def stop(self):
        """Stop the async processor"""
        self.running = False
        
        # Wait for workers to finish
        for worker in self.workers:
            worker.join(timeout=5)
        
        # Wait for cleanup thread
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        # Shutdown executor
        self.executor.shutdown(wait=True)
        
        logger.info("Stopped async processor")
    
    def _worker_loop(self, worker_id: int):
        """Main worker loop"""
        logger.info(f"Worker {worker_id} started")
        
        while self.running:
            try:
                # Get next task from queue (blocking with timeout)
                task_id = self._get_next_task(timeout=1)
                
                if task_id:
                    self._process_task(task_id, worker_id)
                
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                time.sleep(1)
        
        logger.info(f"Worker {worker_id} stopped")
    
    def _get_next_task(self, timeout: int = 1) -> Optional[str]:
        """Get next task from queue"""
        try:
            # Use BZPOPMIN for blocking pop with timeout
            result = self.redis_client.bzpopmin(self.queue_key, timeout=timeout)
            if result:
                _, task_id, _ = result
                return task_id
            return None
        except redis.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error getting next task: {e}")
            return None
    
    def _process_task(self, task_id: str, worker_id: int):
        """Process a single task"""
        start_time = time.time()
        
        try:
            # Update active workers count
            with self.stats_lock:
                self.stats['active_workers'] += 1
            
            # Get task data
            task_data = self.redis_client.hget(self.tasks_key, task_id)
            if not task_data:
                logger.error(f"Task {task_id} not found")
                return
            
            task = Task.from_dict(json.loads(task_data))
            
            # Check if task is expired
            if self._is_task_expired(task):
                self._mark_task_expired(task)
                return
            
            # Mark task as running
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.utcnow()
            self.redis_client.hset(self.tasks_key, task_id, json.dumps(task.to_dict()))
            
            # Add to processing set
            self.redis_client.sadd(self.processing_key, task_id)
            
            logger.info(f"Worker {worker_id} processing task {task_id} of type {task.type}")
            
            # Get task handler
            handler = self.task_handlers.get(task.type)
            if not handler:
                raise ValueError(f"No handler registered for task type: {task.type}")
            
            # Execute task with timeout
            try:
                future = self.executor.submit(handler, task.payload)
                result = future.result(timeout=task.timeout)
                
                # Mark task as completed
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.utcnow()
                task.result = result
                
                # Store result
                result_data = {
                    'result': result,
                    'completed_at': task.completed_at.isoformat()
                }
                self.redis_client.hset(self.results_key, task_id, json.dumps(result_data))
                self.redis_client.expire(f"{self.results_key}:{task_id}", self.result_ttl)
                
                logger.info(f"Task {task_id} completed successfully")
                
                with self.stats_lock:
                    self.stats['tasks_processed'] += 1
                
            except Exception as e:
                # Handle task failure
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.utcnow()
                task.error = str(e)
                task.retry_count += 1
                
                logger.error(f"Task {task_id} failed: {e}")
                
                # Retry if within limits
                if task.retry_count <= task.max_retries:
                    # Requeue with delay
                    delay = min(2 ** task.retry_count, 300)  # Exponential backoff, max 5 minutes
                    retry_time = time.time() + delay
                    self.redis_client.zadd(self.queue_key, {task_id: retry_time})
                    
                    task.status = TaskStatus.PENDING
                    logger.info(f"Retrying task {task_id} in {delay} seconds (attempt {task.retry_count})")
                else:
                    # Store error result
                    result_data = {
                        'error': task.error,
                        'completed_at': task.completed_at.isoformat(),
                        'traceback': traceback.format_exc()
                    }
                    self.redis_client.hset(self.results_key, task_id, json.dumps(result_data))
                    self.redis_client.expire(f"{self.results_key}:{task_id}", self.result_ttl)
                    
                    with self.stats_lock:
                        self.stats['tasks_failed'] += 1
            
            # Update task data
            self.redis_client.hset(self.tasks_key, task_id, json.dumps(task.to_dict()))
            
            # Remove from processing set
            self.redis_client.srem(self.processing_key, task_id)
            
            # Update processing time stats
            processing_time = time.time() - start_time
            with self.stats_lock:
                current_avg = self.stats['average_processing_time']
                total_processed = self.stats['tasks_processed'] + self.stats['tasks_failed']
                if total_processed > 0:
                    self.stats['average_processing_time'] = (
                        (current_avg * (total_processed - 1) + processing_time) / total_processed
                    )
            
        except Exception as e:
            logger.error(f"Unexpected error processing task {task_id}: {e}")
            # Remove from processing set
            self.redis_client.srem(self.processing_key, task_id)
        finally:
            # Update active workers count
            with self.stats_lock:
                self.stats['active_workers'] = max(0, self.stats['active_workers'] - 1)
    
    def _is_task_expired(self, task: Task) -> bool:
        """Check if task is expired"""
        if task.timeout <= 0:
            return False
        
        age = (datetime.utcnow() - task.created_at).total_seconds()
        return age > task.timeout
    
    def _mark_task_expired(self, task: Task):
        """Mark task as expired"""
        task.status = TaskStatus.EXPIRED
        task.completed_at = datetime.utcnow()
        task.error = "Task expired"
        
        self.redis_client.hset(self.tasks_key, task.id, json.dumps(task.to_dict()))
        logger.warning(f"Task {task.id} expired")
    
    def _cleanup_loop(self):
        """Cleanup loop for expired tasks and results"""
        logger.info("Cleanup thread started")
        
        while self.running:
            try:
                self._cleanup_expired_tasks()
                self._cleanup_old_results()
                
                # Sleep for cleanup interval
                for _ in range(self.cleanup_interval):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                time.sleep(60)  # Wait before retrying
        
        logger.info("Cleanup thread stopped")
    
    def _cleanup_expired_tasks(self):
        """Clean up expired tasks"""
        try:
            # Get all tasks
            task_data = self.redis_client.hgetall(self.tasks_key)
            expired_tasks = []
            
            for task_id, data in task_data.items():
                try:
                    task_dict = json.loads(data)
                    created_at = datetime.fromisoformat(task_dict['created_at'])
                    
                    # Check if task is expired
                    timeout = task_dict.get('timeout', 300)
                    age = (datetime.utcnow() - created_at).total_seconds()
                    
                    if age > timeout and task_dict['status'] == TaskStatus.PENDING.value:
                        expired_tasks.append(task_id)
                        
                except Exception as e:
                    logger.error(f"Error checking task {task_id} expiration: {e}")
            
            # Mark expired tasks
            for task_id in expired_tasks:
                self.redis_client.zrem(self.queue_key, task_id)  # Remove from queue
                logger.info(f"Cleaned up expired task: {task_id}")
            
            if expired_tasks:
                logger.info(f"Cleaned up {len(expired_tasks)} expired tasks")
                
        except Exception as e:
            logger.error(f"Error in cleanup_expired_tasks: {e}")
    
    def _cleanup_old_results(self):
        """Clean up old task results"""
        try:
            # Get all result keys
            result_keys = self.redis_client.hkeys(self.results_key)
            old_results = []
            
            for task_id in result_keys:
                try:
                    result_data = self.redis_client.hget(self.results_key, task_id)
                    if result_data:
                        result_dict = json.loads(result_data)
                        completed_at = datetime.fromisoformat(result_dict['completed_at'])
                        
                        # Check if result is old
                        age = (datetime.utcnow() - completed_at).total_seconds()
                        if age > self.result_ttl:
                            old_results.append(task_id)
                            
                except Exception as e:
                    logger.error(f"Error checking result {task_id} age: {e}")
            
            # Remove old results
            if old_results:
                self.redis_client.hdel(self.results_key, *old_results)
                logger.info(f"Cleaned up {len(old_results)} old results")
                
        except Exception as e:
            logger.error(f"Error in cleanup_old_results: {e}")
    
    def get_stats(self) -> Dict:
        """Get processor statistics"""
        with self.stats_lock:
            stats = self.stats.copy()
        
        # Add queue information
        try:
            queue_size = self.redis_client.zcard(self.queue_key)
            processing_size = self.redis_client.scard(self.processing_key)
            total_tasks = self.redis_client.hlen(self.tasks_key)
            
            stats.update({
                'queue_size': queue_size,
                'processing_size': processing_size,
                'total_tasks': total_tasks,
                'workers_running': len(self.workers),
                'is_running': self.running
            })
        except Exception as e:
            logger.error(f"Error getting queue stats: {e}")
        
        return stats
    
    def get_queue_info(self) -> Dict:
        """Get detailed queue information"""
        try:
            # Get queue contents
            queue_items = self.redis_client.zrange(self.queue_key, 0, -1, withscores=True)
            processing_items = self.redis_client.smembers(self.processing_key)
            
            # Get task details
            queue_tasks = []
            for task_id, score in queue_items:
                task_data = self.redis_client.hget(self.tasks_key, task_id)
                if task_data:
                    task_dict = json.loads(task_data)
                    task_dict['score'] = score
                    queue_tasks.append(task_dict)
            
            processing_tasks = []
            for task_id in processing_items:
                task_data = self.redis_client.hget(self.tasks_key, task_id)
                if task_data:
                    processing_tasks.append(json.loads(task_data))
            
            return {
                'queue_tasks': queue_tasks,
                'processing_tasks': processing_tasks,
                'registered_handlers': list(self.task_handlers.keys())
            }
            
        except Exception as e:
            logger.error(f"Error getting queue info: {e}")
            return {}


# Global processor instance
_async_processor: Optional[AsyncProcessor] = None
_processor_lock = threading.Lock()

def get_async_processor(**kwargs) -> AsyncProcessor:
    """Get global async processor instance"""
    global _async_processor
    
    if _async_processor is None:
        with _processor_lock:
            if _async_processor is None:
                _async_processor = AsyncProcessor(**kwargs)
    
    return _async_processor

def initialize_async_processor(redis_host: str = 'localhost',
                              redis_port: int = 6379,
                              redis_db: int = 1,
                              redis_password: Optional[str] = None,
                              **kwargs) -> AsyncProcessor:
    """Initialize global async processor"""
    global _async_processor
    
    with _processor_lock:
        _async_processor = AsyncProcessor(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
            **kwargs
        )
    
    return _async_processor

# Vanna SQL Optimization Setup Guide

## Overview

This guide details the complete setup and configuration of the optimized Vanna SQL application designed to handle 1000+ concurrent users with high performance and scalability.

## Architecture Overview

The optimized system includes:

- **Database Connection Pooling**: Thread-safe connection pool with health checking
- **Redis Caching Layer**: Multi-level caching for queries, results, and sessions  
- **Async Processing**: Background task queue with Redis backend
- **Session Management**: Redis-based session storage for horizontal scaling
- **Rate Limiting**: Multi-algorithm rate limiting with IP blocking
- **Load Balancing**: Nginx load balancer for multiple app instances

## Prerequisites

1. **Redis Server** (version 6.0+)
2. **Python 3.8+** with required packages
3. **SQL Server** with appropriate permissions
4. **Ollama** with your chosen model
5. **Docker & Docker Compose** (for containerized deployment)
6. **Nginx** (for load balancing)

## Quick Start

### 1. Environment Configuration

Copy the optimized environment template:
```bash
cp .env.optimized .env
```

Edit `.env` with your specific configuration:
```env
# Database Configuration
SQL_SERVER=your-sql-server
SQL_DATABASE=your-database
SQL_USERNAME=your-username
SQL_PASSWORD=your-password

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your-redis-password

# High Performance Settings
MAX_CONCURRENT_REQUESTS=500
DB_POOL_MAX_CONNECTIONS=200
CACHE_DEFAULT_TTL=1800
```

### 2. Redis Setup

#### Option A: Docker Redis
```bash
docker run -d --name vanna-redis -p 6379:6379 redis:7-alpine
```

#### Option B: System Redis
Install Redis on your system and start the service.

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

Additional optimization dependencies:
```bash
pip install redis hiredis
```

### 4. Start the Application

#### Single Instance
```bash
python app.py
```

#### Multiple Instances with Load Balancer
```bash
docker-compose up -d
```

## Configuration Details

### Database Pool Configuration

```env
DB_POOL_MIN_CONNECTIONS=20        # Minimum pool size
DB_POOL_MAX_CONNECTIONS=200       # Maximum pool size  
DB_POOL_HEALTH_CHECK_INTERVAL=60  # Health check frequency (seconds)
```

### Redis Caching Configuration

```env
CACHE_DEFAULT_TTL=1800            # Default cache TTL (30 minutes)
CACHE_SQL_RESULT_TTL=3600         # SQL result cache TTL (1 hour)
CACHE_SESSION_TTL=7200            # Session cache TTL (2 hours)
```

### Rate Limiting Configuration

```env
RATE_LIMIT_API_REQUESTS_PER_MINUTE=120    # API requests per minute
RATE_LIMIT_SQL_GENERATION_PER_MINUTE=60   # SQL generations per minute
RATE_LIMIT_GENERAL_API_PER_MINUTE=200     # General API calls per minute
```

### Async Processing Configuration

```env
ASYNC_WORKER_COUNT=10             # Number of background workers
ASYNC_MAX_RETRIES=3               # Maximum retry attempts
ASYNC_TASK_TIMEOUT=600            # Task timeout (10 minutes)
```

## Performance Tuning

### For 1000+ Concurrent Users

1. **Database Pool Settings**:
   ```env
   DB_POOL_MIN_CONNECTIONS=50
   DB_POOL_MAX_CONNECTIONS=500
   ```

2. **Redis Memory Configuration**:
   ```bash
   # In redis.conf or docker command
   maxmemory 4gb
   maxmemory-policy allkeys-lru
   ```

3. **Application Scaling**:
   ```env
   MAX_CONCURRENT_REQUESTS=1000
   MAX_REQUESTS_PER_USER=100
   ```

4. **Nginx Load Balancing**:
   - Deploy multiple app instances
   - Configure upstream servers in nginx.conf
   - Enable connection pooling

### Memory Optimization

1. **Python GC Tuning**:
   ```python
   import gc
   gc.set_threshold(700, 10, 10)
   ```

2. **Redis Memory Optimization**:
   ```env
   # Use memory-efficient data structures
   CACHE_COMPRESSION_ENABLED=True
   ```

## Monitoring and Metrics

### Health Check Endpoint

```bash
curl http://localhost:5000/api/health
```

Response includes:
- Database connection status
- Redis connectivity
- Optimization system health
- Active user counts
- Connection pool statistics

### Key Metrics to Monitor

1. **Database Pool**:
   - Active connections
   - Pool utilization
   - Connection timeouts

2. **Redis Cache**:
   - Hit/miss ratios
   - Memory usage
   - Eviction rate

3. **Rate Limiting**:
   - Blocked requests
   - Rate limit violations
   - IP blocks

4. **Session Management**:
   - Active sessions
   - Session creation rate
   - Cleanup efficiency

## Load Testing

### Using Apache Bench
```bash
# Test basic endpoint
ab -n 1000 -c 100 http://localhost/api/health

# Test with concurrent SQL queries
ab -n 500 -c 50 -p query.json -T application/json http://localhost/api/ask
```

### Expected Performance Targets

- **Response Time**: < 200ms for cached queries
- **Throughput**: 1000+ requests/second
- **Concurrent Users**: 1000+ simultaneous users
- **Memory Usage**: < 4GB for app + Redis
- **CPU Usage**: < 80% under peak load

## Troubleshooting

### Common Issues

1. **Redis Connection Failed**:
   ```bash
   # Check Redis status
   redis-cli ping
   
   # Check connectivity
   telnet localhost 6379
   ```

2. **Database Pool Exhausted**:
   - Increase `DB_POOL_MAX_CONNECTIONS`
   - Check for connection leaks
   - Monitor connection timeout settings

3. **High Memory Usage**:
   - Reduce cache TTL values
   - Enable Redis memory optimization
   - Check for memory leaks in application

4. **Rate Limiting Too Aggressive**:
   - Adjust rate limit rules
   - Check IP whitelisting
   - Monitor false positives

### Debug Mode

Enable debug logging:
```env
LOG_LEVEL=DEBUG
FLASK_DEBUG=True
```

### Performance Profiling

```bash
# Install profiling tools
pip install py-spy

# Profile running application
py-spy top --pid <python-pid>
```

## Security Considerations

1. **Redis Security**:
   ```env
   REDIS_PASSWORD=strong-password
   # Configure Redis AUTH
   ```

2. **Rate Limiting**:
   ```env
   ENABLE_IP_BLOCKING=True
   ENABLE_RATE_LIMITING=True
   ```

3. **Session Security**:
   ```env
   FLASK_SECRET_KEY=cryptographically-strong-key
   SESSION_COOKIE_SECURE=True
   ```

## Deployment Strategies

### Single Server Deployment

1. Install all components on one server
2. Use systemd for service management
3. Configure log rotation
4. Set up monitoring

### Multi-Server Deployment

1. **Application Servers**: Multiple Flask instances
2. **Redis Cluster**: For high availability
3. **Load Balancer**: Nginx or HAProxy
4. **Database**: Separate SQL Server cluster

### Container Deployment

```bash
# Build and deploy with Docker Compose
docker-compose up -d --scale app1=3 --scale app2=3
```

## Maintenance

### Regular Tasks

1. **Redis Maintenance**:
   ```bash
   # Memory analysis
   redis-cli --bigkeys
   
   # Cleanup expired keys
   redis-cli FLUSHDB
   ```

2. **Database Pool Health**:
   - Monitor connection pool statistics
   - Check for failed health checks
   - Restart pool if needed

3. **Log Rotation**:
   ```bash
   # Configure logrotate for application logs
   /etc/logrotate.d/vanna-app
   ```

### Backup and Recovery

1. **Redis Data**:
   ```bash
   # Backup Redis data
   redis-cli BGSAVE
   ```

2. **Application State**:
   - Backup ChromaDB data
   - Export learning data
   - Save configuration files

## Support and Documentation

For additional support:
- Check application logs in `/logs/` directory
- Monitor health check endpoint
- Review Redis logs for cache issues
- Consult performance monitoring dashboard

## Version History

- **v1.0**: Basic optimization implementation
- **v1.1**: Added Redis caching and session management  
- **v1.2**: Implemented async processing and rate limiting
- **v1.3**: Added database connection pooling
- **v1.4**: Complete optimization stack with load balancing

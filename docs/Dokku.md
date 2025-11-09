# Deploying to Dokku

## Overview

Dokku will automatically detect your app as Python (via `requirements.txt`) and use the `Procfile` to start your application with Gunicorn instead of Flask's development server.

## Required Files

Ensure you have these files in your repository:

```
your-app/
├── flight_service.py
├── requirements.txt
├── Procfile
└── runtime.txt (optional, but recommended)
```

## Deployment Steps

### 1. Create the Dokku app

On your Dokku server:

```bash
dokku apps:create flight-finder
```

### 2. (Optional) Set up a domain

```bash
dokku domains:add flight-finder your-domain.com
```

### 3. (Optional) Configure environment variables

If you want to add API key authentication:

```bash
dokku config:set flight-finder SERVICE_API_KEY=your_secret_key_here
```

### 4. Deploy from your local machine

Add Dokku as a git remote:

```bash
git remote add dokku dokku@your-server.com:flight-finder
```

Push to deploy:

```bash
git push dokku main
```

Or if your branch is named differently:

```bash
git push dokku master:main
```

### 5. Enable SSL (recommended)

Using Let's Encrypt:

```bash
dokku letsencrypt:enable flight-finder
```

## Dokku Configuration Options

### Scale workers

If you need more capacity:

```bash
dokku ps:scale flight-finder web=2
```

### Set memory limits

```bash
dokku resource:limit flight-finder --memory 512m
```

### View logs

```bash
dokku logs flight-finder -t
```

### Restart the app

```bash
dokku ps:restart flight-finder
```

## Understanding the Procfile

```
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 60 flight_service:app
```

Breaking it down:

- `web:` - Tells Dokku this is the web process
- `gunicorn` - Production WSGI server
- `--bind 0.0.0.0:$PORT` - Bind to all interfaces on Dokku's assigned port
- `--workers 2` - Run 2 worker processes (adjust based on CPU cores)
- `--threads 2` - Run 2 threads per worker
- `--timeout 60` - 60 second timeout (API requests can take time)
- `flight_service:app` - Module name : Flask app variable

### Worker/Thread Recommendations

**Formula:** `(2 × CPU_cores) + 1`

For a 1-core server:
```
--workers 2 --threads 2
```

For a 2-core server:
```
--workers 4 --threads 2
```

For memory-constrained servers (< 1GB RAM):
```
--workers 1 --threads 4
```

## Testing Your Deployment

### 1. Health check

```bash
curl https://your-domain.com/health
```

Expected response:
```json
{"status": "ok"}
```

### 2. Test closest flight endpoint

```bash
curl "https://your-domain.com/closest-flight?lat=37.7749&lon=-122.4194&radius=150"
```

### 3. With API key (if configured)

```bash
curl -H "X-API-Key: your_secret_key_here" \
  "https://your-domain.com/closest-flight?lat=37.7749&lon=-122.4194&radius=150"
```

## Updating Your Pico Configuration

Once deployed, update your Pico's `main.py`:

```python
# Change from local IP
SERVICE_URL = "http://192.168.1.100:3000"

# To your Dokku domain
SERVICE_URL = "https://your-domain.com"

# If you set an API key
API_KEY = "your_secret_key_here"
```

## Monitoring

### View resource usage

```bash
dokku resource:report flight-finder
```

### Check app status

```bash
dokku ps:report flight-finder
```

### View real-time logs

```bash
dokku logs flight-finder -t
```

## Troubleshooting

### App won't start

Check logs:
```bash
dokku logs flight-finder
```

Common issues:
- Missing `gunicorn` in `requirements.txt` ✓ (already included)
- Wrong module name in Procfile
- Port binding issues (ensure using `$PORT`)

### Timeouts

If API requests are timing out, increase the timeout:

```
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120 flight_service:app
```

### High memory usage

Reduce workers:
```
web: gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60 flight_service:app
```

### Rate limiting from FlightRadar24

Add caching (see optimization guide) or implement request throttling.

## Optional: Add Health Checks

Configure Dokku to check your app's health:

```bash
dokku checks:set flight-finder web /health
```

This will:
- Check `/health` endpoint before routing traffic
- Automatically restart if health check fails
- Ensure zero-downtime deployments

## Performance Optimization

### 1. Enable response caching

Add to your Flask service (before the routes):

```python
from flask_caching import Cache

cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 30  # Cache for 30 seconds
})

@app.route('/closest-flight', methods=['GET'])
@cache.cached(timeout=30, query_string=True)
def get_closest_flight():
    # ... existing code
```

Add to `requirements.txt`:
```
Flask-Caching==2.1.0
```

### 2. Enable gzip compression

Add to your Flask service:

```python
from flask_compress import Compress

Compress(app)
```

Add to `requirements.txt`:
```
Flask-Compress==1.14
```

## Cost Estimate

Dokku is self-hosted, so costs depend on your server:

- **DigitalOcean Droplet:** $4-6/month (512MB-1GB RAM)
- **Hetzner Cloud:** ~€3-5/month
- **AWS Lightsail:** $3.50-5/month
- **Home server:** Free (electricity only)

The app is lightweight and should run fine on minimal resources.

# Deploying to Dokku

This assumes you already have a server running Dokku: https://dokku.com

## Overview

Dokku will automatically detect your app as Python (via `requirements.txt`) and use the `Procfile` to start your application with Gunicorn instead of Flask's development server.

## Deployment Steps

### 1. Create the Dokku app

On your Dokku server (remote machine):

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

Add Dokku as a git remote (local machine):

```bash
git remote add dokku dokku@your-server.com:flight-finder
```

Push to deploy:

```bash
git push dokku main
```

### 5. Enable SSL (recommended)

Using Let's Encrypt (back on the remote machine):

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

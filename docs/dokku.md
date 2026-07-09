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

### 6. Persistent storage (fleet database)

The fleet view records a heartbeat per device into a SQLite database. A container's filesystem is wiped on every deploy, so the database must live on a mounted host volume to survive `git push dokku main`.

On the Dokku server:

```bash
# Create a host directory with the right ownership, then mount it into the app
dokku storage:ensure-directory flight-finder
dokku storage:mount flight-finder /var/lib/dokku/data/storage/flight-finder:/app/storage

# Point the app at a database + OTA store on the volume, set the fleet admin
# token, and name your own device as the OTA canary (see below)
dokku config:set flight-finder \
  FLEET_DB_PATH=/app/storage/fleet.db \
  OTA_DIR=/app/storage/ota \
  ADMIN_TOKEN=your_admin_secret \
  CANARY_DEVICE_ID=your_device_id

dokku ps:restart flight-finder

# Confirm the mount
dokku storage:report flight-finder
```

Notes:
- `storage:ensure-directory` creates the host directory owned by the container's runtime user, so the app can write to it.
- The data now survives redeploys and restarts because it lives on the host volume, not the ephemeral container filesystem. This matters for both the fleet database and the OTA payload (`OTA_DIR`), which holds the published device firmware + `manifest.json`.
- WAL mode creates sibling `fleet.db-wal` and `fleet.db-shm` files next to `fleet.db` on the volume - this is expected.
- `ADMIN_TOKEN` guards `/fleet` (HTML), `/fleet.json`, `/fleet/command`, `/fleet/promote`, and `/ota/publish`. In a browser, `/fleet` prompts for HTTP Basic Auth: enter anything as the username and the admin token as the password. For scripts, send it as `X-Admin-Token: <token>` (or `?token=<token>`). Without `ADMIN_TOKEN` set, those endpoints refuse to serve (503) rather than exposing device IPs.
- `CANARY_DEVICE_ID` is the one device (your own - find its id on `/fleet` or the device's `/status`) that receives a freshly published OTA build first. Everyone else only updates after you Promote it. See the device README's [Fleet management & over-the-air updates](../examples/interstate75/README.md#fleet-management--over-the-air-updates).

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

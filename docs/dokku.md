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

### 3. (Optional) Configure device authentication

Devices authenticate to the flight/OTA endpoints with an `X-API-Key` header. There are two ways to provision keys, and they work together:

- **Per-client keys (recommended)** - create one key per device from the `/fleet` admin page's "API keys" panel, so each client has its own key you can revoke individually. These live in the fleet database (no redeploy to add/revoke); managing them needs `ADMIN_TOKEN` (below).
- **Shared key** - a single `SERVICE_API_KEY` every device may use:

  ```bash
  dokku config:set flight-finder SERVICE_API_KEY=your_secret_key_here
  ```

  It stays valid *alongside* per-client keys, which makes migrating an existing fleet easy: keep it set while you reissue devices their own keys, then retire it once they're all migrated:

  ```bash
  dokku config:unset flight-finder SERVICE_API_KEY
  ```

If neither is configured - no `SERVICE_API_KEY` **and** no keys created - the flight endpoints stay open (convenient for local/dev). Set at least one before exposing the service publicly.

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
- `ADMIN_TOKEN` guards `/fleet` (HTML), `/fleet.json`, `/fleet/command`, `/fleet/promote`, `/ota/publish`, and the per-client-key endpoints (`/keys`, `/keys/enable`, `/keys/disable`, `/keys/delete`). In a browser, `/fleet` prompts for HTTP Basic Auth: enter anything as the username and the admin token as the password. For scripts, send it as `X-Admin-Token: <token>` (or `?token=<token>`). Without `ADMIN_TOKEN` set, those endpoints refuse to serve (503) rather than exposing device IPs.
- **Per-client API keys**: the `/fleet` page's "API keys" panel creates (one per client/device), labels, and revokes keys - a disabled or deleted key is rejected on that device's next request, without touching any other device. A device presents its key as `X-API-Key`; provision it via the device's WiFi setup hotspot, `push.py`, or by baking it into `secrets.py` (see the device README). The device table's `Client` column shows which key each device last authenticated with.
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

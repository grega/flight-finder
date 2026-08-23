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

If the app ends up behind Cloudflare Access, swap this for a Cloudflare Origin
CA certificate - Let's Encrypt renewals break once an Access policy covers the
ACME challenge path. See [Locking the origin to
Cloudflare](#locking-the-origin-to-cloudflare).

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
- The admin endpoints - `/fleet` (HTML), `/fleet.json`, `/fleet/command`, `/fleet/logs`, `/fleet/promote`, `/ota/publish`, and the per-client-key endpoints (`/keys`, `/keys/enable`, `/keys/disable`, `/keys/delete`) - accept either a verified Cloudflare Access identity (browsers, see below) or `ADMIN_TOKEN` as `X-Admin-Token: <token>` / `Authorization: Bearer <token>` / `?token=<token>` (scripts). With neither configured they refuse to serve (503) rather than exposing device IPs.
- **Per-client API keys**: the `/fleet` page's "API keys" panel creates (one per client/device), labels, and revokes keys - a disabled or deleted key is rejected on that device's next request, without touching any other device. A device presents its key as `X-API-Key`; provision it via the device's WiFi setup hotspot, `push.py`, or by baking it into `secrets.py` (see the device README). The device table's `Client` column shows which key each device last authenticated with.
- `CANARY_DEVICE_ID` is the one device (your own - find its id on `/fleet` or the device's `/status`) that receives a freshly published OTA build first. Everyone else only updates after you Promote it. See the device README's [Fleet management & over-the-air updates](../examples/interstate75/README.md#fleet-management--over-the-air-updates).

## Admin access via Cloudflare Access

The `/fleet` page and the other admin endpoints are meant for you, not for the
devices. Rather than a shared password in the browser, put Cloudflare Access in
front of them and have the app verify the identity Access asserts.

1. **Create the Access application** (Zero Trust > Access > Applications, type
   *Self-hosted*). Cover every admin path, not just `/fleet` - the fleet page's
   buttons call `/keys*`, `/fleet/command` and `/fleet/promote`, and its log
   links hit `/fleet/logs`. Either add one include path per admin route, or
   protect the whole hostname and *bypass* the paths the devices need:
   `/closest-flight`, `/flights-in-radius`, `/device/checkin`, `/ota/manifest`,
   `/ota/file/*`, `/health`. Devices can't complete an SSO login, so anything
   they call must not sit behind an Access policy.
2. **Add a policy** - an *Allow* rule for your own email (or your domain).
3. **Copy the Application Audience (AUD) tag** from the application's overview,
   and note your team domain (`<team>.cloudflareaccess.com`).
4. **Configure the app**:

   ```bash
   dokku config:set flight-finder \
     CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com \
     CF_ACCESS_AUD=<application-audience-tag>
   ```

The service then fetches the team's public keys from
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` (cached, re-fetched
on rotation) and checks each request's `Cf-Access-Jwt-Assertion` header - or the
`CF_Authorization` cookie, which is what same-origin `fetch()` calls from the
fleet page carry - for a valid signature, audience, issuer, and expiry.

Notes:
- **Verification is what makes this safe.** The app trusts a *signed* assertion,
  not the mere fact that a request arrived. A request that reaches the origin
  outside Cloudflare carries no assertion and is refused.
- **Keep `ADMIN_TOKEN` set.** `publish.py` and any `curl` against `/ota/publish`
  authenticate with it; an SSO redirect would break them. If you'd rather not
  keep a shared token, issue an Access **service token** instead, add it to the
  application's policy, and have the script send the `CF-Access-Client-Id` /
  `CF-Access-Client-Secret` pair - service-token assertions verify through the
  same path (they carry `common_name` rather than `email`).
- **The origin is reachable around Access until you lock it.** See
  [Locking the origin to Cloudflare](#locking-the-origin-to-cloudflare) below -
  worth doing before you rely on Access.

## Locking the origin to Cloudflare

Access only protects traffic that goes *through* Cloudflare. Dokku routes by
hostname, but the `Host` header is caller-controlled, so an app on a public
Dokku host answers to anyone who knows its IP:

```bash
# From anywhere; substitute your Dokku host's public IP
curl -sk -o /dev/null -w '%{http_code}\n' \
  -H 'Host: your-app.example.com' https://<dokku-host-ip>/fleet
```

A `401` there is the app refusing an unauthenticated request - which is exactly
why it verifies the Access assertion rather than trusting the perimeter. But a
request that never touches Cloudflare never touches your Access policy either,
so close the bypass with an origin certificate plus Authenticated Origin Pulls
(mTLS): Cloudflare presents a client certificate, and nginx refuses anyone who
can't produce it.

### 1. Origin certificate (optional, but do it before tightening Access)

A publicly-trusted certificate on the origin renews over HTTP-01 at
`/.well-known/acme-challenge/`. Once an Access policy covers the whole hostname
that path redirects to the Access login, the renewal fails, and the certificate
quietly expires. A [Cloudflare Origin CA
certificate](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)
lasts 15 years and needs no challenge, which removes the failure mode:

```bash
# Generate in the dashboard (SSL/TLS > Origin Server > Create Certificate),
# save the certificate + key locally, then:
tar cvf cert-key.tar server.crt server.key
dokku certs:add your-app < cert-key.tar
```

Set the zone's SSL/TLS mode to **Full (strict)** so Cloudflare validates it.

### 2. Client certificate for Authenticated Origin Pulls

Zone-level AOP can use Cloudflare's shared certificate, but that only proves a
request came from *Cloudflare* - any Cloudflare customer could point a zone at
your IP and pass. Uploading your own certificate proves it came from Cloudflare
*carrying your certificate*, which is what you want.

Cloudflare wants a **leaf** (end-entity) certificate here, so `openssl req
-x509` on its own won't do - that produces a `CA:TRUE` certificate and the
upload is rejected with "Missing leaf certificate". Make a throwaway CA, then a
client certificate signed by it. Nothing public trusts either one; they only
ever have to verify against your own nginx.

```bash
# The CA - nginx trusts this, Cloudflare never sees it
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout cf-origin-pull-ca.key -out cf-origin-pull-ca.crt \
  -subj "/CN=example.com origin pull CA"

# The leaf - this is what you upload to Cloudflare
openssl req -newkey rsa:2048 -nodes \
  -keyout cf-origin-pull.key -out cf-origin-pull.csr \
  -subj "/CN=example.com origin pull"

cat > leaf.ext <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth
EOF

openssl x509 -req -in cf-origin-pull.csr \
  -CA cf-origin-pull-ca.crt -CAkey cf-origin-pull-ca.key -CAcreateserial \
  -days 3650 -out cf-origin-pull.crt -extfile leaf.ext

# Sanity check - this is the verification nginx will perform
openssl verify -purpose sslclient -CAfile cf-origin-pull-ca.crt cf-origin-pull.crt
```

Generate these somewhere outside the repo - two of the four files are private
keys, and this one is public.

Upload `cf-origin-pull.crt` (certificate) and `cf-origin-pull.key` (private key)
under SSL/TLS > Origin Server > Authenticated Origin Pulls, then enable the zone
toggle. **Keep both private keys** - Cloudflare won't show the uploaded one
again, and you need the CA key to issue a replacement leaf later.

### 3. Enforce it in nginx

Cloudflare must be presenting the certificate *before* nginx starts demanding
one, or every request 400s in the gap.

nginx verifies the certificate Cloudflare presents against its **issuer**, so
copy up the *CA* certificate here - not the leaf you uploaded to Cloudflare, and
never either private key.

```bash
# On the Dokku host, as root
mkdir -p /etc/nginx/certs
install -m 644 cf-origin-pull-ca.crt /etc/nginx/certs/cf-origin-pull-ca.crt

mkdir -p /home/dokku/your-app/nginx.conf.d
cat > /home/dokku/your-app/nginx.conf.d/origin-pull.conf <<'EOF'
ssl_client_certificate /etc/nginx/certs/cf-origin-pull-ca.crt;
ssl_verify_client on;
EOF
chown -R dokku:dokku /home/dokku/your-app/nginx.conf.d
dokku proxy:build-config your-app

# Confirm it landed. `nginx:show-config` shows only the include *directive* -
# `nginx -T` resolves the included files, so this is the check that means
# something:
nginx -T 2>/dev/null | grep -i ssl_verify_client
```

`nginx.conf.d/*.conf` is included inside *that app's* server block, so the rule
stays scoped to one app. Putting the same directives in the `http` block or a
default server would enforce mTLS for every app on the host.

### 4. Verify

```bash
# Direct to the origin: should now fail the handshake, not return 401
curl -sk -H 'Host: your-app.example.com' https://<dokku-host-ip>/health
# expect: TLS alert, or "400 No required SSL certificate was sent"

# Through Cloudflare: still fine
curl -s https://your-app.example.com/health
```

Notes:
- **Scope.** The zone toggle makes Cloudflare present the certificate to every
  proxied origin in the zone. Origins that don't ask for a client certificate
  never see it, so other apps are unaffected - enforcement is the nginx side,
  per app. DNS-only ("grey-clouded") hostnames aren't proxied and so aren't
  covered at all.
- **Devices are fine.** They reach the service through Cloudflare like any other
  client, so mTLS on the vhost doesn't affect check-ins or OTA.
- **Alternative.** A `cloudflared` tunnel with port 443 unpublished avoids
  inbound origin traffic entirely, at the cost of running the tunnel daemon.

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

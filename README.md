# Flight Finder Service

A Flask-based API that finds the closest aircraft to given coordinates using FlightRadar24 data.

The service was designed to be consumed by lower-power WiFi-enabled devices (eg. Raspberry Pi Pico 2 W) hooked up to a display of sorts in order to show nearby flight data.

See the [interstate75 directory](examples/interstate75) for an example project using a Pimoroni "Interstate 75 W" (RP2350) controller with an LED matrix display, along with the accompanying blog post:

https://blog.gregdev.com/posts/2025-11-19-flight-finder-display

![ff-display-1](https://github.com/user-attachments/assets/16e42d57-3e22-4852-b11e-56f426f2234e)

***

There's also an [example Python script](examples/python-aircraft-monitor) for monitoring nearby aircraft by type, and alerting if certain conditions are met.

***

## Quick Start

### Prerequisites

- [asdf](https://asdf-vm.com/guide/getting-started.html)
 
### Installation

1. Install Python via [asdf](https://asdf-vm.com/guide/getting-started.html) (recommended):

    ```bash
    asdf install
    ```

2. Create a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Deploy the Cloudflare Worker proxy that FlightRadar24 access depends on. See [FlightRadar24 access (Cloudflare Worker proxy)](#flightradar24-access-cloudflare-worker-proxy) below - without it, flight-detail fields (`aircraft.model`, airport names) won't be returned.

5. Configure your `.env` file with the Worker URL from the previous step, plus an optional API key:

   ```bash
   FR24_PROXY_URL=https://fr24-proxy.<your-subdomain>.workers.dev/?url=
   SERVICE_API_KEY=
   ```

6. Run the development server:

   ```bash
   python flight_service.py
   ```

   The service will start on: http://0.0.0.0:7478

7. Run the tests:

   ```bash
   pytest
   ```

## FlightRadar24 access (Cloudflare Worker proxy)

Around 2026-05-01, FlightRadar24 deployed Cloudflare bot protection with TLS fingerprinting on the undocumented JSON endpoints this service uses. The unmaintained upstream [JeanExtreme002/FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI) library now receives `403 Forbidden` on the `clickhandler` endpoint - the one that supplies `aircraft.model`, `route.origin_name`, and `route.destination_name`. See upstream [issue #98](https://github.com/JeanExtreme002/FlightRadarAPI/issues/98) for context. User-Agent or header tweaks won't get past TLS fingerprinting.

The workaround is the [DimaD16/FlightRadarAPI](https://github.com/DimaD16/FlightRadarAPI) fork (PyPI: `ddima16-flightradarapi`), which routes requests through a Cloudflare Worker you deploy yourself. From CF's edge the request originates inside Cloudflare's network and isn't fingerprinted as a bot.

### 1. Deploy the Cloudflare Worker

The Worker source lives at [DimaD16/cloudflare-workers-fr24-proxy](https://github.com/DimaD16/cloudflare-workers-fr24-proxy). It's a ~30-line script that takes a `?url=<target>` query parameter, forwards the request to FR24 with a recent Chrome User-Agent and `X-Requested-With: com.flightradar24.iphone`, and returns the response.

```bash
git clone https://github.com/DimaD16/cloudflare-workers-fr24-proxy.git
cd cloudflare-workers-fr24-proxy
npx wrangler login        # one-time browser auth with Cloudflare
npx wrangler deploy
```

Wrangler prints the deployed URL on success, eg. `https://fr24-proxy.<your-subdomain>.workers.dev`. You'll use this in step 3.

The Cloudflare Workers free tier allows 100,000 requests/day, far more than this service needs.

> **Security note:** the Worker as published is an **open relay** - anyone with the URL can use it to fetch arbitrary URLs (the `?url=` parameter isn't restricted to FR24 hosts, and there's no authentication). Before exposing the URL anywhere public, consider hardening it:
> - Restrict the target host to FR24 domains in `worker.js` (eg. `if (!new URL(targetUrl).hostname.endsWith("flightradar24.com")) return new Response("Forbidden", { status: 403 });`).
> - Optionally require a shared-secret header that the Worker checks before proxying.

### 2. Swap the library

In [requirements.txt](requirements.txt), replace:

```
FlightRadarAPI==1.3.15
```

with:

```
ddima16-flightradarapi
```

Then reinstall:

```bash
pip install -r requirements.txt
```

### 3. Set `FR24_PROXY_URL`

Add the Worker URL to `.env`. **Note the trailing `/?url=`** - the library appends FR24 URLs to this string:

```bash
FR24_PROXY_URL=https://fr24-proxy.<your-subdomain>.workers.dev/?url=
```

### 4. Verify

Restart the service and hit `/closest-flight` with a busy area - `aircraft.model` and `route.origin_name` / `route.destination_name` should populate when a flight is found:

```bash
curl "http://localhost:7478/closest-flight?lat=33.0118884&lon=-97.0558339&radius=25"
```

If the Worker is misconfigured the service still returns the basic feed fields (number, IATA codes, registration) but silently drops the detail fields - the `try/except` around `get_flight_details` in `flight_service.py` swallows proxy errors. Check `wrangler tail` while running a request to confirm the Worker is being hit.

## Usage

### Health Check

Test if the service is running:

```bash
curl http://localhost:7478/health
```

Response:
```json
{"status": "ok"}
```

### Find Closest Flight

**Basic request:**

```bash
curl "http://localhost:7478/closest-flight?lat=37.7749&lon=-122.4194&radius=25"
```

**With API key authentication:**

```bash
curl -H "X-API-Key: your_secret_key_here" \
  "http://localhost:7478/closest-flight?lat=37.7749&lon=-122.4194&radius=25"
```

### API Parameters

| Parameter | Type | Required | Description | Range |
|-----------|------|----------|-------------|-------|
| `lat` | float | Yes | Latitude | -90 to 90 |
| `lon` | float | Yes | Longitude | -180 to 180 |
| `radius` | float | No | Search radius in km | 1 to 500 (default: 10) |

### Response Format

Success (flight found):

```json
{
  "found": true,
  "distance_km": 45.23,
  "flight": {
    "id": "2f3a4b5c",
    "number": "UA123",
    "callsign": "UAL123",
    "icao_24bit": "A12345",
    "position": {
      "latitude": 37.8,
      "longitude": -122.5,
      "altitude": 33000,
      "heading": 270,
      "ground_speed": 450,
      "vertical_speed": 1500
    },
    "aircraft": {
      "code": "B738",
      "model": "Boeing 737-800",
      "registration": "N12345"
    },
    "airline": {
      "icao": "UAL",
      "iata": "UA"
    },
    "route": {
      "origin_iata": "SFO",
      "destination_iata": "LAX",
      "origin_name": "San Francisco International Airport",
      "destination_name": "Los Angeles International Airport"
    }
  }
}
```

No flights found:

```json
{
  "found": false,
  "message": "No flights found in search area"
}
```

Error:

```json
{
  "error": "Invalid parameters. Required: lat, lon. Optional: radius"
}
```

### Find Flights in Radius

This works similarly to the `/closest-flight` endpoint but returns all flights within the specified radius.

```bash
curl "http://localhost:7478/flights-in-radius?lat=37.7749&lon=-122.4194&radius=25"
```

The response is a `flights` array containing all flights within the specified radius (each `flight` object has the same structure as in the `/closest-flight` response, see above).

## API Endpoints

### `GET /`

Returns API documentation and available endpoints.

### `GET /health`

Health check endpoint for monitoring.

Response:
```json
{"status": "ok"}
```

### `GET /closest-flight`

Find the closest in-flight aircraft to given coordinates.

Query Parameters:
- `lat` (required): Latitude
- `lon` (required): Longitude  
- `radius` (optional): Search radius in km (default: 10)

Headers:
- `X-API-Key` (optional): API key, if authentication is enabled / required

### `GET /flights-in-radius`

Find all in-flight aircraft within a given radius of the specified coordinates.

Query Parameters:
- `lat` (required): Latitude
- `lon` (required): Longitude
- `radius` (optional): Search radius in kilometers (default: 10)

Headers:
- `X-API-Key` (optional): API key, if authentication is enabled / required

## Debug

```python
app.run(host='0.0.0.0', port=port, debug=True) # enable debug mode
```

## Production Deployment

This README covers development setup. For production deployments, see the deployment guides in `/docs`.

## Data Source

This service uses data from [FlightRadar24](https://www.flightradar24.com/) via the unofficial [DimaD16/FlightRadarAPI](https://github.com/DimaD16/FlightRadarAPI) fork (PyPI: `ddima16-flightradarapi`), routed through a self-hosted Cloudflare Worker proxy. The original [JeanExtreme002/FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI) is no longer functional against FR24's Cloudflare-protected endpoints - see the [FlightRadar24 access](#flightradar24-access-cloudflare-worker-proxy) section above for setup and context.

**Important:** This service is for educational and personal use only. For commercial use, contact [business@fr24.com](mailto:business@fr24.com) or use the [official FlightRadar24 API](https://fr24api.flightradar24.com/).

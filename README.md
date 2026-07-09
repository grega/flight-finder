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

4. Configure an API key in a `.env` file to enable optional authentication:

   ```bash
   SERVICE_API_KEY=
   ```

5. Run the development server:

   ```bash
   python flight_service.py
   ```

   The service will start on: http://0.0.0.0:7478

6. Run the tests:

   ```bash
   pytest
   ```

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
| `max_altitude` | float | No | Altitude ceiling in feet - flights above this are ignored (useful to filter out cruise-altitude overflights and focus on flights arriving/departing nearby airports) | ≥ 0 (default: no ceiling) |

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

## Authentication

The flight endpoints (and the device check-in/OTA endpoints) authenticate with an `X-API-Key` header. A request is accepted if its key matches **either**:

- **`SERVICE_API_KEY`** - a single shared key set in the environment (`.env` locally, `dokku config:set` in production).
- **A per-client key** - created and revoked from the admin [`/fleet`](#fleet-tracking) page's "API keys" panel, one per device, stored in the fleet database. Disabling or deleting a key blocks just that device on its next request, leaving the rest of the fleet untouched.

Both are honoured at once, so you can keep the shared key working while migrating a fleet to per-client keys, then retire it. If **neither** is configured the endpoints stay open (handy for local dev - lock it down before exposing the service publicly). Managing per-client keys needs `ADMIN_TOKEN`; for production setup see [docs/dokku.md](docs/dokku.md).

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
- `max_altitude` (optional): Altitude ceiling in feet - flights above this are ignored (default: no ceiling)

Headers:
- `X-API-Key` (optional): API key, if authentication is enabled / required

### `GET /flights-in-radius`

Find all in-flight aircraft within a given radius of the specified coordinates.

Query Parameters:
- `lat` (required): Latitude
- `lon` (required): Longitude
- `radius` (optional): Search radius in kilometers (default: 10)
- `max_altitude` (optional): Altitude ceiling in feet - flights above this are ignored (default: no ceiling)

Headers:
- `X-API-Key` (optional): API key, if authentication is enabled / required

### Fleet tracking

Every authenticated call to `/closest-flight` and `/flights-in-radius` doubles as a device heartbeat: the service records the caller (from the `User-Agent` and an optional `X-Device-Id` header), its reported code version, source IP, and last-seen time into a small SQLite database (`FLEET_DB_PATH`, default `fleet.db`). This is how the Interstate 75 displays report in - see [examples/interstate75](examples/interstate75/). No extra requests are made; it piggybacks on the polling the devices already do.

The fleet endpoints are guarded by a separate `ADMIN_TOKEN` env var (independent of `SERVICE_API_KEY`). When `ADMIN_TOKEN` is unset they return `503` rather than exposing device data.

#### `GET /fleet`

Human-readable HTML table of known devices (ID, label, version, last-seen with an offline flag, IP, request count). In a browser it prompts for HTTP Basic Auth - enter the admin token as the password.

#### `GET /fleet.json`

The same data as JSON.

Headers / auth (both endpoints):
- `X-Admin-Token: <token>`, `Authorization: Bearer <token>`, HTTP Basic Auth password, or `?token=<token>`.

For persistent storage across deploys, see [docs/dokku.md](docs/dokku.md).

## Debug

```python
app.run(host='0.0.0.0', port=port, debug=True) # enable debug mode
```

## Production Deployment

This README covers development setup. For production deployments, see the deployment guides in `/docs`.

## Data Source

This service uses data from [FlightRadar24](https://www.flightradar24.com/) via the unofficial [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI) library.

**Important:** This service is for educational and personal use only. For commercial use, contact [business@fr24.com](mailto:business@fr24.com) or use the [official FlightRadar24 API](https://fr24api.flightradar24.com/).

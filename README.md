# Flight Finder Service

A Flask-based REST API service that finds the closest aircraft to given coordinates using FlightRadar24 data. Designed to be lightweight and queryable by IoT devices like the Raspberry Pi Pico 2 W.

## Features

- Find closest in-flight aircraft to any coordinate
- Search by latitude/longitude with configurable radius
- Optional API key authentication
- Returns detailed flight information (route, aircraft, position)
- Lightweight JSON responses optimized for IoT devices
- Health check endpoint for monitoring

## Quick Start

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Installation

1. Install Python via asdf

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

4. Configure an API key to enable optional authentication:

   ```bash
   SERVICE_API_KEY=
   ```

5. Run the development server:

   ```bash
   python flight_service.py
   ```

   The service will start on `http://0.0.0.0:3000`

## Usage

### Health Check

Test if the service is running:

```bash
curl http://localhost:3000/health
```

Response:
```json
{"status": "ok"}
```

### Find Closest Flight

**Basic request:**

```bash
curl "http://localhost:3000/closest-flight?lat=37.7749&lon=-122.4194&radius=25"
```

**With API key authentication:**

```bash
curl -H "X-API-Key: your_secret_key_here" \
  "http://localhost:3000/closest-flight?lat=37.7749&lon=-122.4194&radius=25"
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
- `X-API-Key` (optional): API key if authentication is enabled

## Debug

```python
app.run(host='0.0.0.0', port=port, debug=True)  # enable debug mode
```

## Testing with Python

```python
import requests

# Basic request
response = requests.get(
    "http://localhost:3000/closest-flight",
    params={"lat": 37.7749, "lon": -122.4194, "radius": 150}
)
print(response.json())

# With API key
headers = {"X-API-Key": "your_secret_key"}
response = requests.get(
    "http://localhost:3000/closest-flight",
    params={"lat": 37.7749, "lon": -122.4194},
    headers=headers
)
print(response.json())
```

## Production Deployment

This README covers development setup. For production deployment to Dokku, Heroku, or other platforms, see the deployment guides in `/docs`.

## Data Source

This service uses data from [FlightRadar24](https://www.flightradar24.com/) via the unofficial [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI) library.

**Important:** This service is for educational and personal use only. For commercial use, contact [business@fr24.com](mailto:business@fr24.com) or use the [official FlightRadar24 API](https://fr24api.flightradar24.com/).

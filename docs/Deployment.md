# Flight Finder Service - Deployment Guide

## Architecture Overview

```
┌──────────────┐         HTTP          ┌──────────────┐
│              │ ─────────────────────> │              │
│  Pico 2 W    │   Simple JSON API     │ Flask Server │
│  (Client)    │ <───────────────────── │  (Service)   │
│              │                        │              │
└──────────────┘                        └──────┬───────┘
                                               │
                                               │ Uses
                                               ▼
                                        ┌──────────────┐
                                        │ FlightRadar  │
                                        │     API      │
                                        └──────────────┘
```

## Part 1: Deploy Flask Service

### Option A: Run Locally (Development/Home Network)

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the service:**
   ```bash
   python flight_service.py
   ```
   
   The service will start on `http://0.0.0.0:3000`

3. **Find your local IP:**
   - Linux/Mac: `ifconfig` or `ip addr`
   - Windows: `ipconfig`
   - Look for your local network IP (e.g., 192.168.1.100)

4. **Test the service:**
   ```bash
   curl "http://YOUR_IP:3000/closest-flight?lat=37.7749&lon=-122.4194&radius=150"
   ```

### Option B: Deploy to Cloud (Production)

#### Using Railway.app (Free tier available)

1. Create a `Procfile`:
   ```
   web: gunicorn flight_service:app
   ```

2. Push to Railway:
   - Connect your GitHub repo to Railway
   - Railway will auto-detect Python and deploy
   - You'll get a URL like `https://your-app.railway.app`

#### Using Heroku

1. Create a `Procfile`:
   ```
   web: gunicorn flight_service:app
   ```

2. Deploy:
   ```bash
   heroku create your-app-name
   git push heroku main
   ```

#### Using DigitalOcean/AWS/GCP

1. Install gunicorn:
   ```bash
   pip install gunicorn
   ```

2. Run with gunicorn:
   ```bash
   gunicorn -w 4 -b 0.0.0.0:3000 flight_service:app
   ```

3. Set up nginx as reverse proxy (recommended for production)

### Optional: Add API Key Authentication

Set an environment variable:
```bash
export SERVICE_API_KEY="your_secret_key_here"
```

Or in your deployment platform's environment variables section.

## Part 2: Configure Pico 2 W Client

1. **Upload files to Pico 2 W:**
   - Use Thonny IDE or `ampy`
   - Upload `pico_client.py` as `main.py`

2. **Edit configuration in `main.py`:**
   ```python
   WIFI_SSID = "YourWiFiName"
   WIFI_PASSWORD = "YourWiFiPassword"
   SERVICE_URL = "http://192.168.1.100:3000"  # Your Flask service URL
   API_KEY = "your_secret_key_here"  # If you set one
   ```

3. **Test the connection:**
   - In Thonny REPL: `import main`
   - Should connect to WiFi and query the service

## Part 3: Testing

### Test the Flask Service

```bash
# Health check
curl http://YOUR_URL/health

# Get closest flight
curl "http://YOUR_URL/closest-flight?lat=37.7749&lon=-122.4194&radius=150"
```

### Expected Response Format

```json
{
  "found": true,
  "distance_km": 45.23,
  "flight": {
    "number": "UA123",
    "callsign": "UAL123",
    "position": {
      "latitude": 37.8,
      "longitude": -122.5,
      "altitude": 33000,
      "ground_speed": 450,
      "heading": 270
    },
    "aircraft": {
      "code": "B738",
      "registration": "N12345"
    },
    "route": {
      "origin_iata": "SFO",
      "destination_iata": "LAX"
    }
  }
}
```

## API Endpoints

### `GET /health`
Health check endpoint

**Response:**
```json
{"status": "ok"}
```

### `GET /closest-flight`
Find closest flight to coordinates

**Parameters:**
- `lat` (required): Latitude (-90 to 90)
- `lon` (required): Longitude (-180 to 180)  
- `radius` (optional): Search radius in km (default: 100, max: 500)

**Headers (optional):**
- `X-API-Key`: Your API key if configured

**Example:**
```
GET /closest-flight?lat=37.7749&lon=-122.4194&radius=150
```

## Troubleshooting

### Pico Can't Connect to Service

1. **Check WiFi connection:**
   - Verify SSID and password
   - Ensure Pico is on same network as service (for local deployment)

2. **Check service URL:**
   - Use IP address, not hostname (Pico has limited DNS)
   - Include `http://` prefix
   - Use port number if not 80/443

3. **Test from another device:**
   ```bash
   curl "http://YOUR_SERVICE_URL/health"
   ```

### Service Returns Errors

1. **Check FlightRadar24 connectivity:**
   - FlightRadar24 may rate-limit requests
   - Try from a different IP if blocked

2. **Check logs:**
   ```bash
   # If running locally
   Check terminal output
   
   # If on cloud platform
   Check platform logs (Railway/Heroku dashboard)
   ```

### Memory Issues on Pico

1. **Reduce search radius** to get fewer results
2. **Add more `gc.collect()` calls**
3. **Simplify response parsing**

## Production Considerations

1. **Rate Limiting:** Add rate limiting to prevent abuse
2. **Caching:** Cache results for a few seconds to reduce API calls
3. **HTTPS:** Use HTTPS in production (Railway/Heroku provide this automatically)
4. **Monitoring:** Add logging and error tracking
5. **Authentication:** Use API keys for production deployments

## Cost Estimates

- **Railway.app:** Free tier (500 hours/month)
- **Heroku:** Free tier discontinued, ~$5-7/month
- **DigitalOcean Droplet:** $4-6/month
- **AWS/GCP:** ~$5-10/month with free tier
- **Home server:** Free (uses your internet/power)

## Next Steps

Once running, you can:
- Add periodic checking on Pico (check every N minutes)
- Display results on LCD/OLED screen
- Trigger LED/buzzer when flight detected
- Log data to SD card
- Add deep sleep for battery operation

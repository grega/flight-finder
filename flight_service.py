"""
Web service for finding closest flights using FlightRadarAPI
Designed to be called by devices with minimal processing power (like a tracker running on a Pico W)
"""

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from FlightRadar24 import FlightRadar24API
from math import radians, cos
import os

load_dotenv()

app = Flask(__name__)
fr_api = FlightRadar24API()

API_KEY = os.getenv("SERVICE_API_KEY", None)

def calculate_bounds(lat: float, lon: float, radius_km: float) -> str:
    """Calculate bounding box for search area."""
    lat_offset = radius_km / 111.0
    lon_offset = radius_km / (111.0 * cos(radians(lat)))
    
    north = lat + lat_offset
    south = lat - lat_offset
    west = lon - lon_offset
    east = lon + lon_offset
    
    return f"{north},{south},{west},{east}"


def validate_api_key():
    """Validate API key if configured."""
    if API_KEY is None:
        return True
    
    provided_key = request.headers.get('X-API-Key')
    return provided_key == API_KEY


@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route('/closest-flight', methods=['GET'])
def get_closest_flight():
    """
    Find the closest flight to given coordinates.
    
    Query Parameters:
        lat (float): Latitude
        lon (float): Longitude
        radius (float, optional): Search radius in km (default: 10)
    
    Headers (optional):
        X-API-Key: API key for authentication
    
    Returns:
        JSON with flight information or error message
    """
    if not validate_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
        radius_km = float(request.args.get('radius', 10))
        
        if not (-90 <= lat <= 90):
            return jsonify({"error": "Latitude must be between -90 and 90"}), 400
        if not (-180 <= lon <= 180):
            return jsonify({"error": "Longitude must be between -180 and 180"}), 400
        if not (1 <= radius_km <= 500):
            return jsonify({"error": "Radius must be between 1 and 500 km"}), 400
            
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid parameters. Required: lat, lon. Optional: radius"}), 400
    
    try:
        bounds = calculate_bounds(lat, lon, radius_km)
        
        flights = fr_api.get_flights(bounds=bounds)
        
        if not flights:
            return jsonify({
                "found": False,
                "message": "No flights found in search area"
            }), 200
        
        closest_flight = None
        min_distance = float('inf')
        
        for flight in flights:
            if flight.on_ground:
                continue
            
            if flight.latitude is None or flight.longitude is None:
                continue
            
            class SearchPoint:
                def __init__(self, lat, lon):
                    self.latitude = lat
                    self.longitude = lon
            
            search_point = SearchPoint(lat, lon)
            distance = flight.get_distance_from(search_point)
            
            if distance < min_distance:
                min_distance = distance
                closest_flight = flight
        
        if not closest_flight:
            return jsonify({
                "found": False,
                "message": "No airborne flights found in search area"
            }), 200
        
        try:
            flight_details = fr_api.get_flight_details(closest_flight.id)
            closest_flight.set_flight_details(flight_details)
        except:
            pass # proceed without detailed flight info if fetching fails
        
        response = {
            "found": True,
            "distance_km": round(min_distance, 2),
            "flight": {
                "id": closest_flight.id,
                "number": closest_flight.number,
                "callsign": closest_flight.callsign,
                "icao_24bit": closest_flight.icao_24bit,
                "position": {
                    "latitude": closest_flight.latitude,
                    "longitude": closest_flight.longitude,
                    "altitude": closest_flight.altitude,
                    "heading": closest_flight.heading,
                    "ground_speed": closest_flight.ground_speed,
                    "vertical_speed": closest_flight.vertical_speed
                },
                "aircraft": {
                    "code": closest_flight.aircraft_code,
                    "registration": closest_flight.registration
                },
                "airline": {
                    "icao": closest_flight.airline_icao,
                    "iata": closest_flight.airline_iata
                },
                "route": {
                    "origin_iata": closest_flight.origin_airport_iata,
                    "destination_iata": closest_flight.destination_airport_iata
                }
            }
        }
        
        # add detailed info if available
        if hasattr(closest_flight, 'origin_airport_name'):
            response["flight"]["route"]["origin_name"] = closest_flight.origin_airport_name
        if hasattr(closest_flight, 'destination_airport_name'):
            response["flight"]["route"]["destination_name"] = closest_flight.destination_airport_name
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API documentation."""
    return jsonify({
        "service": "Flight Finder API",
        "version": "1.0",
        "endpoints": {
            "/health": {
                "method": "GET",
                "description": "Health check"
            },
            "/closest-flight": {
                "method": "GET",
                "description": "Find closest flight to coordinates",
                "parameters": {
                    "lat": "Latitude (required, -90 to 90)",
                    "lon": "Longitude (required, -180 to 180)",
                    "radius": "Search radius in km (optional, default 10, max 500)"
                },
                "example": "/closest-flight?lat=37.7749&lon=-122.4194&radius=10"
            }
        }
    }), 200


if __name__ == '__main__':
    # for development, in production use the Procfile instead
    port = int(os.getenv('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=False)

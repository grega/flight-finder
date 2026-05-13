"""
Web service for finding closest flights using FlightRadarAPI
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


def serialize_flight(flight):
    """Convert FlightRadar24 flight object to a serializable dictionary."""
    flight_data = {
        "id": flight.id,
        "number": flight.number,
        "callsign": flight.callsign,
        "icao_24bit": flight.icao_24bit,
        "position": {
            "latitude": flight.latitude,
            "longitude": flight.longitude,
            "altitude": flight.altitude,
            "heading": flight.heading,
            "ground_speed": flight.ground_speed,
            "vertical_speed": flight.vertical_speed
        },
        "aircraft": {
            "code": flight.aircraft_code,
            "registration": flight.registration
        },
        "airline": {
            "icao": flight.airline_icao,
            "iata": flight.airline_iata
        },
        "route": {
            "origin_iata": flight.origin_airport_iata,
            "destination_iata": flight.destination_airport_iata
        }
    }

    # add detailed info if available
    if hasattr(flight, 'aircraft_model'):
        flight_data["aircraft"]["model"] = flight.aircraft_model
    if hasattr(flight, 'origin_airport_name'):
        flight_data["route"]["origin_name"] = flight.origin_airport_name
    if hasattr(flight, 'destination_airport_name'):
        flight_data["route"]["destination_name"] = flight.destination_airport_name

    return flight_data


def parse_and_validate_params():
    """Parse query parameters and validate them."""
    try:
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
        radius_km = float(request.args.get('radius', 10))

        max_altitude_raw = request.args.get('max_altitude')
        max_altitude_ft = float(max_altitude_raw) if max_altitude_raw is not None else None

        if not (-90 <= lat <= 90):
            return None, None, None, None, jsonify({"error": "Latitude must be between -90 and 90"}), 400
        if not (-180 <= lon <= 180):
            return None, None, None, None, jsonify({"error": "Longitude must be between -180 and 180"}), 400
        if not (1 <= radius_km <= 500):
            return None, None, None, None, jsonify({"error": "Radius must be between 1 and 500 km"}), 400
        if max_altitude_ft is not None and max_altitude_ft < 0:
            return None, None, None, None, jsonify({"error": "max_altitude must be non-negative"}), 400

        return lat, lon, radius_km, max_altitude_ft, None, None

    except (TypeError, ValueError):
        return None, None, None, None, jsonify({"error": "Invalid parameters. Required: lat, lon. Optional: radius, max_altitude"}), 400


@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route('/closest-flight', methods=['GET'])
def get_closest_flight():
    """Find the closest flight to given coordinates."""
    if not validate_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    lat, lon, radius_km, max_altitude_ft, error_response, status = parse_and_validate_params()
    if error_response:
        return error_response, status

    try:
        bounds = calculate_bounds(lat, lon, radius_km)
        flights = fr_api.get_flights(bounds=bounds)

        if not flights:
            return jsonify({"found": False, "message": "No flights found in search area"}), 200

        closest_flight = None
        min_distance = float('inf')

        class SearchPoint:
            def __init__(self, lat, lon):
                self.latitude = lat
                self.longitude = lon

        search_point = SearchPoint(lat, lon)

        for flight in flights:
            if flight.on_ground or flight.latitude is None or flight.longitude is None:
                continue
            if max_altitude_ft is not None and (flight.altitude is None or flight.altitude > max_altitude_ft):
                continue
            distance = flight.get_distance_from(search_point)
            if distance < min_distance:
                min_distance = distance
                closest_flight = flight

        if not closest_flight:
            return jsonify({"found": False, "message": "No airborne flights found in search area"}), 200

        try:
            flight_details = fr_api.get_flight_details(closest_flight)
            closest_flight.set_flight_details(flight_details)
        except Exception as e:
            # proceed without detailed flight info if fetching fails (eg. proxy outage)
            app.logger.warning("get_flight_details failed for %s: %s", closest_flight.id, e)

        response = {
            "found": True,
            "distance_km": round(min_distance, 2),
            "flight": serialize_flight(closest_flight)
        }

        return jsonify(response), 200

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route('/flights-in-radius', methods=['GET'])
def get_flights_in_radius():
    """Find all flights within a given radius of coordinates."""
    if not validate_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    lat, lon, radius_km, max_altitude_ft, error_response, status = parse_and_validate_params()
    if error_response:
        return error_response, status

    try:
        bounds = calculate_bounds(lat, lon, radius_km)
        flights = fr_api.get_flights(bounds=bounds)

        if not flights:
            return jsonify({"found": False, "message": "No flights found in search area"}), 200

        response = {"found": True, "flights": []}

        for flight in flights:
            if flight.on_ground or flight.latitude is None or flight.longitude is None:
                continue
            if max_altitude_ft is not None and (flight.altitude is None or flight.altitude > max_altitude_ft):
                continue

            try:
                flight_details = fr_api.get_flight_details(flight)
                flight.set_flight_details(flight_details)
            except Exception as e:
                # proceed without detailed flight info if fetching fails (eg. proxy outage)
                app.logger.warning("get_flight_details failed for %s: %s", flight.id, e)

            response["flights"].append(serialize_flight(flight))

        if not response["flights"]:
            return jsonify({"found": False, "message": "No airborne flights found in search area"}), 200

        return jsonify(response), 200

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API documentation."""
    return jsonify({
        "service": "Flight Finder API",
        "version": "1.0",
        "endpoints": {
            "/health": {"method": "GET", "description": "Health check"},
            "/closest-flight": {
                "method": "GET",
                "description": "Find closest flight to coordinates",
                "parameters": {
                    "lat": "Latitude (required, -90 to 90)",
                    "lon": "Longitude (required, -180 to 180)",
                    "radius": "Search radius in km (optional, default 10, max 500)",
                    "max_altitude": "Altitude ceiling in feet (optional, non-negative). Flights above this altitude are ignored."
                },
                "example": "/closest-flight?lat=37.7749&lon=-122.4194&radius=10&max_altitude=10000"
            },
            "/flights-in-radius": {
                "method": "GET",
                "description": "Find all flights within a given radius of coordinates",
                "parameters": {
                    "lat": "Latitude (required, -90 to 90)",
                    "lon": "Longitude (required, -180 to 180)",
                    "radius": "Search radius in km (optional, default 10, max 500)",
                    "max_altitude": "Altitude ceiling in feet (optional, non-negative). Flights above this altitude are ignored."
                },
                "example": "/flights-in-radius?lat=37.7749&lon=-122.4194&radius=10&max_altitude=10000"
            }
        }
    }), 200


if __name__ == '__main__':
    # for development, in production use the Procfile instead
    port = int(os.getenv('PORT', 7478))
    app.run(host='0.0.0.0', port=port, debug=False)

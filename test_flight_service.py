import pytest
from unittest.mock import MagicMock, patch
from flight_service import app, calculate_bounds, API_KEY

@pytest.fixture(autouse=True)
def disable_api_key(monkeypatch):
    """
    Temporarily override API_KEY to None for all tests.
    """
    monkeypatch.setattr("flight_service.API_KEY", None)

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_calculate_bounds_basic():
    lat, lon, radius = 0, 0, 111
    bounds = calculate_bounds(lat, lon, radius)
    north, south, west, east = map(float, bounds.split(","))
    assert north > south
    assert east > west
    assert round(north - south, 1) == 2.0

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}

def test_index(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.get_json()
    assert "service" in data
    assert "/closest-flight" in data["endpoints"]

def test_api_key_valid(client, monkeypatch):
    monkeypatch.setattr("flight_service.API_KEY", "secret")
    response = client.get(
        "/closest-flight?lat=10&lon=20",
        headers={"X-API-Key": "secret"}
    )
    assert response.status_code in (200, 400, 500)

@pytest.mark.parametrize("query", [
    "lon=10",
    "lat=91&lon=0",
    "lat=0&lon=181",
    "lat=0&lon=0&radius=0",
    "lat=0&lon=0&radius=9999"
])
def test_invalid_parameters(client, query):
    response = client.get(f"/closest-flight?{query}")
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data

class DummyFlight:
    def __init__(self, flight_id="ABC123", lat=10.1, lon=20.1):
        self.id = flight_id
        self.number = "XY123"
        self.callsign = "CALL123"
        self.icao_24bit = "abcd12"
        self.latitude = lat
        self.longitude = lon
        self.altitude = 10000
        self.heading = 250
        self.ground_speed = 750
        self.vertical_speed = 0
        self.aircraft_code = "A320"
        self.registration = "REG123"
        self.airline_icao = "ICAO"
        self.airline_iata = "IATA"
        self.origin_airport_iata = "LHR"
        self.destination_airport_iata = "CDG"
        self.on_ground = False

    def get_distance_from(self, other):
        return 5.0

    def set_flight_details(self, details):
        self.origin_airport_name = details.get("origin")
        self.destination_airport_name = details.get("destination")


@patch("flight_service.fr_api")
def test_closest_flight_found(mock_api, client):
    dummy_flight = DummyFlight()
    mock_api.get_flights.return_value = [dummy_flight]
    mock_api.get_flight_details.return_value = {"origin": "London", "destination": "Paris"}

    response = client.get("/closest-flight?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 200
    assert data["found"] is True
    assert data["flight"]["route"]["origin_name"] == "London"
    assert "distance_km" in data


@patch("flight_service.fr_api")
def test_no_flights_found(mock_api, client):
    mock_api.get_flights.return_value = []
    response = client.get("/closest-flight?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 200
    assert data["found"] is False
    assert "No flights" in data["message"]


@patch("flight_service.fr_api")
def test_no_airborne_flights(mock_api, client):
    grounded = DummyFlight()
    grounded.on_ground = True
    mock_api.get_flights.return_value = [grounded]
    response = client.get("/closest-flight?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 200
    assert data["found"] is False
    assert "airborne" in data["message"]


@patch("flight_service.fr_api")
def test_internal_error_handling(mock_api, client):
    mock_api.get_flights.side_effect = Exception("Simulated failure")
    response = client.get("/closest-flight?lat=10&lon=20")
    assert response.status_code == 500
    assert "Server error" in response.get_json()["error"]

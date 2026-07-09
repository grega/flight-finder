import pytest
from unittest.mock import MagicMock, patch
from flight_service import app, calculate_bounds, API_KEY, serialize_flight, airport_name
import fleet_store
import ota_store

@pytest.fixture(autouse=True)
def disable_api_key(monkeypatch):
    """
    Temporarily override API_KEY to None for all tests.
    """
    monkeypatch.setattr("flight_service.API_KEY", None)

@pytest.fixture(autouse=True)
def isolated_fleet_db(tmp_path, monkeypatch):
    """Point the fleet store at a fresh per-test SQLite file. Check-ins record
    heartbeats/commands/logs, so without this the tests would share (and
    pollute) one database."""
    monkeypatch.setattr("fleet_store.DB_PATH", str(tmp_path / "fleet.db"))
    fleet_store.init_db()

@pytest.fixture(autouse=True)
def isolated_ota_dir(tmp_path, monkeypatch):
    """Point the OTA store at a per-test dir so publishes never write into the repo."""
    monkeypatch.setattr("ota_store.OTA_DIR", str(tmp_path / "ota"))

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
    "lat=0&lon=0&radius=9999",
    "lat=0&lon=0&max_altitude=-1",
    "lat=0&lon=0&max_altitude=abc",
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


@patch("flight_service.fr_api")
def test_closest_flight_max_altitude_filter(mock_api, client):
    """Flights above max_altitude should be skipped when picking the closest."""
    low = DummyFlight(flight_id="LOW")
    low.altitude = 5000
    high = DummyFlight(flight_id="HIGH")
    high.altitude = 35000
    mock_api.get_flights.return_value = [high, low]
    mock_api.get_flight_details.return_value = {"origin": "London", "destination": "Paris"}

    # ceiling above both: either could be returned (same mock distance) - just verify success
    response = client.get("/closest-flight?lat=10&lon=20&max_altitude=40000")
    assert response.status_code == 200
    assert response.get_json()["found"] is True

    # ceiling between the two: only LOW remains as a candidate
    response = client.get("/closest-flight?lat=10&lon=20&max_altitude=10000")
    data = response.get_json()
    assert response.status_code == 200
    assert data["found"] is True
    assert data["flight"]["id"] == "LOW"

    # ceiling below both: no candidates left
    response = client.get("/closest-flight?lat=10&lon=20&max_altitude=1000")
    data = response.get_json()
    assert response.status_code == 200
    assert data["found"] is False


def test_airport_name_lookup():
    """IATA codes resolve to a full name; unknown/empty codes return None."""
    assert "Heathrow" in airport_name("LHR")
    assert airport_name("ZZ9") is None
    assert airport_name(None) is None


def test_serialize_flight_fills_airport_names_from_iata():
    """When FR24 omits airport names, fill them from the offline IATA lookup."""
    flight = DummyFlight()  # has LHR/CDG iata, but no *_airport_name attributes
    data = serialize_flight(flight)
    assert "Heathrow" in data["route"]["origin_name"]
    assert data["route"]["destination_name"]  # CDG resolved to some name


def test_serialize_flight_prefers_fr24_airport_names():
    """FR24's own airport names take precedence over the IATA lookup."""
    flight = DummyFlight()
    flight.set_flight_details({"origin": "Custom Origin", "destination": "Custom Dest"})
    data = serialize_flight(flight)
    assert data["route"]["origin_name"] == "Custom Origin"
    assert data["route"]["destination_name"] == "Custom Dest"


def test_serialize_flight_unknown_iata_omits_name():
    """An unresolvable IATA code leaves the name out rather than guessing."""
    flight = DummyFlight()
    flight.origin_airport_iata = None
    flight.destination_airport_iata = None
    data = serialize_flight(flight)
    assert "origin_name" not in data["route"]
    assert "destination_name" not in data["route"]


@patch("flight_service.fr_api")
def test_closest_flight_max_altitude_skips_unknown(mock_api, client):
    """When max_altitude is set, flights with unknown altitude (None) are skipped."""
    unknown = DummyFlight(flight_id="UNKNOWN")
    unknown.altitude = None
    mock_api.get_flights.return_value = [unknown]

    response = client.get("/closest-flight?lat=10&lon=20&max_altitude=40000")
    data = response.get_json()
    assert response.status_code == 200
    assert data["found"] is False

@patch("flight_service.fr_api")
def test_flights_in_radius_found(mock_api, client):
    """Test that the endpoint returns all flights in the radius."""
    dummy_flight1 = DummyFlight(flight_id="ABC123", lat=10.1, lon=20.1)
    dummy_flight2 = DummyFlight(flight_id="DEF456", lat=10.2, lon=20.2)
    mock_api.get_flights.return_value = [dummy_flight1, dummy_flight2]
    mock_api.get_flight_details.return_value = {"origin": "London", "destination": "Paris"}

    response = client.get("/flights-in-radius?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 200
    assert data["found"] is True
    assert len(data["flights"]) == 2
    assert data["flights"][0]["id"] == "ABC123"
    assert data["flights"][1]["id"] == "DEF456"
    assert data["flights"][0]["route"]["origin_name"] == "London"

@patch("flight_service.fr_api")
def test_flights_in_radius_empty(mock_api, client):
    """Test that the endpoint handles no flights found."""
    mock_api.get_flights.return_value = []

    response = client.get("/flights-in-radius?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 200
    assert data["found"] is False
    assert "No flights" in data["message"]

@patch("flight_service.fr_api")
def test_flights_in_radius_grounded(mock_api, client):
    """Test that the endpoint filters out grounded flights."""
    grounded = DummyFlight()
    grounded.on_ground = True
    mock_api.get_flights.return_value = [grounded]

    response = client.get("/flights-in-radius?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 200
    assert data["found"] is False
    assert "No airborne flights" in data["message"]

@patch("flight_service.fr_api")
def test_flights_in_radius_error(mock_api, client):
    """Test that the endpoint handles internal errors."""
    mock_api.get_flights.side_effect = Exception("Simulated failure")

    response = client.get("/flights-in-radius?lat=10&lon=20")
    data = response.get_json()

    assert response.status_code == 500
    assert "Server error" in data["error"]

@patch("flight_service.fr_api")
def test_flights_in_radius_with_api_key(mock_api, client, monkeypatch):
    """Test that the endpoint respects API key authentication."""
    monkeypatch.setattr("flight_service.API_KEY", "secret")
    dummy_flight = DummyFlight()
    mock_api.get_flights.return_value = [dummy_flight]

    response = client.get(
        "/flights-in-radius?lat=10&lon=20",
        headers={"X-API-Key": "secret"}
    )
    assert response.status_code in (200, 400, 500)

    response = client.get(
        "/flights-in-radius?lat=10&lon=20",
        headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized"

@pytest.mark.parametrize("query", [
    "lon=10",
    "lat=91&lon=0",
    "lat=0&lon=181",
    "lat=0&lon=0&radius=0",
    "lat=0&lon=0&radius=9999",
    "lat=0&lon=0&max_altitude=-1",
    "lat=0&lon=0&max_altitude=abc",
])
def test_flights_in_radius_invalid_parameters(client, query):
    """Test that the endpoint validates parameters."""
    response = client.get(f"/flights-in-radius?{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


@patch("flight_service.fr_api")
def test_flights_in_radius_max_altitude_filter(mock_api, client):
    """Flights above max_altitude should be excluded from the results."""
    low = DummyFlight(flight_id="LOW")
    low.altitude = 5000
    high = DummyFlight(flight_id="HIGH")
    high.altitude = 35000
    mock_api.get_flights.return_value = [low, high]
    mock_api.get_flight_details.return_value = {"origin": "London", "destination": "Paris"}

    response = client.get("/flights-in-radius?lat=10&lon=20&max_altitude=10000")
    data = response.get_json()
    assert response.status_code == 200
    assert data["found"] is True
    assert len(data["flights"]) == 1
    assert data["flights"][0]["id"] == "LOW"


# ---- Fleet store ----------------------------------------------------------

def test_fleet_store_insert_then_upsert():
    """First record() inserts; a second for the same device updates its fields,
    bumps request_count, and preserves first_seen."""
    fleet_store.record("devA", "Greg Test", "1.0.0", "203.0.113.5")
    devices = fleet_store.list_devices()
    assert len(devices) == 1
    first = devices[0]
    assert first["device_id"] == "devA"
    assert first["request_count"] == 1
    assert first["version"] == "1.0.0"

    fleet_store.record("devA", "Greg Test", "1.1.0", "203.0.113.9")
    devices = fleet_store.list_devices()
    assert len(devices) == 1  # still one row - deduped by device_id
    updated = devices[0]
    assert updated["request_count"] == 2
    assert updated["version"] == "1.1.0"       # refreshed
    assert updated["last_ip"] == "203.0.113.9" # refreshed
    assert updated["first_seen"] == first["first_seen"]  # preserved


def test_fleet_store_orders_by_last_seen():
    fleet_store.record("old", "Old", "1.0.0", "10.0.0.1")
    fleet_store.record("new", "New", "1.0.0", "10.0.0.2")
    ids = [d["device_id"] for d in fleet_store.list_devices()]
    assert ids[0] == "new"  # most-recently-seen first


# ---- Device check-in (heartbeat + control block) --------------------------

@patch("flight_service.fr_api")
def test_flight_endpoints_no_longer_record_heartbeat(mock_api, client):
    """Heartbeat recording moved to /device/checkin; flight polls are pure
    flight data now and must not create device rows."""
    mock_api.get_flights.return_value = []
    client.get(
        "/closest-flight?lat=51.5&lon=-0.26&radius=10",
        headers={"X-Device-Id": "abc", "User-Agent": "I75 Matrix Display/2.3.4 Kitchen"},
    )
    assert fleet_store.list_devices() == []


def test_checkin_records_heartbeat_and_returns_control(client):
    """A check-in records the device (from the JSON body, incl. lan_ip) and
    returns a control block."""
    r = client.post("/device/checkin", json={
        "device_id": "abc123def456", "version": "2.3.4",
        "label": "Greg Kitchen", "lan_ip": "192.168.1.42",
    })
    assert r.status_code == 200
    control = r.get_json()
    assert control["update_available"] is False and control["target_version"] is None
    devices = fleet_store.list_devices()
    assert len(devices) == 1
    d = devices[0]
    assert d["device_id"] == "abc123def456"
    assert d["version"] == "2.3.4" and d["label"] == "Greg Kitchen"
    assert d["lan_ip"] == "192.168.1.42"


def test_checkin_falls_back_to_label_or_ip_without_device_id(client):
    r = client.post("/device/checkin", json={"label": "Legacy", "version": "1.0.0"})
    assert r.status_code == 200
    devices = fleet_store.list_devices()
    assert len(devices) == 1 and devices[0]["device_id"] == "Legacy"


def test_command_delivered_then_acked(client, monkeypatch):
    """A queued command is delivered on the next check-in, redelivered until
    acked, then stops once the device acks it."""
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"})
    r = client.post("/fleet/command", headers={"X-Admin-Token": "sekret"},
                    json={"device_id": "dev1", "action": "reboot"})
    cid = r.get_json()["id"]

    ctl = client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"}).get_json()
    assert ctl["command"] == {"id": cid, "action": "reboot"}
    # redelivers while un-acked
    ctl = client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"}).get_json()
    assert ctl["command"]["id"] == cid
    # ack clears it
    client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0", "ack": [cid]})
    ctl = client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"}).get_json()
    assert "command" not in ctl


def test_command_rejects_unknown_action(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    r = client.post("/fleet/command", headers={"X-Admin-Token": "sekret"},
                    json={"device_id": "dev1", "action": "explode"})
    assert r.status_code == 400


def test_command_requires_admin(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    assert client.post("/fleet/command", json={"device_id": "d", "action": "reboot"}).status_code == 401


def test_send_logs_round_trip(client, monkeypatch):
    """Uploaded logs are stored (latest set only) and retrievable via /fleet/logs."""
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    assert client.get("/fleet/logs?device_id=dev1",
                      headers={"X-Admin-Token": "sekret"}).status_code == 404
    client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0", "logs": "BOOT ok\nfetch ok"})
    r = client.get("/fleet/logs?device_id=dev1", headers={"X-Admin-Token": "sekret"})
    assert r.status_code == 200 and "fetch ok" in r.get_data(as_text=True)
    # a newer upload overwrites
    client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0", "logs": "second set"})
    body = client.get("/fleet/logs?device_id=dev1", headers={"X-Admin-Token": "sekret"}).get_data(as_text=True)
    assert "second set" in body and "fetch ok" not in body


def test_checkin_store_failure_returns_503(client, monkeypatch):
    """A store failure must not fake a 200: the device clears its pending acks
    only on success, so a faked 200 would drop acks the server never processed
    and the command would re-execute on redelivery."""
    def boom(*args, **kwargs):
        raise RuntimeError("db locked")
    monkeypatch.setattr("fleet_store.record", boom)
    r = client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"})
    assert r.status_code == 503


def test_checkin_canary_gating_then_promote(client, monkeypatch):
    """A published canary_version advertises only to the canary; a promote
    (fleet_version) then widens it to the rest."""
    monkeypatch.setattr("flight_service.CANARY_DEVICE_ID", "canary01")
    fleet_store.set_meta("canary_version", "1.1.0")
    canary = client.post("/device/checkin", json={"device_id": "canary01", "version": "1.0.0"}).get_json()
    other = client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"}).get_json()
    assert canary["update_available"] is True and canary["target_version"] == "1.1.0"
    assert other["update_available"] is False  # fleet_version unset -> untouched
    fleet_store.set_meta("fleet_version", "1.1.0")  # promote
    other = client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0"}).get_json()
    assert other["update_available"] is True and other["target_version"] == "1.1.0"


# ---- Fleet endpoints + admin auth -----------------------------------------

def test_fleet_json_503_when_token_unset(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", None)
    response = client.get("/fleet.json")
    assert response.status_code == 503


def test_fleet_json_401_with_wrong_token(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    response = client.get("/fleet.json", headers={"X-Admin-Token": "nope"})
    assert response.status_code == 401


def test_fleet_json_200_with_valid_token(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    client.post("/device/checkin", json={"device_id": "dev1", "version": "1.0.0", "label": "Kitchen"})
    response = client.get("/fleet.json", headers={"X-Admin-Token": "sekret"})
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["devices"]) == 1
    assert data["devices"][0]["device_id"] == "dev1"


def test_fleet_json_accepts_token_query_param(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    assert client.get("/fleet.json?token=sekret").status_code == 200


def test_fleet_html_prompts_for_basic_auth(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    response = client.get("/fleet")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_fleet_html_503_when_token_unset(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", None)
    assert client.get("/fleet").status_code == 503


def test_fleet_html_renders_with_valid_token(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sekret")
    fleet_store.record("dev-html", "Living Room", "1.0.0", "203.0.113.7")
    response = client.get("/fleet", headers={"X-Admin-Token": "sekret"})
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "dev-html" in html and "Living Room" in html



# ---- OTA: publish / manifest / file / promote -----------------------------

def test_ota_publish_sets_canary_only(client, monkeypatch):
    """A publish sets canary_version (canary sees it) but not fleet_version."""
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sek")
    monkeypatch.setattr("flight_service.CANARY_DEVICE_ID", "canary01")
    r = client.post("/ota/publish", headers={"X-Admin-Token": "sek"},
                    json={"version": "1.1.0", "files": {"flight_display.py": "print(1)\n"}})
    assert r.status_code == 200
    assert fleet_store.get_meta("canary_version") == "1.1.0"
    assert fleet_store.get_meta("fleet_version") is None
    canary = client.post("/device/checkin", json={"device_id": "canary01", "version": "1.0.0"}).get_json()
    other = client.post("/device/checkin", json={"device_id": "dev2", "version": "1.0.0"}).get_json()
    assert canary["update_available"] and not other["update_available"]


def test_ota_publish_monotonic_and_malformed(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sek")
    H = {"X-Admin-Token": "sek"}
    client.post("/ota/publish", headers=H, json={"version": "1.1.0", "files": {"a.py": "1\n"}})
    assert client.post("/ota/publish", headers=H, json={"version": "1.1.0", "files": {"a.py": "2\n"}}).status_code == 409
    assert client.post("/ota/publish", headers=H, json={"version": "1.0.0", "files": {"a.py": "2\n"}}).status_code == 409
    assert client.post("/ota/publish", headers=H, json={"version": "x.y", "files": {"a.py": "2\n"}}).status_code == 400
    assert client.post("/ota/publish", headers=H, json={"version": "1.2.0", "files": "nope"}).status_code == 400


def test_ota_publish_requires_admin(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sek")
    assert client.post("/ota/publish", json={"version": "1.1.0", "files": {"a.py": "1"}}).status_code == 401


def test_ota_manifest_and_file(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sek")
    assert client.get("/ota/manifest").status_code == 404  # nothing published yet
    client.post("/ota/publish", headers={"X-Admin-Token": "sek"},
                json={"version": "1.1.0", "files": {"flight_display.py": "print(1)\n"}})
    manifest = client.get("/ota/manifest").get_json()
    assert manifest["version"] == "1.1.0" and manifest["files"][0]["name"] == "flight_display.py"
    assert "sha256" in manifest["files"][0] and "crc32" in manifest["files"][0]
    fdl = client.get("/ota/file/flight_display.py")
    assert fdl.status_code == 200 and fdl.data == b"print(1)\n"
    assert client.get("/ota/file/unknown.py").status_code == 404
    assert client.get("/ota/file/config.py").status_code == 404  # not in the manifest


def test_promote_widens_to_fleet(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sek")
    H = {"X-Admin-Token": "sek"}
    assert client.post("/fleet/promote", headers=H).status_code == 400  # nothing published
    client.post("/ota/publish", headers=H, json={"version": "1.1.0", "files": {"a.py": "1\n"}})
    r = client.post("/fleet/promote", headers=H)
    assert r.status_code == 200 and r.get_json()["fleet_version"] == "1.1.0"
    other = client.post("/device/checkin", json={"device_id": "dev2", "version": "1.0.0"}).get_json()
    assert other["update_available"] and other["target_version"] == "1.1.0"


def test_promote_requires_admin(client, monkeypatch):
    monkeypatch.setattr("flight_service.ADMIN_TOKEN", "sek")
    assert client.post("/fleet/promote").status_code == 401


# ---- ota_store unit tests -------------------------------------------------

def test_ota_store_checksums_and_read():
    import hashlib, binascii
    m = ota_store.publish("1.0.0", {"flight_display.py": "print(1)\n"})
    e = m["files"][0]
    assert e["sha256"] == hashlib.sha256(b"print(1)\n").hexdigest()
    assert e["crc32"] == "%08x" % (binascii.crc32(b"print(1)\n") & 0xffffffff)
    assert e["size"] == len(b"print(1)\n")
    assert ota_store.read_file("flight_display.py") == b"print(1)\n"
    assert ota_store.read_file("../secrets.py") is None  # traversal guard


def test_ota_store_publish_error_status():
    ota_store.publish("2.0.0", {"a.py": "1\n"})
    with pytest.raises(ota_store.PublishError) as exc:
        ota_store.publish("2.0.0", {"a.py": "2\n"})
    assert exc.value.status == 409
    with pytest.raises(ota_store.PublishError) as exc:
        ota_store.publish("2.0.1", {"bad name.py": "1\n"})
    assert exc.value.status == 400

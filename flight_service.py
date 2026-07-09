"""
Web service for finding closest flights using FlightRadarAPI
"""

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request
from FlightRadarAPI import FlightRadar24API
from math import radians, cos
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote
import airportsdata
import hmac
import os

import fleet_store
import ota_store

load_dotenv()

app = Flask(__name__)
fr_api = FlightRadar24API()

API_KEY = os.getenv("SERVICE_API_KEY", None)
# Generic admin token guarding the fleet view (and any future admin endpoints).
# Deliberately separate from SERVICE_API_KEY so it can be rotated independently.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", None)
# The one device that receives a newly-published build first (canary). A publish
# reaches only this device; a manual promote then widens it to the whole fleet.
CANARY_DEVICE_ID = os.getenv("CANARY_DEVICE_ID", None)

fleet_store.init_db()

# Offline IATA -> airport metadata, loaded once at startup. Used to fill in airport names when FlightRadar24 returns only the IATA code (its free endpoint intermittently omits the full route detail)
_AIRPORTS = airportsdata.load("IATA")


def airport_name(iata):
    """Full airport name for an IATA code, or None if unknown."""
    record = _AIRPORTS.get(iata) if iata else None
    return record["name"] if record else None


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
    """Validate the X-API-Key header.

    Accepts either the grandfathered shared SERVICE_API_KEY env var (kept valid
    while devices migrate to per-client keys) or any enabled key in the api_keys
    table. When the server is unconfigured - no env key AND no table keys - the
    flight endpoints stay open, preserving the original dev/unconfigured behaviour."""
    provided = request.headers.get('X-API-Key')
    if API_KEY is not None and hmac.compare_digest(provided or "", API_KEY):
        return True
    row = fleet_store.get_api_key(provided)
    if row is not None and row["enabled"]:
        g.matched_api_key = provided  # stash for attribution (see _attribute_key_use)
        return True
    if API_KEY is None and not fleet_store.has_api_keys():
        return True
    return False


# Bound how often a flight poll rewrites a key's last-used row (per key).
_KEY_USE_THROTTLE_S = 60


def _attribute_key_use():
    """Record which client just used its per-client key, on the flight endpoints
    (throttled) - so keys used by non-checking-in clients (e.g. a monitor worker)
    still populate 'last used'/'last device'. Identity: the X-Device-Id an I75
    sends, else the User-Agent. No-op for the env key / unconfigured access."""
    key = g.get("matched_api_key")
    if not key:
        return
    identity = request.headers.get("X-Device-Id") or request.headers.get("User-Agent")
    fleet_store.touch_api_key(key, identity, min_interval_s=_KEY_USE_THROTTLE_S)


def control_for(device_id, version):
    """The device-management control block returned from a check-in: the OTA
    target version (canary-aware) and the next queued command, if any.

    A publish sets `canary_version` (only the canary device targets it); a
    promote copies it into `fleet_version` (what every other device targets).
    So an un-promoted build can never advertise to a non-canary device."""
    is_canary = CANARY_DEVICE_ID is not None and device_id == CANARY_DEVICE_ID
    target = fleet_store.get_meta("canary_version") if is_canary else fleet_store.get_meta("fleet_version")
    control = {
        "target_version": target,
        "update_available": bool(target) and version != target,
        "manifest_url": "/ota/manifest",
    }
    command = fleet_store.pending_command(device_id)
    if command is not None:
        control["command"] = command
    return control


def require_admin():
    """Gate the fleet endpoints. Accepts the admin token via X-Admin-Token,
    Authorization: Bearer, HTTP Basic Auth password, or ?token=. Deny-by-default
    when unset - the fleet view exposes device IPs, so unlike the flight
    endpoints it must not silently open."""
    if ADMIN_TOKEN is None:
        return False
    provided = (
        request.headers.get('X-Admin-Token')
        or request.args.get('token')
    )
    if not provided:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            provided = auth_header[len('Bearer '):]
        elif request.authorization and request.authorization.password:
            provided = request.authorization.password  # HTTP Basic (browser prompt)
    return provided == ADMIN_TOKEN


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

    # Prefer FlightRadar24's airport names, but fall back to the offline IATA
    # lookup when they're absent so partial FR24 responses still carry names.
    origin_name = getattr(flight, 'origin_airport_name', None) or airport_name(flight.origin_airport_iata)
    if origin_name:
        flight_data["route"]["origin_name"] = origin_name
    destination_name = getattr(flight, 'destination_airport_name', None) or airport_name(flight.destination_airport_iata)
    if destination_name:
        flight_data["route"]["destination_name"] = destination_name

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
    _attribute_key_use()

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
    _attribute_key_use()

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


@app.route('/device/checkin', methods=['POST'])
def device_checkin():
    """Device-management check-in, separate from the flight endpoints so the
    flight contract stays purely about flights. Records the heartbeat, stashes
    any uploaded logs, processes command acks, and returns the control block.
    The device calls this on its own ~5 min cadence."""
    if not validate_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    # access_route[0] is the client from X-Forwarded-For (behind Dokku's nginx
    # remote_addr is just the proxy) - the household's public egress IP. The
    # device also reports its own lan_ip in the body.
    ip = request.access_route[0] if request.access_route else request.remote_addr
    device_id = body.get("device_id") or body.get("label") or ip
    version = body.get("version")

    try:
        fleet_store.record(device_id, body.get("label"), version, ip,
                           body.get("lan_ip"), body.get("ota_failed"))
        # Attribute the key to this device (no-op for the grandfathered env key).
        # Done here, on the ~5 min check-in, rather than on every flight poll.
        fleet_store.touch_api_key(request.headers.get('X-API-Key'), device_id)
        logs = body.get("logs")
        if logs is not None:
            fleet_store.store_logs(device_id, logs)
        ack = body.get("ack") or []
        if ack:
            fleet_store.ack_commands(device_id, ack)
        control = control_for(device_id, version)
    except Exception as e:
        app.logger.warning("checkin store failed: %s", e)
        # 503, not a faked 200: the device only clears its pending acks/logs on
        # a 200, so pretending success here would drop acks the server never
        # processed - and the un-acked command would then re-execute (eg. a
        # second reboot) when redelivered. A 503 makes the device retry intact.
        return jsonify({"error": "temporarily unavailable"}), 503
    return jsonify(control), 200


def _relative_age(last_seen_iso):
    """(seconds_ago, human_string) for an ISO timestamp, or (None, 'never')."""
    if not last_seen_iso:
        return None, "never"
    try:
        seen = datetime.fromisoformat(last_seen_iso)
        secs = int((datetime.now(timezone.utc) - seen).total_seconds())
    except (ValueError, TypeError):
        return None, "unknown"
    if secs < 0:
        secs = 0
    if secs < 60:
        return secs, f"{secs}s ago"
    if secs < 3600:
        return secs, f"{secs // 60}m ago"
    if secs < 86400:
        return secs, f"{secs // 3600}h {secs % 3600 // 60}m ago"
    return secs, f"{secs // 86400}d ago"


# A device is flagged offline once it misses ~3 check-ins (the device checks in
# every 300s, plus display-cycle rounding), so a healthy device never flaps.
_OFFLINE_AFTER_S = 900


def _ota_banner(canary_version, fleet_version):
    """The OTA rollout banner: published (canary) vs fleet version, with a
    Promote button when there's a canary-tested build not yet on the fleet."""
    if not canary_version and not fleet_version:
        return ("<div class=ota>No firmware published yet. "
                "Use <b>publish.py</b> to publish a build to the canary.</div>")
    fleet_txt = escape(fleet_version) if fleet_version else "&mdash;"
    banner = (f"<div class=ota>Published to canary: <b>{escape(canary_version or '&mdash;')}</b>"
              f" &middot; Fleet: <b>{fleet_txt}</b>")
    # canary_version >= fleet_version always (publish enforces monotonic), so a
    # simple inequality means there's a newer, canary-tested build to roll out.
    if canary_version and canary_version != fleet_version:
        banner += (f' <button id=promote-btn data-version="{escape(canary_version)}">'
                   f'Promote {escape(canary_version)} to fleet</button>')
    return banner + "</div>"


def _render_keys_html(api_keys):
    """The 'API keys' panel: a create form plus a table of issued keys with
    enable/disable/delete actions. Keys are stored plaintext, so the full key is
    shown (copyable) in the table."""
    api_keys = api_keys or []
    if api_keys:
        rows = []
        for k in api_keys:
            key_attr = escape(str(k.get("key") or ""))
            label = str(k.get("label") or "")
            label_attr = escape(label)
            enabled = bool(k.get("enabled"))
            status = ('<span class=online>● enabled</span>' if enabled
                      else '<span class=offline>○ disabled</span>')
            _, created = _relative_age(k.get("created_at"))
            _, used = _relative_age(k.get("last_seen_at"))
            last_dev = str(k.get("last_device_id") or "")
            toggle = (
                f'<button class=act data-act="key-disable" data-key="{key_attr}" data-label="{label_attr}">Disable</button>'
                if enabled else
                f'<button class=act data-act="key-enable" data-key="{key_attr}" data-label="{label_attr}">Enable</button>'
            )
            actions = toggle + (
                f'<button class=act data-act="key-delete" data-key="{key_attr}" data-label="{label_attr}">Delete</button>'
            )
            rows.append(
                "<tr>"
                f"<td>{escape(label)}</td>"
                f"<td class=mono>{escape(str(k.get('key') or ''))}</td>"
                f"<td>{escape(created)}</td>"
                f"<td>{escape(used)}</td>"
                f"<td class=mono>{escape(last_dev)}</td>"
                f"<td>{status}</td>"
                f"<td class=actions>{actions}</td>"
                "</tr>"
            )
        body = "".join(rows)
    else:
        body = ("<tr><td colspan=7 class=empty>No API keys yet - create one to give "
                "a client its own key.</td></tr>")
    return (
        '<section class=keys><h2>API keys</h2>'
        '<form id=key-form autocomplete=off>'
        '<input id=key-label placeholder="Client label (e.g. Living room)" maxlength=64>'
        '<button id=key-create type=submit>Create key</button>'
        '</form>'
        '<div class=scroll><table><thead><tr>'
        '<th>Label</th><th>Key</th><th>Created</th><th>Last used</th>'
        '<th>Last device</th><th>Status</th><th>Actions</th>'
        '</tr></thead><tbody>' + body + '</tbody></table></div></section>'
    )


def _render_fleet_html(devices, canary_id=None, logs_index=None,
                       canary_version=None, fleet_version=None, api_keys=None):
    """Self-contained HTML fleet page (no external assets), auto-refreshing, with
    an API-keys panel and per-device management actions. All state-changing
    buttons confirm first (accidental-click guard) and POST to /keys*,
    /fleet/command, or /fleet/promote."""
    logs_index = logs_index or {}
    # device_id -> the label of the key it last authenticated with (attribution).
    key_by_device = {k.get("last_device_id"): k.get("label")
                     for k in (api_keys or []) if k.get("last_device_id")}
    if devices:
        rows = []
        for d in devices:
            secs, human = _relative_age(d.get("last_seen"))
            offline = secs is None or secs > _OFFLINE_AFTER_S
            seen_cell = (f'<span class="{"offline" if offline else "online"}">'
                         f'{"●" if not offline else "○"} {escape(human)}</span>')
            did = str(d.get("device_id") or "")
            did_attr = escape(did)
            label = str(d.get("label") or "")
            label_attr = escape(label)
            is_canary = bool(canary_id) and did == canary_id
            badge = " <span class=badge>canary</span>" if is_canary else ""
            ota_failed = d.get("ota_failed")
            warn = (f' <span class=warn title="auto-rolled-back from a failed update">'
                    f'⚠ {escape(str(ota_failed))}</span>') if ota_failed else ""
            # Flag a device whose running version differs from its own target
            # (canary devices track canary_version; the rest track fleet_version).
            dver = str(d.get("version") or "?")
            target = canary_version if is_canary else fleet_version
            drift = (f' <span class=warn title="target">&rarr; {escape(str(target))}</span>'
                     ) if target and dver != "?" and dver != target else ""
            lan = d.get("lan_ip")
            lan_cell = (f'<a href="http://{escape(str(lan))}/" target=_blank rel=noopener>'
                        f'{escape(str(lan))}</a>') if lan else ""
            logs_at = logs_index.get(did)
            logs_link = (f'<a class=logs-link href="/fleet/logs?device_id={quote(did)}" '
                         f'target=_blank rel=noopener title="received {escape(str(logs_at))}">logs</a>'
                         ) if logs_at else ""
            actions = (
                f'<button class=act data-act="reboot" data-id="{did_attr}" data-label="{label_attr}">Reboot</button>'
                f'<button class=act data-act="enter-setup" data-id="{did_attr}" data-label="{label_attr}">Setup</button>'
                f'<button class=act data-act="send-logs" data-id="{did_attr}" data-label="{label_attr}">Req&nbsp;logs</button>'
                f"{logs_link}"
            )
            rows.append(
                "<tr>"
                f"<td class=mono>{escape(did)}{badge}{warn}</td>"
                f"<td>{escape(label)}</td>"
                f"<td>{escape(str(key_by_device.get(did) or ''))}</td>"
                f"<td class=mono>{escape(dver)}{drift}</td>"
                f"<td>{seen_cell}</td>"
                f"<td class=mono>{escape(str(d.get('last_ip') or ''))}</td>"
                f"<td class=mono>{lan_cell}</td>"
                f"<td class=num>{escape(str(d.get('request_count') or 0))}</td>"
                f"<td class=actions>{actions}</td>"
                "</tr>"
            )
        body = "".join(rows)
    else:
        body = "<tr><td colspan=9 class=empty>No devices have checked in yet.</td></tr>"

    return (
        "<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta http-equiv=refresh content=30>"
        "<title>Flight Finder fleet</title><style>"
        "body{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1c1e21}"
        "main{max-width:72rem;margin:0 auto;padding:1.2rem}"
        "h1{font-size:1.3rem;margin:0 0 0.2rem}"
        ".sub{color:#666;font-size:0.85rem;margin:0 0 1rem}"
        "#msg{min-height:1.3em;margin:0 0 0.8rem;font-size:0.85rem}"
        "#msg.ok{color:#157347}#msg.err{color:#c62828}"
        ".scroll{overflow-x:auto}"
        "table{width:100%;border-collapse:collapse;background:#fff;"
        "border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)}"
        "th,td{text-align:left;padding:0.5rem 0.7rem;border-bottom:1px solid #eceef1;font-size:0.875rem;white-space:nowrap}"
        "th{background:#fafbfc;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;color:#666}"
        ".mono{font-family:ui-monospace,Menlo,monospace;font-size:0.8rem}"
        ".num{text-align:right}"
        ".online{color:#157347;font-weight:600}.offline{color:#c62828;font-weight:600}"
        ".empty{text-align:center;color:#888;padding:1.5rem}"
        ".badge{background:#1667c1;color:#fff;border-radius:4px;padding:0.05rem 0.35rem;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.03em}"
        ".warn{color:#b45309;font-size:0.72rem}"
        ".actions{white-space:nowrap}"
        "button.act{margin-right:0.3rem;padding:0.25rem 0.5rem;font-size:0.75rem;border:1px solid #8a919c;"
        "border-radius:5px;background:#fff;cursor:pointer}"
        "button.act:disabled{opacity:0.5;cursor:default}"
        "td.actions a{font-size:0.75rem}"
        ".ota{background:#fff;border-radius:8px;padding:0.6rem 0.9rem;margin:0 0 1rem;font-size:0.85rem;"
        "box-shadow:0 1px 3px rgba(0,0,0,0.1)}"
        ".ota b{font-family:ui-monospace,Menlo,monospace}"
        "#promote-btn{margin-left:0.6rem;padding:0.3rem 0.7rem;font-size:0.8rem;border:1px solid #1667c1;"
        "border-radius:5px;background:#1667c1;color:#fff;cursor:pointer}"
        "#promote-btn:disabled{opacity:0.5;cursor:default}"
        "h2{font-size:1rem;margin:1.3rem 0 0.5rem}"
        "#key-form{display:flex;gap:0.5rem;margin:0 0 0.7rem;flex-wrap:wrap}"
        "#key-label{flex:1;min-width:12rem;max-width:24rem;padding:0.35rem 0.5rem;"
        "border:1px solid #8a919c;border-radius:5px;font-size:0.85rem}"
        "#key-create{padding:0.35rem 0.8rem;font-size:0.8rem;border:1px solid #1667c1;"
        "border-radius:5px;background:#1667c1;color:#fff;cursor:pointer}"
        "#key-create:disabled{opacity:0.5;cursor:default}"
        "</style></head><body><main>"
        "<h1>Flight Finder fleet</h1>"
        f"<p class=sub>{len(devices)} device(s) &middot; auto-refreshes every 30s</p>"
        + _ota_banner(canary_version, fleet_version) +
        "<div id=msg></div>"
        + _render_keys_html(api_keys) +
        "<h2>Devices</h2>"
        "<div class=scroll><table><thead><tr>"
        "<th>Device ID</th><th>Label</th><th>Client</th><th>Version</th><th>Last seen</th>"
        "<th>Household IP</th><th>LAN IP</th><th>Requests</th><th>Actions</th>"
        "</tr></thead><tbody>" + body + "</tbody></table></div></main>"
        "<script>" + _FLEET_JS + "</script></body></html>"
    )


# Fleet-page client script: confirms every action, then POSTs to /fleet/command.
# Kept as a plain string (braces) separate from the f-string page assembly.
_FLEET_JS = r"""
(function(){
  var token = new URLSearchParams(location.search).get('token');
  // When authed via ?token= (not Basic Auth), propagate it to fetches + log links.
  if(token){
    document.querySelectorAll('a.logs-link').forEach(function(a){
      a.href += (a.href.indexOf('?')>=0?'&':'?') + 'token=' + encodeURIComponent(token);
    });
  }
  var msg = document.getElementById('msg');
  function flash(t, ok){ msg.textContent = t; msg.className = ok ? 'ok' : 'err'; }
  function headers(){
    var h = {'Content-Type': 'application/json'};
    if(token){ h['X-Admin-Token'] = token; }
    return h;
  }
  function post(url, body){
    return fetch(url, {method:'POST', headers:headers(), body: JSON.stringify(body || {})})
      .then(function(r){ return r.json().then(function(d){
        if(!r.ok){ throw new Error(d.error || ('HTTP ' + r.status)); } return d; }); });
  }

  // One delegated click handler for both tables (device commands + key actions).
  document.addEventListener('click', function(ev){
    var btn = ev.target.closest('button[data-act]');
    if(!btn) return;
    var act = btn.getAttribute('data-act');
    if(act.indexOf('key-') === 0){ handleKey(act, btn); } else { handleDevice(act, btn); }
  });

  function handleDevice(act, btn){
    var id = btn.getAttribute('data-id');
    var label = btn.getAttribute('data-label') || id;
    var ok;
    if(act === 'reboot'){
      ok = confirm("Reboot '" + label + "' (" + id + ")?");
    } else if(act === 'enter-setup'){
      var typed = prompt("This takes '" + label + "' OFF the network into WiFi setup mode " +
        "(someone may need physical access to recover it).\n\nType the device label to confirm:");
      if(typed === null){ return; }
      ok = (typed === label);
      if(!ok){ flash("Label didn't match - cancelled.", false); return; }
    } else if(act === 'send-logs'){
      ok = confirm("Request the latest logs from '" + label + "'? They'll appear here within a few minutes.");
    } else {
      ok = confirm("Send '" + act + "' to '" + label + "'?");
    }
    if(!ok){ return; }
    btn.disabled = true;
    post('/fleet/command', {device_id:id, action:act})
      .then(function(){ flash("Queued '" + act + "' for " + label +
        " - applies on the device's next check-in.", true); })
      .catch(function(e){ flash('Failed: ' + e.message, false); })
      .finally(function(){ btn.disabled = false; });
  }

  function handleKey(act, btn){
    var key = btn.getAttribute('data-key');
    var label = btn.getAttribute('data-label') || key;
    var url, ok, verb;
    if(act === 'key-delete'){
      ok = confirm("Delete key '" + label + "'? A device still using it stops working on its next request.");
      url = '/keys/delete'; verb = 'Deleted';
    } else if(act === 'key-disable'){
      ok = confirm("Disable key '" + label + "'? It's rejected on the next request (re-enable anytime).");
      url = '/keys/disable'; verb = 'Disabled';
    } else { // key-enable
      ok = true; url = '/keys/enable'; verb = 'Enabled';
    }
    if(!ok){ return; }
    btn.disabled = true;
    post(url, {key:key})
      .then(function(){ flash(verb + " key '" + label + "'.", true);
        setTimeout(function(){ location.reload(); }, 700); })
      .catch(function(e){ btn.disabled = false; flash('Failed: ' + e.message, false); });
  }

  var keyForm = document.getElementById('key-form');
  if(keyForm){
    keyForm.addEventListener('submit', function(ev){
      ev.preventDefault();
      var input = document.getElementById('key-label');
      var label = (input.value || '').trim();
      if(!label){ flash('Enter a label for the key.', false); return; }
      var btn = document.getElementById('key-create');
      btn.disabled = true;
      post('/keys', {label:label})
        .then(function(d){ flash("Created key for '" + label + "': " + d.key +
          " (also shown in the table below)", true);
          input.value = ''; setTimeout(function(){ location.reload(); }, 2500); })
        .catch(function(e){ flash('Failed: ' + e.message, false); })
        .finally(function(){ btn.disabled = false; });
    });
  }

  var promote = document.getElementById('promote-btn');
  if(promote){
    promote.addEventListener('click', function(){
      var version = promote.getAttribute('data-version');
      if(!confirm("Promote " + version + " to the WHOLE fleet? Every non-canary device " +
        "updates on its next check-in. Confirm you've verified it on the canary first.")){ return; }
      promote.disabled = true;
      post('/fleet/promote', {})
        .then(function(){ flash("Promoted " + version + " to the fleet - devices converge on their next check-in.", true); })
        .catch(function(e){ promote.disabled = false; flash('Promote failed: ' + e.message, false); });
    });
  }
})();
"""


@app.route('/fleet.json', methods=['GET'])
def fleet_json():
    """Fleet data as JSON (admin-only)."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"devices": fleet_store.list_devices(),
                    "api_keys": fleet_store.list_api_keys()}), 200


@app.route('/fleet', methods=['GET'])
def fleet_view():
    """Human-facing fleet table (admin-only). Prompts for HTTP Basic Auth in a
    browser when unauthorized - enter the admin token as the password."""
    if ADMIN_TOKEN is None:
        return Response("ADMIN_TOKEN is not configured on the server", status=503)
    if not require_admin():
        return Response(
            "Authentication required", status=401,
            headers={"WWW-Authenticate": 'Basic realm="Flight fleet"'},
        )
    html = _render_fleet_html(
        fleet_store.list_devices(), canary_id=CANARY_DEVICE_ID,
        logs_index=fleet_store.logs_index(),
        canary_version=fleet_store.get_meta("canary_version"),
        fleet_version=fleet_store.get_meta("fleet_version"),
        api_keys=fleet_store.list_api_keys(),
    )
    return Response(html, mimetype="text/html")


# One-shot commands the fleet page / admins may queue for a device.
_FLEET_COMMANDS = {"reboot", "enter-setup", "clear-crash", "send-logs"}


@app.route('/fleet/command', methods=['POST'])
def fleet_command():
    """Queue a one-shot command for a device (admin-only). Delivered on the
    device's next check-in and acked on the one after."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    device_id = body.get("device_id")
    action = body.get("action")
    if not device_id or action not in _FLEET_COMMANDS:
        return jsonify({"error": "device_id and a valid action are required"}), 400
    command_id = fleet_store.enqueue_command(device_id, action)
    return jsonify({"ok": True, "id": command_id}), 200


@app.route('/keys', methods=['POST'])
def keys_create():
    """Create a new per-client API key (admin-only). Returns the plaintext key
    once - it's also visible afterwards on the fleet page (stored plaintext)."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label is required"}), 400
    key = fleet_store.create_api_key(label)
    return jsonify({"ok": True, "key": key, "label": label}), 200


@app.route('/keys/enable', methods=['POST'])
@app.route('/keys/disable', methods=['POST'])
def keys_set_enabled():
    """Enable or disable an API key (admin-only). Disabling revokes it on the
    client's next request; the key stays listed so it can be re-enabled."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    key = body.get("key")
    if not key:
        return jsonify({"error": "key is required"}), 400
    enabled = request.path.endswith("/enable")
    if not fleet_store.set_api_key_enabled(key, enabled):
        return jsonify({"error": "unknown key"}), 404
    return jsonify({"ok": True, "enabled": enabled}), 200


@app.route('/keys/delete', methods=['POST'])
def keys_delete():
    """Delete an API key entirely (admin-only). A device still holding it drops
    to Unauthorized on its next request."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    key = body.get("key")
    if not key:
        return jsonify({"error": "key is required"}), 400
    if not fleet_store.delete_api_key(key):
        return jsonify({"error": "unknown key"}), 404
    return jsonify({"ok": True}), 200


@app.route('/fleet/promote', methods=['POST'])
def fleet_promote():
    """Roll the currently-published (canary-tested) build out to the whole fleet:
    fleet_version = canary_version. Admin-only; the fleet page confirms first."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    canary = fleet_store.get_meta("canary_version")
    if not canary:
        return jsonify({"error": "nothing published to promote"}), 400
    fleet_store.set_meta("fleet_version", canary)
    return jsonify({"ok": True, "fleet_version": canary}), 200


@app.route('/fleet/logs', methods=['GET'])
def fleet_logs():
    """The latest uploaded log set for a device (admin-only). Requested via the
    send-logs command; the device uploads on its next check-in."""
    if ADMIN_TOKEN is None:
        return Response("ADMIN_TOKEN is not configured on the server", status=503)
    if not require_admin():
        return Response(
            "Authentication required", status=401,
            headers={"WWW-Authenticate": 'Basic realm="Flight fleet"'},
        )
    device_id = request.args.get("device_id", "")
    entry = fleet_store.get_logs(device_id)
    if entry is None:
        return Response("No logs captured for this device yet.", status=404)
    header = f"# logs for {device_id} - received {entry['received_at']}\n\n"
    return Response(header + (entry["logs"] or ""), mimetype="text/plain; charset=utf-8")


@app.route('/ota/manifest', methods=['GET'])
def ota_manifest():
    """The current published firmware manifest (version + per-file checksums).
    API-key gated like the flight endpoints; the device already sends X-API-Key."""
    if not validate_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    manifest = ota_store.get_manifest()
    if manifest is None:
        return jsonify({"error": "nothing published"}), 404
    return jsonify(manifest), 200


@app.route('/ota/file/<name>', methods=['GET'])
def ota_file(name):
    """Download one published module. `name` is validated against the manifest's
    file list inside ota_store, so it can't escape OTA_DIR."""
    if not validate_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    data = ota_store.read_file(name)
    if data is None:
        return Response("Unknown file", status=404)
    return Response(data, mimetype="text/x-python")


@app.route('/ota/publish', methods=['POST'])
def ota_publish():
    """Publish a new firmware set (admin-only, from publish.py). Reaches the
    canary only: sets canary_version, leaving fleet_version until a promote."""
    if ADMIN_TOKEN is None:
        return jsonify({"error": "ADMIN_TOKEN is not configured on the server"}), 503
    if not require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    version = body.get("version")
    files = body.get("files")
    if not isinstance(files, dict):
        return jsonify({"error": "body needs {version, files: {name: content}}"}), 400
    try:
        manifest = ota_store.publish(version, files)
    except ota_store.PublishError as e:
        return jsonify({"error": str(e)}), e.status
    fleet_store.set_meta("canary_version", version)
    return jsonify({"ok": True, "manifest": manifest, "canary_version": version}), 200


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

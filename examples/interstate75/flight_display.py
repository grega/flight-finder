import builtins
import gc
import json
import machine
import network
import ntptime
import time
import urequests
from interstate75 import Interstate75

# config lives in config.py - edit it there to customise behaviour
from config import *

import webserver
import dashboard

i75 = Interstate75(display=DISPLAY_TYPE, color_order=COLOR_ORDER)
display = i75.display

WIDTH  = i75.width
HEIGHT = i75.height

BLACK   = display.create_pen(0, 0, 0)
WHITE   = display.create_pen(*((255, 255, 255) if BRIGHT_MODE else (200, 200, 200)))
BLUE    = display.create_pen(*((64, 64, 255) if BRIGHT_MODE else (32, 32, 128)))
RED     = display.create_pen(*((255, 64, 64) if BRIGHT_MODE else (128, 32, 32)))
GREEN   = display.create_pen(*((64, 255, 64) if BRIGHT_MODE else (32, 128, 32)))
CYAN    = display.create_pen(*((0, 255, 255) if BRIGHT_MODE else (0, 128, 128)))
MAGENTA = display.create_pen(*((255, 0, 255) if BRIGHT_MODE else (128, 0, 128)))
YELLOW  = display.create_pen(*((255, 255, 0) if BRIGHT_MODE else (128, 128, 0)))
ORANGE  = display.create_pen(*((255, 128, 0) if BRIGHT_MODE else (128, 64, 0)))

# font
display.set_font("bitmap8")

# px between adjacent segments on a multi-segment scrolling line (eg. between the flight number and the distance/altitude value on line 2)
SEGMENT_GAP = 4

_LOG_BUFFER_BYTES = 4096

class _LogBuffer:
    """Fixed-size in-memory ring buffer for /logs. """
    def __init__(self, size):
        self.buf = bytearray()
        self.size = size

    def write(self, data):
        self.buf.extend(data)
        overflow = len(self.buf) - self.size
        if overflow > 0:
            del self.buf[:overflow]

_log_buffer = _LogBuffer(_LOG_BUFFER_BYTES)

def _install_log_capture():
    """Replace builtins.print so every print() also lands in _log_buffer. """
    original_print = builtins.print
    def _captured_print(*args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        try:
            _log_buffer.write((sep.join(str(a) for a in args) + end).encode())
        except Exception:
            pass # never let log capture break a print
        return original_print(*args, **kwargs)
    builtins.print = _captured_print

# Installed at import (not in main()) so the config validation warnings below reach /logs
_install_log_capture()

def _coerce_refresh_interval(raw_value):
    """Return a safe refresh interval in seconds (minimum 30)."""
    try:
        value = int(raw_value)
    except Exception:
        print(f"Invalid REFRESH_INTERVAL {raw_value!r}; falling back to 60s")
        return 60
    if value < 30:
        print(f"REFRESH_INTERVAL {value}s is below minimum 30s; clamping to 30s")
        return 30
    return value

# Enforce minimum value
REFRESH_INTERVAL = _coerce_refresh_interval(REFRESH_INTERVAL)

def clear_display():
    """Clear the display / turn it off"""
    display.set_pen(BLACK)
    display.clear()
    i75.update()

# Set by /reboot; the main loop reboots after the response has flushed so the client sees a clean 200 rather than a dropped connection
_reboot_requested = False

# Restrict /upload targets to safe Python module filenames: alphanumeric + underscores, .py extension only
def _is_safe_upload_target(name):
    if not name or not name.endswith(".py"):
        return False
    stem = name[:-3]
    if not stem:
        return False
    for ch in stem:
        if not (("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch == "_"):
            return False
    return True

# Tick value captured at boot for uptime reporting via /status
_boot_ticks_ms = time.ticks_ms()

# State tracked for /status. Updated by fetch_flight_data and display_flight_data
_last_fetch_ticks_ms = None
_last_fetch_ok = None
_last_fetch_error = None
_current_flight_summary = None

# Recently-seen flights for /history (newest last)
_FLIGHT_HISTORY_MAX = 15
_flight_history = []

def _handle_get_config(body, query):
    try:
        with open("config.py", "rb") as f:
            return (200, "text/x-python", f.read())
    except OSError as e:
        return (500, "text/plain", f"Cannot read config.py: {e}")

def _handle_config_editor(body, query):
    # Imported lazily so its page string only costs heap once the editor is used (and a reboot after saving clears it again)
    try:
        import config_editor
    except ImportError:
        return (500, "text/plain", "config_editor.py is not on the device")
    return (200, "text/html; charset=utf-8", config_editor.render())

def _handle_wifi_page(body, query):
    # /wifi handlers lazy-import wifi_setup for the same heap reason as above
    import wifi_setup
    return wifi_setup.handle_page(body, query)

def _handle_wifi_scan(body, query):
    import wifi_setup
    return wifi_setup.handle_scan(body, query)

def _handle_wifi_status(body, query):
    import wifi_setup
    return wifi_setup.handle_status(body, query)

def _handle_wifi_save(body, query):
    # In normal mode a successful save also reboots so the new creds take effect
    # (in setup mode wifi_setup's own reboot countdown handles it instead)
    global _reboot_requested
    import wifi_setup
    result = wifi_setup.handle_save(body, query)
    if result[0] == 200 and not wifi_setup.in_setup_mode():
        _reboot_requested = True
    return result

def _handle_upload(body, query):
    # query is the raw query string, eg. "path=main.py"
    target = None
    for pair in query.split("&"):
        if pair.startswith("path="):
            target = pair[len("path="):]
            break
    if not _is_safe_upload_target(target):
        return (400, "text/plain",
                f"path must be a simple .py filename (eg. main.py, config.py, dashboard.py), got {target!r}")
    try:
        with open(target, "wb") as f:
            f.write(body)
    except OSError as e:
        return (500, "text/plain", f"Write failed: {e}")
    return (200, "text/plain", f"Wrote {len(body)} bytes to {target}")

def _handle_reboot(body, query):
    global _reboot_requested
    _reboot_requested = True
    return (200, "text/plain", "Rebooting")

def _collect_status():
    """Snapshot the current device + display state. Shared by /status (JSON)
    and dashboard.render_status_html (HTML)."""
    wlan = network.WLAN(network.STA_IF)
    connected = wlan.isconnected()
    uptime_s = time.ticks_diff(time.ticks_ms(), _boot_ticks_ms) // 1000
    last_fetch_age_s = None
    if _last_fetch_ticks_ms is not None:
        last_fetch_age_s = time.ticks_diff(time.ticks_ms(), _last_fetch_ticks_ms) // 1000
    return {
        "uptime_s": uptime_s,
        "free_heap_bytes": gc.mem_free(),
        "alloc_heap_bytes": gc.mem_alloc(),
        "wifi_connected": connected,
        "ip": wlan.ifconfig()[0] if connected else None,
        "rssi_dbm": wlan.status("rssi") if connected else None,
        "last_fetch_age_s": last_fetch_age_s,
        "last_fetch_ok": _last_fetch_ok,
        "last_fetch_error": _last_fetch_error,
        "current_flight": _current_flight_summary,
        # Surface a few config.py values so the dashboard can match the display (km/mi for distance, ft/m for altitude) and show how often the device fetches new flight data
        "config": {
            "distance_unit": DISTANCE_UNIT,
            "altitude_unit": ALTITUDE_UNIT,
            "refresh_interval_s": REFRESH_INTERVAL,
            "home_lat": LATITUDE,
            "home_lon": LONGITUDE,
        },
    }

def _handle_status(body, query):
    return (200, "application/json", json.dumps(_collect_status()))

def _handle_history(body, query):
    # Recently-seen flights, newest first
    now = time.ticks_ms()
    items = []
    for e in reversed(_flight_history):
        items.append({
            "flight_number": e["flight_number"],
            "origin_iata": e["origin_iata"],
            "destination_iata": e["destination_iata"],
            "aircraft_model": e["aircraft_model"],
            "distance_km": e["distance_km"],
            "registration": e["registration"],
            "age_s": time.ticks_diff(now, e["seen_ticks_ms"]) // 1000,
        })
    return (200, "application/json", json.dumps({"flights": items}))

def _handle_logs(body, query):
    return (200, "text/plain; charset=utf-8", bytes(_log_buffer.buf))

def _handle_index(body, query):
    return (200, "text/html; charset=utf-8", dashboard.render_status_html(_collect_status()))

def register_routes():
    webserver.route("GET",  "/",              _handle_index)
    webserver.route("GET",  "/status",        _handle_status)
    webserver.route("GET",  "/history",       _handle_history)
    webserver.route("GET",  "/logs",          _handle_logs)
    webserver.route("GET",  "/config",        _handle_get_config)
    webserver.route("GET",  "/config-editor", _handle_config_editor)
    webserver.route("GET",  "/wifi",          _handle_wifi_page)
    webserver.route("GET",  "/wifi/scan",     _handle_wifi_scan)
    webserver.route("GET",  "/wifi/status",   _handle_wifi_status)
    webserver.route("POST", "/wifi/save",     _handle_wifi_save)
    webserver.route("POST", "/upload",        _handle_upload)
    webserver.route("POST", "/reboot",        _handle_reboot)

def poll_webserver():
    """Service one HTTP request if pending, then reboot if /reboot was hit."""
    webserver.poll()
    if _reboot_requested:
        time.sleep_ms(500)
        machine.reset()

def network_connect(ssid, password):
    """Connect to WiFi network"""
    wlan = network.WLAN(network.STA_IF)

    try:
        if wlan.isconnected() and wlan.status() == network.STAT_GOT_IP:
            ip = wlan.ifconfig()[0]
            print(f'Reusing existing WiFi connection: {ip}')
            display.set_pen(BLACK)
            display.clear()
            display.set_pen(WHITE)
            display.text("Connected", 2, 2, WIDTH, 1)
            display.text(ip, 2, 13, WIDTH, 1)
            i75.update()
            time.sleep(3)
            return True
    except Exception:
        pass

    wlan.active(True)
    time.sleep(2)
    wlan.config(pm=0xa11140) # turn WiFi power saving off for some slow APs

    print("Connecting to WiFi...")
    display.set_pen(BLACK)
    display.clear()
    display.set_pen(WHITE)
    display.text(f"Connecting to WiFi SSID: {ssid}", 2, 2, WIDTH, 1)
    i75.update()

    wlan.connect(ssid, password)

    max_wait = 10
    while max_wait > 0:
        status = wlan.status()
        if status < 0 or status >= 3:
            break
        max_wait -= 1
        print('Waiting for WiFi connection...')
        time.sleep(1)

    if wlan.status() != 3:
        print("Failed to connect to WiFi")
        display.set_pen(BLACK)
        display.clear()
        display.set_pen(RED)
        display.text(f"WiFi Error SSID: {ssid}", 2, 2, WIDTH, 1)
        i75.update()
        return False
    else:
        print('Connected to WiFi')
        status = wlan.ifconfig()
        ip = status[0]
        print(f'IP: {ip}')
        display.set_pen(BLACK)
        display.clear()
        display.set_pen(WHITE)
        display.text("Connected", 2, 2, WIDTH, 1)
        display.text(ip, 2, 13, WIDTH, 1)
        i75.update()
        time.sleep(5)
        return True

def is_quiet_period():
    """Check if current time is within the quiet period, using UTC_OFFSET. Returns False outright when quiet time is disabled.
    """
    if not globals().get("QUIET_ENABLED", True):
        return False
    try:
        current_time = time.localtime()
        utc_hour = current_time[3]
        utc_minute = current_time[4]

        local_hour = (utc_hour + UTC_OFFSET) % 24
        local_minute = utc_minute

        quiet_start = QUIET_START_HOUR * 60 + QUIET_START_MINUTE
        quiet_end = QUIET_END_HOUR * 60 + QUIET_END_MINUTE
        current = local_hour * 60 + local_minute

        # handle overnight quiet period (eg. 22:00 to 07:00)
        if quiet_start > quiet_end:
            return current >= quiet_start or current < quiet_end
        else: # quiet period is within a single day
            return current >= quiet_start and current < quiet_end
    except:
        return False

def fetch_flight_data(api_key):
    """Fetch closest flight data from the API"""
    global _last_fetch_ticks_ms, _last_fetch_ok, _last_fetch_error
    _last_fetch_ticks_ms = time.ticks_ms()
    try:
        url = f"{API_URL}/closest-flight?lat={LATITUDE}&lon={LONGITUDE}&radius={RADIUS}"
        if ALTITUDE_CEILING_FT is not None:
            url += f"&max_altitude={ALTITUDE_CEILING_FT}"

        headers = {
            "X-API-Key": api_key,
            "User-Agent": f"I75 Matrix Display {USER_AGENT_ID}"
        }

        print(f"Fetching data from: {url}")

        response = urequests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            print("Data received successfully")
            _last_fetch_ok = True
            _last_fetch_error = None
            return data
        else:
            print(f"API Error: {response.status_code}")
            _last_fetch_ok = False
            _last_fetch_error = f"HTTP {response.status_code}"
            display.set_pen(RED)
            display.clear()
            display.text(f"API Err", 2, 2, WIDTH, 1)
            i75.update()
            return None

    except Exception as e:
        print(f"Error fetching data: {e}")
        _last_fetch_ok = False
        _last_fetch_error = str(e)
        display.set_pen(RED)
        display.clear()
        display.text(f"Error", 2, 2, WIDTH, 1)
        i75.update()
        return None
    finally:
        if 'response' in locals():
            response.close()

# Helpers using explicit ranges since the Pimoroni MicroPython build omits str.isdigit()/isalpha()/isalnum()
def _is_digit(ch):
    return "0" <= ch <= "9"

def _is_alpha(ch):
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")

def shorten_aircraft_model(model):
    """Trim an aircraft model string to a concise designator for display.

    The aim is to drop noise we don't care about (numeric variant suffixes and
    trailing marketing names) while keeping hyphens that are part of the type
    designator itself:

      "Airbus A319-132"              -> "Airbus A319"
      "Boeing 787-900"               -> "Boeing 787"
      "Boeing 737 MAX 8"             -> "Boeing 737"
      "Boeing C-17A Globemaster III" -> "Boeing C-17"
      "McDonnell Douglas MD-11F"     -> "McDonnell Douglas MD-11"

    The original code split on the first '-', which mangled designators with an
    internal hyphen ("Boeing C-17A..." became just "Boeing C").
    """
    # Keep manufacturer + designator (first token with a digit); drop trailing marketing words
    tokens = model.split()
    for idx in range(len(tokens)):
        if any(_is_digit(ch) for ch in tokens[idx]):
            model = " ".join(tokens[:idx + 1])
            break

    # Cut a numeric variant at a digit-led hyphen ("-132"); leave designator hyphens ("C-17")
    i = model.find("-")
    while i != -1:
        if i > 0 and _is_digit(model[i - 1]):
            return model[:i]
        i = model.find("-", i + 1)

    # Otherwise drop a single trailing variant letter after a digit ("C-17A" -> "C-17")
    if "-" in model and len(model) >= 2 and _is_alpha(model[-1]) and _is_digit(model[-2]):
        return model[:-1]
    return model

def round_value(value):
    """Round values appropriately (depending on their magnitude) for display"""
    if value >= 1:
        return round(value) # nearest whole number
    elif 0 < value < 1:
        return round(value, 1) # 1 decimal place
    else:
        return value # zero or negative, return as-is

def format_altitude_ft(altitude_ft):
    """Format altitude in feet - 'Nft' under 1000 (eg. '500ft'), 'Nk ft' otherwise (rounded to thousands)."""
    if altitude_ft < 1000:
        return f"{altitude_ft}ft"
    return f"{round(altitude_ft / 1000)}k ft"
    
# Resolved airport names cached by IATA code, since the FR API sometimes returns a flight with an IATA code but no airport name
_airport_names = {}
_AIRPORT_NAME_CACHE_MAX = 100

def _resolve_airport_name(iata, name):
    if not iata or iata == "N/A":
        return name
    if name:
        if iata not in _airport_names and len(_airport_names) >= _AIRPORT_NAME_CACHE_MAX:
            _airport_names.popitem()
        _airport_names[iata] = name
        return name
    return _airport_names.get(iata)

def _record_flight_history(flight_number, origin_iata, destination_iata, aircraft_model, distance_km, registration):
    """Append a flight to the /history ring, skipping consecutive duplicates so a
    flight that stays closest across several fetches is logged once."""
    if _flight_history and _flight_history[-1]["flight_number"] == flight_number:
        return
    _flight_history.append({
        "flight_number": flight_number,
        "origin_iata": origin_iata,
        "destination_iata": destination_iata,
        "aircraft_model": aircraft_model,
        "distance_km": distance_km,
        "registration": registration,
        "seen_ticks_ms": time.ticks_ms(),
    })
    while len(_flight_history) > _FLIGHT_HISTORY_MAX:
        _flight_history.pop(0)

def display_flight_data(data):
    """Display flight data on the screen"""
    global _current_flight_summary

    display.set_pen(BLACK)
    display.clear()

    if DISTANCE_UNIT == "mi":
        distance_modifier = 0.621371
        unit = "mi"
    else:
        distance_modifier = 1
        unit = "km"

    if not data:
        _current_flight_summary = None
        display.set_pen(YELLOW)
        display.text("No data returned", 2, 8, WIDTH, 1)
        i75.update()
        return

    if not data.get("found"):
        _current_flight_summary = None
        display.set_pen(YELLOW)
        display.text(f"No flights in radius {round_value(RADIUS * distance_modifier)}{unit}", 2, 8, WIDTH, 1)
        i75.update()
        return

    # extract data
    flight = data.get("flight", {})
    flight_number = data.get("flight", {}).get("number") or "N/A"
    aircraft_model = shorten_aircraft_model(flight.get("aircraft", {}).get("model") or "N/A")
    distance_km = round_value(data.get("distance_km", {}))
    distance = round_value(distance_km * distance_modifier)
    route = flight.get("route", {})
    origin = route.get("origin_iata") or "N/A"
    destination = route.get("destination_iata") or "N/A"

    position = flight.get("position", {})
    aircraft = flight.get("aircraft", {})
    _current_flight_summary = {
        "flight_number": flight_number,
        "aircraft_model": aircraft_model,
        "distance_km": distance_km,
        "origin_iata": origin,
        "origin_name": _resolve_airport_name(origin, route.get("origin_name")),
        "destination_iata": destination,
        "destination_name": _resolve_airport_name(destination, route.get("destination_name")),
        # Extra fields exposed to /status (JSON) and rendered by the dashboard
        # Display-side rendering ignores them; they exist purely for the web UI
        "altitude_ft": position.get("altitude"),
        "vertical_speed": position.get("vertical_speed"),
        "ground_speed": position.get("ground_speed"),
        "heading": position.get("heading"),
        "latitude": position.get("latitude"),
        "longitude": position.get("longitude"),
        "registration": aircraft.get("registration"),
        "callsign": flight.get("callsign"),
    }

    _record_flight_history(flight_number, origin, destination, aircraft_model, distance_km, aircraft.get("registration"))

    # display the flight info...
    # line 1: origin > destination
    display.set_pen(YELLOW)
    display.text(f"{origin} > {destination}", 2, 2, WIDTH, 1)

    altitude_ft = flight.get("position", {}).get("altitude", 0)
    if ALTITUDE_UNIT == "m":
        altitude_text = f"{round_value(altitude_ft * 0.3048)}m"
    else:
        altitude_text = format_altitude_ft(altitude_ft)

    distance_text = f"{distance}{unit}"
    flight_number_width = display.measure_text(flight_number, 1)
    distance_width = display.measure_text(distance_text, 1)
    altitude_width = display.measure_text(altitude_text, 1)
    model_pixel_width = display.measure_text(aircraft_model, 1)

    # line 2: flight number + distance (update_dynamic_display swaps to altitude when SHOW_ALTITUDE, and scrolls if needed
    draw_scrolling_line(13, [(flight_number, CYAN), (distance_text, BLUE)], 2)

    # line 3: aircraft model (scrolled by update_dynamic_display when it overflows the display)
    draw_scrolling_line(23, [(aircraft_model, MAGENTA)], 2)

    i75.update()

    return {
        "flight_number": flight_number,
        "distance_text": distance_text,
        "altitude_text": altitude_text,
        "line2_distance_width": flight_number_width + SEGMENT_GAP + distance_width,
        "line2_altitude_width": flight_number_width + SEGMENT_GAP + altitude_width,
        "model_text": aircraft_model,
        "model_pixel_width": model_pixel_width,
    }

def draw_scrolling_line(y, segments, x_offset):
    """Render a multi-color line at vertical position y, with text starting at x_offset.

    segments: list of (text, color) tuples drawn left-to-right; the cursor advances
    by measure_text() after each segment. The whole row is cleared first. Shared by
    line 2 (flight number + distance/altitude) and line 3 (aircraft model).
    """
    display.set_pen(BLACK)
    display.rectangle(0, y, WIDTH, 9)
    cursor_x = x_offset
    for i, (text, color) in enumerate(segments):
        if i > 0:
            cursor_x += SEGMENT_GAP
        display.set_pen(color)
        display.text(text, cursor_x, y, 1000, 1)
        cursor_x += display.measure_text(text, 1)

def compute_scroll_offset(elapsed_ms, line_pixel_width):
    """Compute the x offset for marquee scrolling of any line.

    Cycle: pause at left, scroll left until the end is visible at the right
    edge, pause again, then loop. Returns 2 (no scroll) if the line fits within
    the display. Shared by line 2 and line 3.
    """
    if not SCROLL_ENABLED or line_pixel_width < WIDTH:
        return 2

    scroll_distance = line_pixel_width - WIDTH + 2 # end with last char at the right edge
    scroll_duration_ms = scroll_distance * 1000 // SCROLL_SPEED_PX_PER_SEC
    cycle_ms = SCROLL_PAUSE_MS * 2 + scroll_duration_ms
    t = elapsed_ms % cycle_ms

    if t < SCROLL_PAUSE_MS:
        return 2
    elif t < SCROLL_PAUSE_MS + scroll_duration_ms:
        scroll_progress_ms = t - SCROLL_PAUSE_MS
        return 2 - (scroll_progress_ms * scroll_distance // scroll_duration_ms)
    else:
        return 2 - scroll_distance

def draw_countdown(progress):
    """Draw a countdown progress bar in the top-right corner.
    The bar starts filled and reduces to zero from left to right,
    disappearing completely at the end of REFRESH_INTERVAL.
    """
    bar_width = 15
    bar_height = 3
    x = WIDTH - bar_width
    y = 2

    filled_width = max(0, int(bar_width * (1 - progress)))

    display.set_pen(BLACK)
    display.rectangle(x, y, bar_width, bar_height)

    if filled_width > 0:
        display.set_pen(GREEN)
        display.rectangle(x + bar_width - filled_width, y, filled_width, bar_height)
    display.set_pen(BLACK)

def update_dynamic_display(elapsed_ms, cycle_info, state):
    """Per-tick update for countdown, line 2 (altitude/distance swap + scroll), and line 3 scroll.

    `state` is a dict with keys `showing_altitude`, `line2_scroll_start_ms`,
    `line2_offset`, and `line3_offset`. Mutated in place. Both lines route through
    compute_scroll_offset + draw_scrolling_line so they share logic.
    Shared by the hardware main loop and the emulator so both render identically.
    """
    elapsed_s = elapsed_ms / 1000
    progress = elapsed_s / REFRESH_INTERVAL
    draw_countdown(progress)

    if not cycle_info:
        return

    # Pick the line 2 value, and reset its scroll position when the value swaps
    if SHOW_ALTITUDE:
        show_altitude = (int(elapsed_s) // VALUE_SWAP_INTERVAL) % 2 == 1
    else:
        show_altitude = False

    if show_altitude != state["showing_altitude"]:
        state["showing_altitude"] = show_altitude
        state["line2_scroll_start_ms"] = elapsed_ms
        state["line2_offset"] = None # force redraw below

    if show_altitude:
        line2_segments = [(cycle_info["flight_number"], CYAN), (cycle_info["altitude_text"], ORANGE)]
        line2_width = cycle_info["line2_altitude_width"]
    else:
        line2_segments = [(cycle_info["flight_number"], CYAN), (cycle_info["distance_text"], BLUE)]
        line2_width = cycle_info["line2_distance_width"]

    line2_elapsed = elapsed_ms - state["line2_scroll_start_ms"]
    new_line2_offset = compute_scroll_offset(line2_elapsed, line2_width)
    if new_line2_offset != state["line2_offset"]:
        state["line2_offset"] = new_line2_offset
        draw_scrolling_line(13, line2_segments, new_line2_offset)

    new_line3_offset = compute_scroll_offset(elapsed_ms, cycle_info["model_pixel_width"])
    if new_line3_offset != state["line3_offset"]:
        state["line3_offset"] = new_line3_offset
        draw_scrolling_line(23, [(cycle_info["model_text"], MAGENTA)], new_line3_offset)

_AP_ALTERNATE_MS = 4000 # ms per screen in the setup-idle two-screen rotation

def _setup_screen_key(ws, now_ms):
    """Identify the setup screen to show, so the loop only redraws on change."""
    st = ws.state()
    phase = st["phase"]
    if phase == "joined":
        remain = 0
        if st["reboot_at_ms"] is not None:
            remain = max(0, time.ticks_diff(st["reboot_at_ms"], now_ms) // 1000)
        return ("joined", remain)
    if phase == "joining":
        return ("joining", st["auto"])
    if phase == "failed":
        return ("failed", st["error"])
    return ("idle", (now_ms // _AP_ALTERNATE_MS) % 2)

def _draw_setup_screen(ws, screen, ap_ip, ap_name):
    st = ws.state()
    kind = screen[0]
    display.set_pen(BLACK)
    display.clear()
    if kind == "idle":
        if screen[1] == 0:
            if st["reason"] == "connect-failed":
                display.set_pen(ORANGE)
                display.text("WiFi down", 2, 2, WIDTH, 1)
            else:
                display.set_pen(WHITE)
                display.text("WiFi setup", 2, 2, WIDTH, 1)
            display.set_pen(CYAN)
            display.text("Join:", 2, 13, WIDTH, 1)
            display.text(ap_name, 2, 23, WIDTH, 1)
        else:
            display.set_pen(WHITE)
            display.text("Then open", 2, 2, WIDTH, 1)
            display.set_pen(CYAN)
            display.text("http://", 2, 13, WIDTH, 1)
            display.text(ap_ip, 2, 23, WIDTH, 1)
    elif kind == "joining":
        display.set_pen(YELLOW)
        display.text("Trying" + (" (auto)" if st["auto"] else ""), 2, 2, WIDTH, 1)
        display.text(st["target_ssid"] or "", 2, 13, WIDTH, 1)
    elif kind == "joined":
        display.set_pen(GREEN)
        display.text("Saved! IP:", 2, 2, WIDTH, 1)
        display.text(st["ip"] or "", 2, 13, WIDTH, 1)
        display.text(f"reboot in {screen[1]}s", 2, 23, WIDTH, 1)
    else: # failed
        display.set_pen(RED)
        display.text("Join failed", 2, 2, WIDTH, 1)
        display.text(st["error"] or "", 2, 13, WIDTH, 1)
    i75.update()

def run_setup_mode(reason, saved_ssid, saved_password):
    """AP-mode provisioning: serve /wifi on an open hotspot until working creds
    are saved. Never returns - every exit path is a machine.reset()."""
    import wifi_setup # lazy: normal boots never pay for its page string

    print(f"SETUP: entering setup mode ({reason})")
    wifi_setup.begin(reason, saved_ssid, saved_password)
    ap_ip = wifi_setup.start_ap()
    ap_name = wifi_setup.ap_ssid()
    print(f"SETUP: join '{ap_name}' then open http://{ap_ip}/")

    wifi_setup.register_routes(True)
    webserver.route("GET", "/", wifi_setup.handle_page) # bare AP IP lands on the setup page
    # /upload, /logs and /reboot stay available so push.py can still fix a device stuck in setup mode
    webserver.route("POST", "/upload", _handle_upload)
    webserver.route("GET",  "/logs",   _handle_logs)
    webserver.route("POST", "/reboot", _handle_reboot)
    try:
        webserver.start()
    except Exception as e:
        print(f"SETUP: webserver failed to start: {e}")

    last_screen = None
    while True:
        now = time.ticks_ms()
        wifi_setup.tick(now)
        screen = _setup_screen_key(wifi_setup, now)
        if screen != last_screen:
            _draw_setup_screen(wifi_setup, screen, ap_ip, ap_name)
            last_screen = screen
        poll_webserver()
        time.sleep_ms(100)

def main():
    """Main function to connect to WiFi, fetch data, and display it"""
    print("BOOT: main() entered")

    try:
        network.hostname("flightdisplay") # enables http://flightdisplay.local where mDNS works
    except Exception:
        pass # older firmware without hostname support

    # SW_A held at power-on forces setup mode (eg. to move the device to a new network)
    try:
        force_setup = i75.switch_pressed(i75.SWITCH_A)
    except Exception:
        force_setup = False # boards/firmware without SW_A
    if force_setup:
        run_setup_mode("button", None, None)
        return

    try:
        import secrets as _secrets
        WIFI_SSID = getattr(_secrets, "WIFI_SSID", "")
        WIFI_PASSWORD = getattr(_secrets, "WIFI_PASSWORD", "")
        FLIGHT_FINDER_API_KEY = getattr(_secrets, "FLIGHT_FINDER_API_KEY", "")
    except ImportError:
        WIFI_SSID, WIFI_PASSWORD, FLIGHT_FINDER_API_KEY = "", "", ""

    if not WIFI_SSID:
        # No usable secrets.py - provision over the setup hotspot instead of dead-ending.
        # An empty WIFI_PASSWORD is allowed through (open networks).
        run_setup_mode("no-creds", None, None)
        return

    print("BOOT: secrets loaded, connecting WiFi")
    connected = False
    for attempt in range(3):
        connected = network_connect(WIFI_SSID, WIFI_PASSWORD)
        if connected:
            break
        print(f"WiFi attempt {attempt + 1}/3 failed")
        time.sleep(5)
    if not connected:
        run_setup_mode("connect-failed", WIFI_SSID, WIFI_PASSWORD)
        return

    # Start the webserver BEFORE anything that might block (eg. NTP DNS resolution)
    # This guarantees `./push.py` can always reach us to push fixes, even if the rest of startup hangs
    print("BOOT: registering routes")
    register_routes()
    print("BOOT: starting webserver")
    try:
        webserver.start()
    except Exception as e:
        print(f"Webserver failed to start: {e}") # non-fatal; display still works

    if not FLIGHT_FINDER_API_KEY:
        # WiFi works but there's no API key yet: park on /wifi until one is saved.
        # A successful save sets the reboot flag, so poll_webserver() restarts us.
        ip = network.WLAN(network.STA_IF).ifconfig()[0]
        print(f"BOOT: no API key - set it via http://{ip}/wifi")
        display.set_pen(BLACK)
        display.clear()
        display.set_pen(RED)
        display.text("No API key", 2, 2, WIDTH, 1)
        display.set_pen(WHITE)
        display.text(ip, 2, 13, WIDTH, 1)
        display.text("/wifi", 2, 23, WIDTH, 1)
        i75.update()
        while True:
            poll_webserver()
            time.sleep_ms(100)

    print("BOOT: attempting NTP sync")
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
        now = time.localtime()
        print("Date: {}/{}/{}".format(now[1], now[2], now[0]))
        print("Time (UTC): {:02d}:{:02d}".format(now[3], now[4]))
    except:
        print("Failed to sync time")

    print("BOOT: entering display loop")
    display.set_pen(BLACK)
    display.clear()
    display.set_pen(GREEN)
    display.text("Fetching...", 2, 2, 100, 1)
    display.text(f"{LATITUDE}", 2, 13, 100, 1)
    display.text(f"{LONGITUDE}", 2, 23, 100, 1)
    i75.update()
    time.sleep(3)
    
    while True:
        if is_quiet_period():
            print("Quiet time")
            clear_display()
            time.sleep(300)
            continue

        try:
            flight_data = fetch_flight_data(FLIGHT_FINDER_API_KEY)
            print(f"Displaying flight data for {REFRESH_INTERVAL} seconds...")
            cycle_info = display_flight_data(flight_data)

            start_ticks = time.ticks_ms()
            state = {
                "showing_altitude": False,
                "line2_scroll_start_ms": 0,
                "line2_offset": 2,
                "line3_offset": 2,
            }
            refresh_interval_ms = REFRESH_INTERVAL * 1000
            while time.ticks_diff(time.ticks_ms(), start_ticks) < refresh_interval_ms:
                elapsed_ms = time.ticks_diff(time.ticks_ms(), start_ticks)
                update_dynamic_display(elapsed_ms, cycle_info, state)
                i75.update()
                poll_webserver()
                time.sleep_ms(100)

        except Exception as e:
            print(f"Error in main loop: {e}")
            display.set_pen(RED)
            display.clear()
            display.text("Error", 2, 2, WIDTH, 1)
            i75.update()
            for _ in range(10):
                poll_webserver()
                time.sleep(1)

# On the device this file runs as main.py (__main__); the guard keeps imports
# (emulator, tests) from booting the display loop / setup mode
if __name__ == "__main__":
    main()

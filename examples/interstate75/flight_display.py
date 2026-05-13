import json
import machine
import network
import ntptime
import time
import urequests
from interstate75 import Interstate75

# config lives in config.py - edit it there to customise behaviour
from config import *

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

# px between adjacent segments on a multi-segment scrolling line (eg. between
# the flight number and the distance/altitude value on line 2)
SEGMENT_GAP = 4

#############
# Functions #
#############
def clear_display():
    """Clear the display / turn it off"""
    display.set_pen(BLACK)
    display.clear()
    i75.update()

def network_connect(ssid, password):
    """Connect to WiFi network"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
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
        print(f'IP: {status[0]}')
        display.set_pen(BLACK)
        display.clear()
        display.set_pen(WHITE)
        display.text("Connected", 2, 2, WIDTH, 1)
        i75.update()
        return True

def is_quiet_period():
    """Check if current time is within the quiet period, using UTC_OFFSET"""
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
    try:
        url = f"{API_URL}/closest-flight?lat={LATITUDE}&lon={LONGITUDE}&radius={RADIUS}"
        
        headers = {
            "X-API-Key": api_key,
            "User-Agent": f"I75 Matrix Display {USER_AGENT_ID}"
        }

        print(f"Fetching data from: {url}")

        response = urequests.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            print("Data received successfully")
            return data
        else:
            print(f"API Error: {response.status_code}")
            display.set_pen(RED)
            display.clear()
            display.text(f"API Err", 2, 2, WIDTH, 1)
            i75.update()
            return None

    except Exception as e:
        print(f"Error fetching data: {e}")
        display.set_pen(RED)
        display.clear()
        display.text(f"Error", 2, 2, WIDTH, 1)
        i75.update()
        return None
    finally:
        if 'response' in locals():
            response.close()

def shorten_aircraft_model(model):
    """Drop the variant suffix (anything after '-') for display."""
    if '-' in model:
        model = model.split('-')[0] # eg. remove "-132" from "Airbus A319-132"
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
    """Format altitude in feet — 'Nft' under 1000 (eg. '500ft'), 'Nk ft' otherwise (rounded to thousands)."""
    if altitude_ft < 1000:
        return f"{altitude_ft}ft"
    return f"{round(altitude_ft / 1000)}k ft"
    
def display_flight_data(data):
    """Display flight data on the screen"""
    display.set_pen(BLACK)
    display.clear()

    if DISTANCE_UNIT == "mi":
        distance_modifier = 0.621371
        unit = "mi"
    else:
        distance_modifier = 1
        unit = "km"

    if not data:
        display.set_pen(YELLOW)
        display.text("No data returned", 2, 8, WIDTH, 1)
        i75.update()
        return

    if not data.get("found"):
        display.set_pen(YELLOW)
        display.text(f"No flights in radius {round_value(RADIUS * distance_modifier)}{unit}", 2, 8, WIDTH, 1)
        i75.update()
        return
    
    # extract data
    flight = data.get("flight", {})
    flight_number = data.get("flight", {}).get("number") or "N/A"
    aircraft_model = shorten_aircraft_model(flight.get("aircraft", {}).get("model", "N/A"))
    distance_km = round_value(data.get("distance_km", {}))
    distance = round_value(distance_km * distance_modifier)
    origin = flight.get("route", {}).get("origin_iata", "N/A")
    destination = flight.get("route", {}).get("destination_iata", "N/A")
    
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

    # line 2: flight number + distance (update_dynamic_display swaps to altitude when SHOW_ALTITUDE,
    # and scrolls if the combined width overflows the display)
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

    # Pick the line 2 value, and reset its scroll clock when the value swaps so
    # the new content starts from the left with a pause (rather than jumping into
    # an arbitrary scroll position carried over from the previous value's width).
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

def main():
    """Main function to connect to WiFi, fetch data, and display it"""
    try:
        from secrets import WIFI_PASSWORD, WIFI_SSID, FLIGHT_FINDER_API_KEY
        if WIFI_SSID == "":
            raise ValueError("WIFI_SSID in 'secrets.py' is empty")
        if WIFI_PASSWORD == "":
            raise ValueError("WIFI_PASSWORD in 'secrets.py' is empty")
        if not FLIGHT_FINDER_API_KEY:
            raise ValueError("FLIGHT_FINDER_API_KEY in 'secrets.py' is empty")
    except ImportError:
        display.set_pen(RED)
        display.clear()
        display.text("Missing", 2, 2, WIDTH, 1)
        display.text("secrets.py", 2, 8, WIDTH, 1)
        i75.update()
        return
    except ValueError as e:
        display.set_pen(RED)
        display.clear()
        display.text(str(e)[:10], 2, 2, WIDTH, 1) # show first 10 chars of error
        i75.update()
        return

    connected = False
    while not connected:
        connected = network_connect(WIFI_SSID, WIFI_PASSWORD)
        if not connected:
            time.sleep(5)

    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
        now = time.localtime()
        print("Date: {}/{}/{}".format(now[1], now[2], now[0]))
        print("Time (UTC): {:02d}:{:02d}".format(now[3], now[4]))
    except:
        print("Failed to sync time")

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
            time.sleep(300) # sleep for 5 minutes during quiet period to reduce activity
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
                time.sleep_ms(100)

        except Exception as e:
            print(f"Error in main loop: {e}")
            display.set_pen(RED)
            display.clear()
            display.text("Error", 2, 2, WIDTH, 1)
            i75.update()
            time.sleep(10)

main()

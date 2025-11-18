import json
import machine
import network
import ntptime
import time
import urequests
from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X32

i75 = Interstate75(display=DISPLAY_INTERSTATE75_64X32)
display = i75.display

WIDTH  = i75.width # 64 pixels
HEIGHT = i75.height # 32 pixels

##########
# Config #
##########
API_URL          = "https://wherever-the-flight-finder-service-is-deployed"
BRIGHT_MODE      = False # Set to True for brighter (higher intensity) colours
DISTANCE_UNIT    = "km" # km or mi, for display purposes only
LATITUDE         = 51.5274575 # lat of display location
LONGITUDE        = -0.2595316 # lon of display location
RADIUS           = 10 # km, for finding flights (from lat/lon)
REFRESH_INTERVAL = 60 # seconds, best to keep this at 30s or more
USER_AGENT_ID    = "Flight Tracker 1" # ID used as part of user-agent header in requests to API, eg. "I75 Matrix Display {USER_AGENT_ID}" (useful for identifying the devices making requests)

# "quiet time" config (ie. show nothing on the display between these times)
UTC_OFFSET         = 0 # offset of your timezone from UTC (eg. for UTC+2 set to 2, for UTC-5 set to -5)
QUIET_START_HOUR   = 22
QUIET_START_MINUTE = 0
QUIET_END_HOUR     = 7
QUIET_END_MINUTE   = 0

# colors (RGB values are weirdly off - bug in I75 v0.0.5?)
BLACK   = display.create_pen(0, 0, 0)
WHITE   = display.create_pen(*((255, 255, 255) if BRIGHT_MODE else (200, 200, 200)))
RED     = display.create_pen(*((64, 64, 255) if BRIGHT_MODE else (32, 32, 128)))
GREEN   = display.create_pen(*((255, 64, 64) if BRIGHT_MODE else (128, 32, 32)))
BLUE    = display.create_pen(*((64, 255, 64) if BRIGHT_MODE else (32, 128, 32)))
CYAN    = display.create_pen(*((255, 255, 64) if BRIGHT_MODE else (128, 128, 32)))
MAGENTA = display.create_pen(*((64, 255, 255) if BRIGHT_MODE else (32, 128, 128)))
YELLOW  = display.create_pen(*((255, 64, 255) if BRIGHT_MODE else (128, 32, 128)))

# font
display.set_font("bitmap8")

#############
# Functions #
#############
def clear_display():
    """Clear the display and turn it off"""
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
    display.text("Connecting to WiFi...", 2, 2, WIDTH, 1)
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
        display.set_pen(RED)
        display.clear()
        display.text("WiFi Err", 2, 2, WIDTH, 1)
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
    """Replace long manufacturer names with shorthand versions. 
    Remove unneeded model info
    """
    if '-' in model:
        model = model.split('-')[0] # eg. remove "-132" from "Airbus A319-132"
        
    words = model.split()

    if not words:
        return model

    manufacturer_shorthand = {
        "Mitsubishi": "Mitsu",
        "Bombardier": "Bomba"
    }

    first_word = words[0]
    if first_word in manufacturer_shorthand:
        words[0] = manufacturer_shorthand[first_word]

    return " ".join(words)

def round_value(value):
    """Round values appropriately (depending on their magnitude) for display"""
    if value >= 1:
        return round(value) # nearest whole number
    elif 0 < value < 1:
        return round(value, 1) # 1 decimal place
    else:
        return value # zero or negative, return as-is
    
def display_flight_data(data):
    """Display flight data on the Interstate 75 screen"""
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
    flight_number = data.get("flight", {}).get("number", "N/A")
    aircraft_model = shorten_aircraft_model(flight.get("aircraft", {}).get("model", "N/A"))
    distance_km = round_value(data.get("distance_km", {}))
    distance = round_value(distance_km * distance_modifier)
    origin = flight.get("route", {}).get("origin_iata", "N/A")
    destination = flight.get("route", {}).get("destination_iata", "N/A")
    
    # display the flight info...
    # line 1: origin > destination
    display.set_pen(YELLOW)
    display.text(f"{origin} > {destination}", 2, 2, WIDTH, 1)

    # line 2: flight number and distance
    display.set_pen(CYAN)
    display.text(f"{flight_number}", 2, 13, WIDTH, 1)
    flight_pixel_width = len(flight_number) * 6 # 6 is the character width for bitmap8
    display.set_pen(BLUE)
    display.text(f"{distance}{unit}", flight_pixel_width, 13, 100, 1)

    # line 3: aircraft model
    display.set_pen(MAGENTA)
    display.text(f"{aircraft_model}", 2, 23, 100, 1) # set word-wrap to a large value (100) so as to never wrap

    i75.update()

def draw_countdown(progress):
    """Draw a countdown progress bar in the top-right corner.
    The bar starts filled and reduces to zero from left to right,
    disappearing completely only at the end of REFRESH_INTERVAL.
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

def main():
    """Main function to connect to WiFi, fetch data, and display it"""
    try:
        from secrets import WIFI_PASSWORD, WIFI_SSID, FLIGHT_FINDER_API_KEY
        if WIFI_SSID == "":
            raise ValueError("WIFI_SSID in 'secrets.py' is empty!")
        if WIFI_PASSWORD == "":
            raise ValueError("WIFI_PASSWORD in 'secrets.py' is empty!")
        if not FLIGHT_FINDER_API_KEY:
            raise ValueError("FLIGHT_FINDER_API_KEY in 'secrets.py' is empty!")
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
            display_flight_data(flight_data)

            start_time = time.time()
            while time.time() - start_time < REFRESH_INTERVAL:
                elapsed = time.time() - start_time
                progress = elapsed / REFRESH_INTERVAL
                draw_countdown(progress)
                i75.update()
                time.sleep(1)

        except Exception as e:
            print(f"Error in main loop: {e}")
            display.set_pen(RED)
            display.clear()
            display.text("Error", 2, 2, WIDTH, 1)
            i75.update()
            time.sleep(10)

main()

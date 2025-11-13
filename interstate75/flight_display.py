import time
import machine
import network
import urequests
import json
from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X32

i75 = Interstate75(display=DISPLAY_INTERSTATE75_64X32)
display = i75.display
WIDTH = i75.width # 64 pixels
HEIGHT = i75.height # 32 pixels

# location config
LATITUDE = 33.0118884
LONGITUDE = -97.0558339
RADIUS = 25 # km

# colors
BLACK = display.create_pen(0, 0, 0)
WHITE = display.create_pen(255, 255, 255)
RED = display.create_pen(255, 0, 0)
GREEN = display.create_pen(0, 255, 0)
BLUE = display.create_pen(0, 0, 255)
YELLOW = display.create_pen(255, 255, 0)
CYAN = display.create_pen(0, 255, 255)
MAGENTA = display.create_pen(255, 0, 255)

# font
display.set_font("bitmap8")

def network_connect(ssid, password):
    """Connect to Wi-Fi network with improved reliability"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.config(pm=0xa11140) # turn WiFi power saving off for some slow APs

    print("Connecting to Wi-Fi...")
    display.set_pen(BLACK)
    display.clear()
    display.set_pen(WHITE)
    display.text("WiFi...", 2, 2, WIDTH, 1)
    i75.update()

    wlan.connect(ssid, password)

    max_wait = 10
    while max_wait > 0:
        status = wlan.status()
        if status < 0 or status >= 3:
            break
        max_wait -= 1
        print('Waiting for connection...')
        display.fill_rectangle(2, 2, WIDTH, 8)
        display.set_pen(WHITE)
        display.text(f"WiFi {max_wait}", 2, 2, WIDTH, 1)
        i75.update()
        time.sleep(1)

    if wlan.status() != 3:
        print("Failed to connect to Wi-Fi")
        display.set_pen(RED)
        display.clear()
        display.text("WiFi Err", 2, 2, WIDTH, 1)
        i75.update()
        return False
    else:
        print('Connected to Wi-Fi')
        status = wlan.ifconfig()
        print(f'IP: {status[0]}')
        display.set_pen(WHITE)
        display.clear()
        display.text("Connected", 2, 2, WIDTH, 1)
        i75.update()
        return True

def fetch_flight_data(api_key):
    """Fetch closest flight data from the API"""
    try:
        url = f"https://flight-finder.gregdev.com/closest-flight?lat={LATITUDE}&lon={LONGITUDE}&radius={RADIUS}"
        
        headers = {
            "X-API-Key": api_key
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
    """Replace manufacturer names with shorthand versions."""
    words = model.split()

    if not words:
        return model

    manufacturer_shorthand = {
        "Mitsubishi": "Mitsu",
        "Bombardier": "Bomb"
    }

    first_word = words[0]
    if first_word in manufacturer_shorthand:
        words[0] = manufacturer_shorthand[first_word]

    return " ".join(words)

def display_flight_data(data):
    """Display flight data on the Interstate 75 screen"""
    display.set_pen(BLACK)
    display.clear()

    if not data:
        display.set_pen(RED)
        display.text("No data", 2, 2, WIDTH, 1)
        i75.update()
        return

    if not data.get("found"):
        display.set_pen(WHITE)
        display.text("No flights", 2, 2, WIDTH, 1)
        i75.update()
        return
    
    # extract data
    flight = data.get("flight", {})
    flight_number = data.get("flight", {}).get("number", "N/A")
    aircraft_model = shorten_aircraft_model(flight.get("aircraft", {}).get("model", "N/A"))
    origin = flight.get("route", {}).get("origin_iata", "N/A")
    destination = flight.get("route", {}).get("destination_iata", "N/A")
    
    # eg. remove "-132" from "Airbus A319-132"
    if '-' in aircraft_model:
        aircraft_model = aircraft_model.split('-')[0] 

    # display the flight info...
    display.set_pen(MAGENTA)
    display.text(f"{origin} > {destination}", 2, 2, WIDTH, 1)

    display.set_pen(CYAN)
    display.text(f"{flight_number}", 2, 13, WIDTH, 1)

    display.set_pen(YELLOW)
    display.text(f"{aircraft_model}", 2, 23, 100, 1) # set word-wrap to a large value (100) so as to never wrap

    i75.update()

def main():
    """Main function to connect to Wi-Fi, fetch data, and display it"""
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

    display.set_pen(BLACK)
    display.clear()
    display.set_pen(WHITE)
    display.text("Fetching...", 2, 2, 100, 1)
    display.text(f"{LATITUDE}", 2, 13, 100, 1)
    display.text(f"{LONGITUDE}", 2, 23, 100, 1)
    i75.update()
    time.sleep(3)
    
    while True:
        try:
            flight_data = fetch_flight_data(FLIGHT_FINDER_API_KEY)

            display_flight_data(flight_data)

            time.sleep(30)

        except Exception as e:
            print(f"Error in main loop: {e}")
            display.set_pen(RED)
            display.clear()
            display.text("Error", 2, 2, WIDTH, 1)
            i75.update()
            time.sleep(10)

main()

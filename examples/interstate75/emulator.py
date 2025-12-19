#!/usr/bin/env python3
"""
ASCII Emulator for Interstate75 LED Matrix Display (32x64 pixels)
This emulator allows testing the Micropython flight_display.py code on a laptop.
"""

import time
import sys
import os
import json
from typing import Optional, Dict, Any

# mock Micropython modules that don't exist in regular Python
class MockMachine:
    pass

class MockNetwork:
    STA_IF = 0
    
    class WLAN:
        def __init__(self, interface):
            self.interface = interface
            self._active = False
            self._ssid = None
            self._password = None
            
        def active(self, state=None):
            if state is not None:
                self._active = state
            return self._active
            
        def config(self, **kwargs):
            pass
            
        def connect(self, ssid, password):
            self._ssid = ssid
            self._password = password
            
        def status(self):
            return 3  # Connected
            
        def ifconfig(self):
            return ('192.168.1.100', '255.255.255.0', '192.168.1.1', '8.8.8.8')

class MockNTPTime:
    host = "pool.ntp.org"
    
    @staticmethod
    def settime():
        pass

class MockUrequests:
    class Response:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data
            
        def json(self):
            return self._json_data
            
        def close(self):
            pass
    
    @staticmethod
    def get(url, headers=None):
      test_data_file = os.path.join(os.path.dirname(__file__), 'test_flight_data.json')
      
      try:
          with open(test_data_file, 'r') as f:
              test_data = json.load(f)
          print(f"[EMULATOR] Loaded test data from {test_data_file}")
      except FileNotFoundError:
          print(f"[EMULATOR] Warning: {test_data_file} not found, using default data")
          test_data = {
              "found": True,
              "distance_km": 5.2,
              "flight": {
                  "number": "BA123",
                  "aircraft": {
                      "model": "Airbus A320-232"
                  },
                  "route": {
                      "origin_iata": "LHR",
                      "destination_iata": "CDG"
                  }
              }
          }
      except json.JSONDecodeError as e:
          print(f"[EMULATOR] Error parsing {test_data_file}: {e}")
          return MockUrequests.Response(500, {})
      
      print(f"[EMULATOR] Mock HTTP GET: {url}")
      return MockUrequests.Response(200, test_data)

# mock Interstate75 display
class MockDisplay:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        # store both character and color for each pixel: (char, color)
        # though we're not actually rendering colors in ASCII, yet...
        self.buffer = [[(' ', 'BLACK') for _ in range(width)] for _ in range(height)]
        self.current_pen = 'BLACK'
        self.font = "bitmap8"
        
    def create_pen(self, r, g, b):
        """Return a color name based on RGB values"""
        if r == 0 and g == 0 and b == 0:
            return 'BLACK'
        elif r > 200 and g > 200 and b > 200:
            return 'WHITE'
        elif b > g and b > r:
            return 'BLUE'
        elif r > g and r > b:
            return 'RED'
        elif g > r and g > b:
            return 'GREEN'
        elif g > 0 and b > 0 and r == 0:
            return 'CYAN'
        elif r > 0 and b > 0 and g == 0:
            return 'MAGENTA'
        elif r > 0 and g > 0 and b == 0:
            return 'YELLOW'
        else:
            return 'WHITE'
    
    def set_pen(self, color):
        self.current_pen = color
        
    def set_font(self, font):
        self.font = font
        
    def clear(self):
        """Clear the buffer"""
        for y in range(self.height):
            for x in range(self.width):
                self.buffer[y][x] = (' ', self.current_pen)
                
    def text(self, text, x, y, width, scale):
        """Draw text on the buffer"""
        # reduce character spacing from 6 to 4 pixels for tighter rendering
        char_width = 4
        text = str(text)
        
        for i, char in enumerate(text):
            char_x = x + (i * char_width)
            if char_x >= self.width:
                break
            if 0 <= y < self.height and 0 <= char_x < self.width:
                self.buffer[y][char_x] = (char, self.current_pen)
                
    def rectangle(self, x, y, width, height):
        """Draw a filled rectangle"""
        for dy in range(height):
            for dx in range(width):
                px = x + dx
                py = y + dy
                if 0 <= px < self.width and 0 <= py < self.height:
                    self.buffer[py][px] = ('█', self.current_pen)

class MockInterstate75:
    COLOR_ORDER_RGB = 0
    COLOR_ORDER_GRB = 1
    
    def __init__(self, display, color_order):
        self.display = MockDisplay(64, 32)
        self.width = 64
        self.height = 32
        self.color_order = color_order
        
    def update(self):
        """Render the display buffer to ASCII - simplified without colors"""
        # build the entire output as a string first, then print once
        output = []
        
        output.append('\033[2J\033[H')
        output.append("╔" + "═" * self.width + "╗\n")
        
        for row in range(0, self.height, 2):
            output.append("║")
            for col in range(self.width):
                top_char, top_color = self.display.buffer[row][col]
                if row + 1 < self.height:
                    bottom_char, bottom_color = self.display.buffer[row + 1][col]
                else:
                    bottom_char, bottom_color = ' ', 'BLACK'
                
                if top_char not in (' ', '█'):
                    output.append(top_char)
                elif bottom_char not in (' ', '█'):
                    output.append(bottom_char)
                elif top_color == 'BLACK' and bottom_color == 'BLACK':
                    output.append(' ')
                elif top_color == bottom_color and top_color != 'BLACK':
                    output.append('█')
                elif top_color != 'BLACK' and bottom_color == 'BLACK':
                    output.append(' ')
                elif top_color == 'BLACK' and bottom_color != 'BLACK':
                    output.append(' ')
                else:
                    output.append('▀')
            output.append("║\n")
            
        output.append("╚" + "═" * self.width + "╝\n")
        output.append("32x64 LED Matrix Emulator | Ctrl+C to exit\n")
        
        print(''.join(output), end='', flush=True)

# install mocks into sys.modules so imports work
sys.modules['machine'] = MockMachine()
sys.modules['network'] = MockNetwork()
sys.modules['ntptime'] = MockNTPTime()
sys.modules['urequests'] = MockUrequests()

# mock the Interstate75 module
class Interstate75Module:
    DISPLAY_INTERSTATE75_64X32 = 0
    Interstate75 = MockInterstate75
    
sys.modules['interstate75'] = Interstate75Module()

# now we can import and run the actual flight display code
# we need to override the main() function to avoid the infinite loop
if __name__ == "__main__":
    print("Starting Interstate75 LED Matrix Emulator...")
    print("This emulator displays ASCII output in your terminal.")
    print()
    
    import flight_display
    
    original_main = flight_display.main
    
    def emulator_main():
        """Modified main function for emulator testing"""
        class Secrets:
            WIFI_SSID = "TestNetwork"
            WIFI_PASSWORD = "password123"
            FLIGHT_FINDER_API_KEY = "test-api-key"
        
        sys.modules['secrets'] = Secrets()
        
        print("[EMULATOR] Connecting to WiFi (mocked)...")
        flight_display.network_connect(Secrets.WIFI_SSID, Secrets.WIFI_PASSWORD)
        time.sleep(1)
        
        print("[EMULATOR] Syncing time (mocked)...")
        time.sleep(1)
        
        iterations = 5
        
        for i in range(iterations):
            print(f"\n[EMULATOR] Iteration {i + 1}/{iterations}")
            
            if flight_display.is_quiet_period():
                print("[EMULATOR] Quiet time detected")
                flight_display.clear_display()
            else:
                flight_data = flight_display.fetch_flight_data(Secrets.FLIGHT_FINDER_API_KEY)
                flight_display.display_flight_data(flight_data)
                
                for second in range(flight_display.REFRESH_INTERVAL):
                    progress = second / flight_display.REFRESH_INTERVAL
                    flight_display.draw_countdown(progress)
                    flight_display.i75.update()
                    time.sleep(1)
        
        print("\n[EMULATOR] Test complete!")
    
    try:
        emulator_main()
    except KeyboardInterrupt:
        print("\n[EMULATOR] Stopped by user")
        sys.exit(0)

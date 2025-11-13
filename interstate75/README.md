# Interstate 75 Flight Display

This MicroPython project displays nearby flight information (fetched from the Flight Finder Service) on an [Interstate 75 W](https://shop.pimoroni.com/products/interstate-75-w?variant=54977948713339) powered LED display ([64x32 RGB LED matrix](https://shop.pimoroni.com/products/rgb-led-matrix-panel?variant=42312764298)).

Inspired by: https://blog.colinwaddell.com/articles/flight-tracker

## Setup

Follow Pimoroni's guide: https://learn.pimoroni.com/article/getting-started-with-interstate-75

(be sure to utilise the "... with filesystem" firmware)

Once connected to the device:
  - Copy the `flight_display.py` file onto the device
  - Configure the location by editing the `LATITUDE`, `LONGITUDE` and `RADIUS` variables in `flight_display.py`
  - Create a `secrets.py` file containing:

    ```python
    WIFI_SSID = ""
    WIFI_PASSWORD = "a"
    FLIGHT_FINDER_API_KEY = ""
    ```
  Run the `flight_display.py` script to start displaying flights

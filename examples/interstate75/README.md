# Interstate 75 Flight Display

This MicroPython project displays nearby flight information (fetched from the Flight Finder Service API) on an [Interstate 75 W](https://shop.pimoroni.com/products/interstate-75-w?variant=54977948713339) powered LED display ([64x32 RGB LED matrix](https://shop.pimoroni.com/products/rgb-led-matrix-panel?variant=42312764298)).

Inspired by: https://blog.colinwaddell.com/articles/flight-tracker

## Setup

Follow Pimoroni's guide: https://learn.pimoroni.com/article/getting-started-with-interstate-75

(be sure to utilise the "... with filesystem" firmware)

This example was tested on Interstate 75 firmware version `0.0.5`.

Once connected to the I75 device:
  - Copy the `flight_display.py` file onto the device (optionally rename it to `main.py` to run on boot)
  - Set `API_URL` in `flight_display.py`
  - Set the config options towards the top of `flight_display.py`, including the location `LATITUDE`, `LONGITUDE` and `RADIUS`
  - Optionally adjust the quiet time settings in `flight_display.py` (show nothing on the display between these times)
    - Be sure to set `UTC_OFFSET` to correctly calculate quiet time based on your timezone
  - Note the line `color_order=Interstate75.COLOR_ORDER_GRB` in `flight_display.py` - this was required for the specific panel tested, but you may need to change it back to `COLOR_ORDER_RGB` or another value depending on your panel
  - Create a `secrets.py` file containing:

    ```python
    WIFI_SSID = ""
    WIFI_PASSWORD = ""
    FLIGHT_FINDER_API_KEY = ""
    ```
  Run the `flight_display.py` script to start displaying flights


## Emulator

This emulator allows testing of the `flight_display.py` Micropython code without needing to connect to the actual Interstate75 hardware.

```bash
python3 emulator.py
```

<img width="468" height="319" alt="emulator" src="https://github.com/user-attachments/assets/8395d392-4acb-4cdf-810b-b613bb94ac06" />


### Test Flight Data

The emulator loads test data from `test_flight_data.json`. Create and edit this file to test different scenarios:

```json
{
  "found": true,
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
```

If nothing is displayed / the emulator quits, check the "quiet time" settings in `flight_display.py` to ensure the current time is outside of the configured quiet period.

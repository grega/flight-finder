# Interstate 75 Flight Display

This MicroPython project displays nearby flight information (fetched from the Flight Finder Service API) on an [Interstate 75 W](https://shop.pimoroni.com/products/interstate-75-w?variant=54977948713339) powered LED display ([64x32 RGB LED matrix](https://shop.pimoroni.com/products/rgb-led-matrix-panel?variant=42312764298)).

Inspired by: https://blog.colinwaddell.com/articles/flight-tracker

## Setup

Follow Pimoroni's guide: https://learn.pimoroni.com/article/getting-started-with-interstate-75

(be sure to utilise the "... with filesystem" firmware)

This example was tested on Interstate 75 firmware version `0.0.5`.

Once connected to the I75 device:
  - Copy `flight_display.py`, `config.py`, `webserver.py`, and `dashboard.py` onto the device (optionally rename `flight_display.py` to `main.py` to run on boot)
  - Edit `config.py`:
    - Set `API_URL` to the deployed Flight Finder Service
    - Set your location: `LATITUDE`, `LONGITUDE`, and `RADIUS`
    - Set `DISPLAY_TYPE` and `COLOR_ORDER` to match your panel - see [Display panel configuration](#display-panel-configuration) below
    - Optionally adjust the quiet time settings (show nothing on the display between these times)
      - Be sure to set `UTC_OFFSET` to correctly calculate quiet time based on your timezone
    - Other options include `SHOW_ALTITUDE` (cycles altitude alongside distance), `DISTANCE_UNIT`, `ALTITUDE_UNIT`, `ALTITUDE_CEILING_FT` (ignore flights above this altitude - useful to filter out cruise overflights and focus on flights arriving/departing nearby airports), and scroll/refresh timing
  - Create a `secrets.py` file containing:

    ```python
    WIFI_SSID = ""
    WIFI_PASSWORD = ""
    FLIGHT_FINDER_API_KEY = ""
    ```
  Run the `flight_display.py` script to start displaying flights

### Display panel configuration

LED matrix panels vary in their physical dimensions, scan rates, and how their red/green/blue lines are wired. Two settings in `config.py` need to match your specific panel:

#### `DISPLAY_TYPE`

Identifies the panel. Defaults to `DISPLAY_INTERSTATE75_64X32` - suitable for a standard 64×32 RGB matrix. To use a different panel, import the matching constant at the top of `config.py` and assign it:

```python
from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X64
DISPLAY_TYPE = DISPLAY_INTERSTATE75_64X64
```

Pimoroni's `interstate75` module exposes constants for other sizes too (`DISPLAY_INTERSTATE75_128X32`, `DISPLAY_INTERSTATE75_128X64`, etc.) - see the [Pimoroni Interstate 75 docs](https://learn.pimoroni.com/article/getting-started-with-interstate-75) for the full list.

**Quirk for some 64×32 panels:** if your panel runs at 1/32 scan (a single line of the controller drives every row at once) it needs to be initialised as 64×64 to render the visible 32 rows correctly and not cut off the bottom half. Use:

```python
from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X64
DISPLAY_TYPE = DISPLAY_INTERSTATE75_64X64
```

`i75.height` will then report 64, but only the top 32 rows are physically visible. The layout in this example fits within those rows, so no other changes are needed.

#### `COLOR_ORDER`

Defaults to `Interstate75.COLOR_ORDER_RGB`. If your panel's colours look wrong (eg. text drawn as RED appears GREEN, or BLUE appears RED), try the alternative ordering:

```python
COLOR_ORDER = Interstate75.COLOR_ORDER_GRB
```

Other orderings are available on the `Interstate75` class (`COLOR_ORDER_BGR`, `COLOR_ORDER_BRG`, `COLOR_ORDER_RBG`, `COLOR_ORDER_GBR`) - try each if neither RGB nor GRB looks right. The easiest way to identify the correct one is to render a known colour (eg. the WiFi-connecting message uses WHITE on a black background, and once you see a real flight, line 1 is YELLOW, line 2 is CYAN + BLUE, and line 3 is MAGENTA).


## Pushing updates over WiFi

Once the device has been bootstrapped (via USB, as above), further iterations on `flight_display.py` and on the per-device `config.py` can be pushed over WiFi using `push.py`. The device runs a small HTTP server (`webserver.py`) on port 80 that exposes upload/download/reboot endpoints; `push.py` is the laptop-side client.

The device shows its IP address on the "Connected" screen at boot. Stash it once:

```bash
echo 192.168.1.42 > .push_host   # or: export I75_HOST=192.168.1.42
```

Then:

```bash
./push.py code                  # push flight_display.py as main.py and reboot
./push.py file dashboard.py     # push any other local .py file under its same name and reboot
./push.py file webserver.py     #   (eg. iterating on dashboard styling, webserver tweaks)
./push.py config fetch          # download device's config.py to _device/config.py
./push.py config push           # upload _device/config.py and reboot
./push.py reboot                # just reboot
```

### Editing per-device config

Per-device values (`LATITUDE`/`LONGITUDE`, `DISPLAY_TYPE`, quiet-hour settings, etc.) live on the device itself, not in the repo. The workflow is:

1. `./push.py config fetch` — pulls the device's current `config.py` into `_device/config.py` (gitignored).
2. Edit `_device/config.py` locally in your editor.
3. `./push.py config push` — uploads it back and reboots.

### Endpoints

For reference, the device exposes:

- `GET /` — HTML status dashboard (styled with [Pico CSS](https://picocss.com/) loaded from CDN; auto-refreshes every 5s). Visit `http://<device-ip>/` in a browser.
- `GET /status` — JSON: uptime, free heap, WiFi RSSI, time since last API fetch + success/error, and the currently-displayed flight. Useful as a quick "is it alive and healthy" check (`curl http://<ip>/status | jq`).
- `GET /logs` — recent `print()` output, captured via a `builtins.print` monkey-patch into a fixed-size RAM ring buffer (~4 KB). Never persisted to flash, so it imposes no storage cost regardless of run duration. Older lines are discarded as new ones arrive.
- `GET /config` — returns the device's current `config.py`
- `POST /upload?path=<filename>.py` — writes the request body to that file. The `path` is restricted to safe Python module names (alphanumeric + underscores, `.py` extension) to prevent path traversal or accidental clobbering of system files.
- `POST /reboot` — `machine.reset()` after flushing the response

The endpoints have no auth, so they assume a trusted LAN.

## Emulator

This emulator allows testing of the `flight_display.py` Micropython code without needing to connect to the actual Interstate75 hardware.

```bash
python3 emulator.py
```

<img width="468" height="319" alt="emulator" src="https://github.com/user-attachments/assets/8395d392-4acb-4cdf-810b-b613bb94ac06" />


### Test Flight Data

The emulator loads test data from `test_flight_data.json`. Edit this file to test different scenarios:

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

If nothing is displayed / the emulator quits, check the "quiet time" settings in `config.py` to ensure the current time is outside of the configured quiet period.

## Future ideas

- **Vertical speed indicator** - small climb/descent/level glyph next to the altitude (from `position.vertical_speed`). Initial attempt with `^`/`v`/`-` chars didn't render cleanly in bitmap8; a "hand-drawn" pixel arrow would likely look better.
- **Heading arrow** - small compass-direction glyph showing where the flight is pointing.
- **Cycle through N closest flights** - rotate the display through the top few via `/flights-in-radius` instead of only ever showing the single closest.
- **Button input for detail view** - use the I75's user buttons (`SWITCH_A`/`B`/`C`) to swap into a detail layout showing callsign, registration, and ground speed.
- **Special-flight highlighting** - flash colors or hold the display longer when a flight matches a watchlist (rare aircraft types, specific airlines, military callsign patterns, etc).

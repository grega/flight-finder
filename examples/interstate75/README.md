# Interstate 75 Flight Display

This MicroPython project displays nearby flight information (fetched from the Flight Finder Service API) on an [Interstate 75 W](https://shop.pimoroni.com/products/interstate-75-w?variant=54977948713339) powered LED display ([64x32 RGB LED matrix](https://shop.pimoroni.com/products/rgb-led-matrix-panel?variant=42312764298)).

Inspired by: https://blog.colinwaddell.com/articles/flight-tracker

## Setup

Follow Pimoroni's guide: https://learn.pimoroni.com/article/getting-started-with-interstate-75

(be sure to utilise the "... with filesystem" firmware)

This example was tested on Interstate 75 firmware version `0.0.5`.

Once connected to the I75 device:
  - Copy `main.py`, `flight_display.py`, `config.py`, `webserver.py`, `dashboard.py`, `config_editor.py`, `wifi_setup.py`, and `ota.py` onto the device (`main.py` is a thin boot stub that runs `flight_display.py` and falls back to a recovery webserver if it crashes - see [Recovery mode](#recovery-mode)). `./push.py all` copies the whole set for you once the device is on the network.
  - Edit `config.py`:
    - Set `API_URL` to the deployed Flight Finder Service
    - Set your location: `LATITUDE`, `LONGITUDE`, and `RADIUS`
    - Set `DISPLAY_TYPE` and `COLOR_ORDER` to match your panel - see [Display panel configuration](#display-panel-configuration) below
    - Optionally adjust the quiet time settings (show nothing on the display between these times)
      - Set `QUIET_ENABLED = False` to disable quiet time entirely (display always on)
      - Be sure to set `UTC_OFFSET` to correctly calculate quiet time based on your timezone
    - Other options include `SHOW_ALTITUDE` (cycles altitude alongside distance), `DISTANCE_UNIT`, `ALTITUDE_UNIT`, `ALTITUDE_CEILING_FT` (ignore flights above this altitude - useful to filter out cruise overflights and focus on flights arriving/departing nearby airports), and scroll/refresh timing
  - Provide WiFi credentials and the API key - **either option works, and direct file editing always remains available** (including to recover from a bad save made via the web UI):
    - **Option A - create `secrets.py` directly** (over USB, exactly as before):

      ```python
      WIFI_SSID = ""
      WIFI_PASSWORD = ""
      FLIGHT_FINDER_API_KEY = ""
      ```
    - **Option B - use the setup hotspot**: skip `secrets.py` entirely. On first boot the device starts an open WiFi hotspot named `FlightDisplay-XXXX` (shown on the LED matrix). Join it and open `http://192.168.4.1` - the setup page lets you pick a network (with scan), enter the password and API key, and it tests the credentials while the hotspot stays up, then shows the device's new LAN IP before rebooting. See [WiFi setup mode](#wifi-setup-mode) below.
  - Power-cycle the device (or run `main.py`) to start displaying flights

### WiFi setup mode

The device falls back to a provisioning hotspot ("setup mode") whenever it can't get online:

- **No credentials**: `secrets.py` missing or `WIFI_SSID` empty.
- **Connection failure**: 3 failed attempts to join the configured network (wrong password, network gone, router offline). In this case the device also **auto-retries the saved network every 60s** while in setup mode, so a transient router outage heals itself.
- **Forced**: hold the **SW_A button while powering on**.

In setup mode the matrix alternates between the hotspot name and `http://192.168.4.1`. The setup page (`/wifi`) tests submitted credentials while keeping the hotspot up - the CYW43 radio supports AP+STA concurrently - and reports the device's new LAN IP back to the page **before** you leave the hotspot, solving the "how do I find it now?" problem. Credentials are only persisted to `secrets.py` after a verified join, then the device reboots into normal operation (after 60s, or immediately via the reboot button).

Notes:
- Your phone may warn "this network has no internet" when joining the hotspot - choose to stay connected.
- The hotspot briefly drops clients while the radio hops to the target network's channel; the page rides this out, and the new IP is also shown on the LED matrix. The device also sets its hostname, so `http://flightdisplay.local/` may work depending on your OS and firmware mDNS support.
- The same page is available at `/wifi` in normal operation (linked from the dashboard) for changing networks; a save there keeps the previous credentials as a rollback copy and reboots - if the new network can't connect, the device restores them and reboots back onto the old network (landing in setup mode only if those fail too).
- Missing only the API key? The device connects to WiFi and parks on a "No API key" screen showing its IP - set the key via `/wifi`.

### Recovery mode

`main.py` is a boot stub: it runs `flight_display.main()`, and if that crashes (a broken `config.py`, a bad push) it drops into a minimal recovery webserver. In recovery the device:

- Joins WiFi with the saved credentials where possible, otherwise starts the `FlightDisplay-XXXX` hotspot (with the `/wifi` provisioning page available)
- Shows `RECOVERY` plus its IP (or the hotspot name) on the matrix
- Serves the crash traceback at `/`, and keeps `/upload` + `/reboot` working, so `./push.py` can be used

Related guard rails: `/upload` writes files atomically (temp file + rename, so a failed upload doesn't leave a corrupted file) and compile-checks `config.py` uploads on the device, rejecting a save that wouldn't import on the next boot. The main loop also self-heals, it reconnects WiFi if the connection drops and reboots after 10 consecutive failed API fetches.

Crashes are persisted to `last_crash.txt` on flash (with a timestamp) so they survive the reboot that usually follows - both a main-loop error and a fatal crash caught by the recovery stub write it. The saved traceback shows up on `/status` (as `last_crash`) and on the dashboard's Device card until dismissed, so a device that rebooted while you weren't looking can still tell you why. This matters because the `/logs` buffer is RAM-only and is lost on reboot.

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
./push.py code                  # push flight_display.py + the main.py boot stub and reboot
./push.py all                   # push every code module (not config.py/secrets.py) and reboot
./push.py file dashboard.py     # push any other local .py file under its same name and reboot
./push.py config fetch          # download device's config.py to _device/config.py
./push.py config push           # upload _device/config.py and reboot
./push.py reboot                # just reboot
```

Bump `VERSION` in `flight_display.py` when you deploy a `push.py all`. The device reports it in the API `User-Agent` header and on `/status`, so a server-side view (or a quick `curl http://<ip>/status | jq .version`) can tell which devices are running the current code - handy once several are out in the field.

### Editing per-device config

Per-device values (`LATITUDE`/`LONGITUDE`, `DISPLAY_TYPE`, quiet-hour settings, etc.) live on the device itself, not in the repo. The workflow is:

1. `./push.py config fetch` - pulls the device's current `config.py` into `_device/config.py` (gitignored).
2. Edit `_device/config.py` locally in your editor.
3. `./push.py config push` - uploads it back and reboots.

## Fleet management & over-the-air updates

`push.py` is a *push* model: it needs a route to the device (same LAN, or a tunnel), which doesn't exist once a device lives behind someone else's home router. So devices also **check in** with the Flight Finder Service on their own schedule (every ~5 min, `CHECKIN_INTERVAL_S`) via `POST /device/checkin` - a management channel separate from the flight polls that needs **no inbound access** to the device's network. Through it you can see the fleet, run remote commands, and roll out code updates.

The service-side controls live behind `ADMIN_TOKEN` (see the [service deployment docs](../../docs/dokku.md)); the device only ever makes outbound requests.

### Fleet dashboard & remote commands

`GET /fleet` (on the service, admin-only) lists every device that's checked in - version, last-seen, household + LAN IP (the LAN IP links straight to that device's dashboard), request count. Each row has buttons that queue a one-shot command, delivered on the device's next check-in:

- **Reboot** - `machine.reset()`.
- **Setup** - drop into [WiFi setup mode](#wifi-setup-mode) (typed-label confirmation, since it takes the device off the network).
- **Request logs** - the device uploads its in-RAM log buffer on the next check-in; a **logs** link then shows them (handy for debugging a device you can't reach).

Every state-changing action confirms first, so a stray click can't reset a device.

### Pushing an update to the whole fleet (canary → promote)

Updates are **pull-based**: the device compares its running `VERSION` to a target the service advertises, and when they differ it downloads the new modules, verifies them, and swaps them in. Rollout is gated so a bad build can't reach everyone at once:

1. **Bump `VERSION`** in `flight_display.py` and run **`./publish.py`**. This bundles the code modules and uploads them to the service, which stores them + a checksummed `manifest.json` and sets them as the **canary** target. (`publish.py` reads the URL from `--url`/`FLIGHT_FINDER_URL` and the admin token from `--token`/`FLIGHT_FINDER_ADMIN_TOKEN`.)
2. **Only the canary device updates.** `CANARY_DEVICE_ID` (a service env var - your own device) is the one that pulls a freshly published build. Everyone else keeps running the current fleet version. Verify the canary came up healthy on the new version via `/fleet` (and pull its logs, poke the display).
3. **Promote.** Click **Promote v… to fleet** on the `/fleet` page (confirmed) to roll the tested build out to every other device on their next check-in.

**How the device updates safely** (all reusing existing primitives):

- **Download + verify.** Each changed module is fetched to a `.ota` temp file, checked against the manifest's sha256 (or crc32 where the firmware lacks `hashlib`), and compile-checked - *all* files must pass before anything is swapped.
- **Atomic swap.** Files are `os.rename()`d into place (`main.py` last), after writing an `ota_pending` marker so an interrupted swap is still caught on the next boot.
- **Auto-rollback.** If the new code crash-loops on boot, `main.py` restores the pre-update modules from the on-device backup, records the bad version in `ota_failed.txt` (so the device won't immediately re-pull it), and comes back on the last-good version - no physical visit needed. A healthy boot commits the update and clears the backup. `main.py`'s [recovery mode](#recovery-mode) is the final net if rollback itself can't help.

Because you must bump `VERSION` to publish (the monotonic guard enforces increasing versions), and the device keys convergence on that string, updates always move forward. To ship a fix for a bad build, publish a *higher* version and promote that.

### Endpoints

For reference, the device exposes:

- `GET /` - HTML status dashboard (styled with [Pico CSS](https://picocss.com/) loaded from CDN). Served as a lightweight static shell that hydrates from `/status` JSON on first paint and then re-polls every 5s. Renders the currently-displayed flight as a hero card (origin → destination IATA codes with airport names linking to Google Maps), a mini [Leaflet](https://leafletjs.com/)/OpenStreetMap map of the aircraft's position relative to the configured home location (plane marker rotated by heading), a "Recent flights" table (from `/history`), device stats (uptime, heap, WiFi RSSI, last fetch, fetch interval), and a reboot button. The map's Leaflet assets and tiles load from CDN (like Pico CSS), and the map degrades to a message if they're unreachable. Visit `http://<device-ip>/` in a browser.
- `GET /status` - JSON: code `version`, uptime, free heap, WiFi RSSI, time since last API fetch + success/error, `last_crash` (the persisted traceback from a previous run, or `null`), and the currently-displayed flight. Useful as a quick "is it alive and healthy" check (`curl http://<ip>/status | jq`).
- `GET /history` - JSON list of recently-seen flights (newest first), each with flight number, route IATA codes, aircraft model, distance, and how long ago it was seen. Rendered as the "Recent flights" table on the dashboard.
- `GET /config-editor` - HTML form for editing the device's `config.py` from a browser (served by `config_editor.py`, imported lazily on first use). Loads the current values via `GET /config`, lets you edit the simple settings (location, units, scrolling, quiet hours, refresh interval, etc.), and on save rewrites only those values preserving comments, layout, and the `DISPLAY_TYPE`/`COLOR_ORDER` expressions, before pushing the file back via `/upload` and rebooting.
- `GET /logs` - recent `print()` output, captured via a `builtins.print` monkey-patch into a fixed-size RAM ring buffer (~4 KB). Never persisted to flash, so it imposes no storage cost regardless of run duration. Older lines are discarded as new ones arrive.
- `GET /config` - returns the device's current `config.py`
- `GET /wifi` - HTML WiFi setup page (served by `wifi_setup.py`, imported lazily; fully self-contained with no CDN assets since setup-hotspot clients have no internet). See [WiFi setup mode](#wifi-setup-mode).
- `GET /wifi/scan` - JSON list of nearby networks (SSID, RSSI, secured), deduped and sorted by signal strength.
- `GET /wifi/status` - JSON provisioning state (`mode`, `phase`, `ip`, `error`, ...); the setup page polls this while a join is being tested.
- `POST /wifi/save` - JSON `{ssid, password, api_key}`: writes `secrets.py` without testing (blank `api_key` keeps the existing key; extra hand-added lines/comments in the file are preserved) and reboots. In normal mode the previous `secrets.py` is first copied to `secrets_backup.py`; if the new network can't connect after the reboot, the device restores it automatically. In setup mode there is also `POST /wifi/connect`, which tests the credentials live before saving - it isn't registered in normal mode since it would drop the current connection.
- `POST /upload?path=<filename>.py` - writes the request body to that file. The `path` is restricted to safe Python module names (alphanumeric + underscores, `.py` extension) to prevent path traversal or accidental clobbering of system files.
- `POST /reboot` - `machine.reset()` after flushing the response
- `POST /clear-crash` - deletes the persisted `last_crash.txt` (also reachable via the "Dismiss" button on the dashboard's crash row)

The endpoints have no auth, so they assume a trusted LAN. In setup mode the trust boundary is "anyone who can join the open `FlightDisplay-XXXX` hotspot", and submitted credentials cross it as plain HTTP - setup mode is transient by design (the device reboots out of it once credentials work). `/upload`, `/logs`, and `/reboot` stay available in setup mode so `push.py` can still fix a device that's stuck there.

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

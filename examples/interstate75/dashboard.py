"""HTML rendering for the device status dashboard, served at /
"""

_PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
_REFRESH_SECONDS = 5

# Inline ~plane SVG used as the page favicon (avoids a separate file/endpoint).
_FAVICON = (
    'data:image/svg+xml,'
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<text y='.9em' font-size='90'>%E2%9C%88</text></svg>"
)

# Soft pastel palette mirroring the I75 display colors. Tuned for readable text
# on a light background (the saturated raw display values would be illegible).
# The hue mapping matches what the display uses:
#   YELLOW  -> route / IATA codes (display line 1)
#   CYAN    -> flight number (display line 2, first segment)
#   BLUE    -> distance (display line 2, distance segment)
#   MAGENTA -> aircraft model (display line 3)
#   GREEN/RED -> last-fetch OK/FAIL
_STYLES = """
:root {
  --i75-yellow: #b8901f;
  --i75-cyan:   #2f8a8a;
  --i75-blue:   #4a6fa5;
  --i75-magenta:#a14d97;
  --i75-orange: #c87333;
  --i75-green:  #5fa370;
  --i75-red:    #c66767;
}
.hero-route {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 0.75rem;
  margin: 0.5rem 0 0.5rem;
}
.hero-route .leg {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.25rem;
  min-width: 0; /* allow grid cells to shrink so long names can wrap */
}
.hero-route .iata {
  font-size: clamp(2rem, 7vw, 3rem);
  letter-spacing: 0.02em;
  color: var(--i75-yellow);
  font-weight: 700;
  line-height: 1;
}
.hero-route .iata a { color: inherit; text-decoration: none; }
.hero-route .iata a:hover { text-decoration: underline; text-underline-offset: 4px; }
.hero-route .airport-name {
  font-size: 0.9rem;
  line-height: 1.2;
}
.hero-route .airport-name a {
  color: var(--pico-muted-color);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.hero-route .arrow {
  font-size: clamp(1.5rem, 5vw, 2rem);
  color: var(--pico-muted-color);
  font-weight: 400;
  align-self: start;
  margin-top: 0.5rem; /* nudge arrow down to baseline with IATA */
}
.hero-meta { text-align: center; color: var(--pico-muted-color); margin: 0.25rem 0 0; }
.hero-meta .aircraft { color: var(--i75-magenta); font-weight: 600; }
.hero-meta .distance { color: var(--i75-blue); font-weight: 600; }
.hero-meta .altitude { color: var(--i75-orange); font-weight: 600; }
.hero-meta .vs-up    { color: var(--i75-green); }
.hero-meta .vs-down  { color: var(--i75-red); }
.hero-meta .vs-level { color: var(--pico-muted-color); }
.hero-sub-meta {
  text-align: center;
  color: var(--pico-muted-color);
  font-size: 0.85rem;
  margin: 0.2rem 0 0;
}
.fr24-link {
  text-align: center;
  font-size: 0.8rem;
  margin: 0.6rem 0 0;
}
.fr24-link a {
  color: var(--pico-muted-color);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.hero-flight-no { text-align: center; margin: 0; }
.hero-flight-no strong { color: var(--i75-cyan); font-size: 1.15rem; letter-spacing: 0.04em; }
.fetch-ok   { color: var(--i75-green); }
.fetch-fail { color: var(--i75-red); }
.dashboard-footer { display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; font-size: 0.85rem; }
.dashboard-footer a { white-space: nowrap; }
.dashboard-footer button { padding: 0.2rem 0.7rem; font-size: 0.8rem; margin: 0; width: auto; }
.refresh-badge {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  background: var(--pico-card-sectioning-background-color);
  color: var(--pico-muted-color);
  font-size: 0.7rem;
  font-weight: 400;
  vertical-align: middle;
  letter-spacing: 0.02em;
}
h1 { margin-bottom: 0.25rem; }
@media (prefers-color-scheme: dark) {
  :root {
    --i75-yellow: #e0c060;
    --i75-cyan:   #7fd1d1;
    --i75-blue:   #94adde;
    --i75-magenta:#d896c7;
    --i75-orange: #f0a060;
    --i75-green:  #88c598;
    --i75-red:    #e89090;
  }
}
"""

# Reboot via fetch() so the button is just a button (not a full-width form).
# Reloads the page after the device responds to confirm the reboot was accepted.
_REBOOT_JS = (
    "if(confirm('Reboot the device?')){"
    "fetch('/reboot',{method:'POST'})"
    ".then(()=>{document.body.style.opacity='.4';setTimeout(()=>location.reload(),3000)})"
    ".catch(e=>alert('Reboot failed: '+e))"
    "}"
)


def _rssi_label(rssi):
    if rssi is None:
        return "n/a"
    if rssi >= -50: return f"{rssi} dBm (excellent)"
    if rssi >= -65: return f"{rssi} dBm (good)"
    if rssi >= -75: return f"{rssi} dBm (fair)"
    return f"{rssi} dBm (poor)"


def _fmt_uptime(seconds):
    if seconds is None:
        return "n/a"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"


def _fmt_age(age_s):
    if age_s is None:
        return "never"
    return f"{age_s}s ago"


def _fmt_bytes(n):
    if n is None:
        return "n/a"
    return f"{n // 1024} KB" if n >= 1024 else f"{n} bytes"


def _esc(s):
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _url_quote_plus(s):
    """URL-encode a string for use in a query parameter; spaces become '+'.

    Stand-in for urllib.parse.quote_plus (not available on MicroPython).
    Safe chars pass through, spaces become +, everything else is %HH-encoded
    byte-by-byte (so UTF-8 in airport names works correctly).
    """
    if not s:
        return ""
    out = []
    for ch in s:
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in "-_.~":
            out.append(ch)
        elif ch == " ":
            out.append("+")
        else:
            for b in ch.encode("utf-8"):
                out.append("%%%02X" % b)
    return "".join(out)


def _maps_link(name):
    """Render an airport name as a link to Google Maps, or empty if unknown."""
    if not name:
        return ''
    return f'<a href="https://www.google.com/maps/search/?api=1&query={_url_quote_plus(name)}">{_esc(name)}</a>'


def _iata_link(iata):
    """Render an IATA code as a link to Google Maps (fallback when no full
    airport name is available from the API). Maps recognises eg. 'LAX airport'.
    """
    if not iata:
        return iata or ''
    query = _url_quote_plus(iata + " airport")
    return f'<a href="https://www.google.com/maps/search/?api=1&query={query}">{_esc(iata)}</a>'


def _fmt_int_with_commas(n):
    """Insert thousands separators into an integer (12000 -> '12,000')."""
    s = str(abs(int(n)))
    parts = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return ("-" if n < 0 else "") + ",".join(parts)


def _fmt_altitude(ft, unit):
    """Format altitude, converting to meters if `unit` is 'm'."""
    if ft is None:
        return ""
    if unit == "m":
        return f"{_fmt_int_with_commas(round(ft * 0.3048))} m"
    return f"{_fmt_int_with_commas(ft)} ft"


def _fmt_distance(km, unit):
    """Format distance, converting to miles if `unit` is 'mi'."""
    if km is None:
        return ""
    if unit == "mi":
        value = km * 0.621371
    else:
        value = km
    # Match flight_display's rounding: whole number above 1, 1 decimal below
    if value >= 1:
        value = round(value)
    else:
        value = round(value, 1)
    return f"{value} {unit}"


def _vertical_arrow(vs):
    """Climb/descent indicator from vertical_speed (fpm). Below |100| = level.

    Returns an HTML span with the appropriate class for color-coding, or ''
    if vertical_speed is missing.
    """
    if vs is None:
        return ""
    if vs > 100:
        return ' <span class="vs-up" title="climbing">&#x2191;</span>'
    if vs < -100:
        return ' <span class="vs-down" title="descending">&#x2193;</span>'
    return ' <span class="vs-level" title="level">&mdash;</span>'


def _compass(deg):
    """Heading in degrees -> 8-point compass label."""
    if deg is None:
        return ""
    dirs = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return dirs[int((deg + 22.5) / 45) % 8]


def _fr24_url(flight):
    """Build a FlightRadar24 URL for the flight. Callsign first (live tracking),
    falling back to flight number (historical data page)."""
    callsign = flight.get("callsign")
    if callsign:
        return f"https://www.flightradar24.com/{_url_quote_plus(callsign)}"
    fn = flight.get("flight_number")
    if fn and fn != "N/A":
        return f"https://www.flightradar24.com/data/flights/{_url_quote_plus(fn.lower())}"
    return None


def _render_flight(flight, config):
    if not flight:
        return (
            '<article>'
            '<header>Current flight</header>'
            '<p style="text-align:center;margin:1rem 0;color:var(--pico-muted-color)">'
            '<em>No flight currently displayed.</em></p>'
            '</article>'
        )
    origin_iata = flight.get("origin_iata")
    destination_iata = flight.get("destination_iata")
    origin_name = flight.get("origin_name")
    destination_name = flight.get("destination_name")

    distance_unit = config.get("distance_unit", "km")
    altitude_unit = config.get("altitude_unit", "ft")

    # Line 1: aircraft model + distance + altitude (with climb/descend arrow).
    # Each segment is omitted gracefully if the underlying field is missing.
    meta_parts = [f'<span class="aircraft">{_esc(flight.get("aircraft_model"))}</span>']
    distance_text = _fmt_distance(flight.get("distance_km"), distance_unit)
    if distance_text:
        meta_parts.append(f'<span class="distance">{distance_text}</span>')
    altitude_text = _fmt_altitude(flight.get("altitude_ft"), altitude_unit)
    if altitude_text:
        meta_parts.append(f'<span class="altitude">{altitude_text}</span>{_vertical_arrow(flight.get("vertical_speed"))}')

    # Line 2: registration / callsign / ground speed / heading. All optional;
    # if every field is missing the whole row is dropped.
    sub_parts = []
    reg = flight.get("registration")
    if reg:
        sub_parts.append(f'<span class="reg">{_esc(reg)}</span>')
    callsign = flight.get("callsign")
    if callsign:
        sub_parts.append(f'<span class="callsign">{_esc(callsign)}</span>')
    gs = flight.get("ground_speed")
    if gs is not None:
        sub_parts.append(f'<span class="speed">{int(gs)} kts</span>')
    heading = flight.get("heading")
    if heading is not None:
        sub_parts.append(f'<span class="heading">{_compass(heading)} ({int(heading)}&deg;)</span>')

    sub_meta_html = ""
    if sub_parts:
        sub_meta_html = f'<p class="hero-sub-meta">{" &middot; ".join(sub_parts)}</p>'

    fr24_url = _fr24_url(flight)
    fr24_html = ""
    if fr24_url:
        fr24_html = f'<p class="fr24-link"><a href="{fr24_url}" target="_blank" rel="noopener">&#x2197; Track on FlightRadar24</a></p>'

    return (
        '<article>'
        '<header>Current flight</header>'
        f'<p class="hero-flight-no"><strong>{_esc(flight.get("flight_number"))}</strong></p>'
        '<div class="hero-route">'
        '<div class="leg">'
        # IATA is wrapped in a link too, so it's still clickable when the API
        # didn't give us a full airport name to display below.
        f'<span class="iata">{_iata_link(origin_iata)}</span>'
        f'<span class="airport-name">{_maps_link(origin_name)}</span>'
        '</div>'
        '<span class="arrow">&rarr;</span>'
        '<div class="leg">'
        f'<span class="iata">{_iata_link(destination_iata)}</span>'
        f'<span class="airport-name">{_maps_link(destination_name)}</span>'
        '</div>'
        '</div>'
        f'<p class="hero-meta">{" &middot; ".join(meta_parts)}</p>'
        f'{sub_meta_html}'
        f'{fr24_html}'
        '</article>'
    )


def _render_device(info):
    fetch_status = "n/a"
    if info["last_fetch_ok"] is True:
        fetch_status = '<span class="fetch-ok">OK</span>'
    elif info["last_fetch_ok"] is False:
        fetch_status = f'<span class="fetch-fail">FAIL: {_esc(info["last_fetch_error"])}</span>'
    return (
        '<article>'
        '<header>Device</header>'
        '<table>'
        f'<tr><th>IP</th><td>{_esc(info["ip"])}</td></tr>'
        f'<tr><th>Uptime</th><td>{_fmt_uptime(info["uptime_s"])}</td></tr>'
        f'<tr><th>WiFi RSSI</th><td>{_rssi_label(info["rssi_dbm"])}</td></tr>'
        f'<tr><th>Free heap</th><td>{_fmt_bytes(info["free_heap_bytes"])} (alloc: {_fmt_bytes(info["alloc_heap_bytes"])})</td></tr>'
        f'<tr><th>Last fetch</th><td>{_fmt_age(info["last_fetch_age_s"])} &middot; {fetch_status}</td></tr>'
        '</table>'
        '</article>'
    )


def _render_footer():
    return (
        '<footer class="dashboard-footer">'
        '<a href="/status">/status (JSON)</a>'
        '<a href="/logs">/logs</a>'
        '<a href="/config">/config</a>'
        f'<button type="button" class="secondary outline" onclick="{_REBOOT_JS}">Reboot</button>'
        '</footer>'
    )


def render_status_html(info):
    """Render the status info dict (same shape as /status JSON) as an HTML page."""
    return (
        '<!DOCTYPE html>'
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="refresh" content="{_REFRESH_SECONDS}">'
        '<title>I75 Flight Display</title>'
        f'<link rel="icon" href="{_FAVICON}">'
        f'<link rel="stylesheet" href="{_PICO_CSS}">'
        f'<style>{_STYLES}</style>'
        '</head><body><main class="container">'
        '<header>'
        f'<h1>Interstate 75 Flight Display'
        f'<span class="refresh-badge" title="page auto-refreshes">&#x21bb; {_REFRESH_SECONDS}s</span>'
        '</h1>'
        '</header>'
        + _render_flight(info["current_flight"], info.get("config", {}))
        + _render_device(info)
        + _render_footer()
        + '</main></body></html>'
    )

"""HTML rendering for the device status dashboard, served at /
"""

_PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
_REFRESH_SECONDS = 5


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


def _render_flight(flight):
    if not flight:
        return '<article><header>Current flight</header><p><em>No flight currently displayed.</em></p></article>'
    origin = _esc(flight.get("origin_iata"))
    destination = _esc(flight.get("destination_iata"))
    origin_name = _esc(flight.get("origin_name")) or "Unknown airport"
    dest_name = _esc(flight.get("destination_name")) or "Unknown airport"
    return (
        '<article>'
        '<header>Current flight</header>'
        f'<hgroup><h2>{_esc(flight.get("flight_number"))}</h2>'
        f'<p>{_esc(flight.get("aircraft_model"))} &middot; {flight.get("distance_km")} km</p></hgroup>'
        '<table>'
        f'<tr><th>From</th><td><strong>{origin}</strong> &mdash; <small>{origin_name}</small></td></tr>'
        f'<tr><th>To</th><td><strong>{destination}</strong> &mdash; <small>{dest_name}</small></td></tr>'
        '</table>'
        '</article>'
    )


def _render_device(info):
    fetch_status = "n/a"
    if info["last_fetch_ok"] is True:
        fetch_status = f'<span style="color:var(--pico-color-green-500)">OK</span>'
    elif info["last_fetch_ok"] is False:
        fetch_status = f'<span style="color:var(--pico-color-red-500)">FAIL: {_esc(info["last_fetch_error"])}</span>'
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


def render_status_html(info):
    """Render the status info dict (same shape as /status JSON) as an HTML page."""
    return (
        '<!DOCTYPE html>'
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="refresh" content="{_REFRESH_SECONDS}">'
        '<title>I75 Flight Display</title>'
        f'<link rel="stylesheet" href="{_PICO_CSS}">'
        '</head><body><main class="container">'
        '<header><h1>Interstate 75 Flight Display</h1></header>'
        + _render_flight(info["current_flight"])
        + _render_device(info)
        + '<footer><small>'
        '<a href="/status">/status (JSON)</a> &middot; '
        '<a href="/logs">/logs</a> &middot; '
        '<a href="/config">/config</a> &middot; '
        '<form style="display:inline" method="post" action="/reboot" '
        'onsubmit="return confirm(\'Reboot the device?\')">'
        '<button type="submit" class="secondary outline" '
        'style="display:inline;padding:0.2rem 0.6rem;font-size:0.8rem">Reboot</button>'
        '</form>'
        f' &middot; auto-refreshes every {_REFRESH_SECONDS}s'
        '</small></footer>'
        '</main></body></html>'
    )

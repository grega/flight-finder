"""HTML rendering for the device status dashboard, served at /

The page is a static shell that hydrates itself from /status JSON: once on first
paint (from data embedded in the page, so there's no extra round-trip) and then
on a timer. All formatting/rendering lives in the browser (the JS below), so the
device only ever serializes a small JSON snapshot per poll instead of rebuilding
~10KB of HTML every few seconds. The previous server-side renderers (and their
per-request string building) are gone as a result.
"""

import json

_PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
_REFRESH_SECONDS = 5

# Inline ~plane SVG used as the page favicon (avoids a separate file/endpoint)
_FAVICON = (
    'data:image/svg+xml,'
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<text y='.9em' font-size='90'>%E2%9C%88</text></svg>"
)

# Palette mirroring the I75 display colors
#   YELLOW    -> route / IATA codes (display line 1)
#   CYAN      -> flight number (display line 2, first segment)
#   BLUE      -> distance (display line 2, distance segment)
#   MAGENTA   -> aircraft model (display line 3)
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
.flight-row { display: flex; gap: 0.75rem; align-items: stretch; }
.flight-main { flex: 1 1 auto; min-width: 0; }
.refresh-bar {
  position: relative;
  flex: 0 0 8px;
  align-self: stretch;
  min-height: 64px;
  background: var(--pico-card-sectioning-background-color);
  border-radius: 999px;
  overflow: hidden;
}
.refresh-bar-fill {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: var(--i75-green);
  transform-origin: bottom center;
  transform: scaleY(0); /* set live by tickBar() */
}
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
.refresh-badge.offline { color: var(--i75-red); }
.dash-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem 1.5rem;
  flex-wrap: wrap;
}
.dash-header .edit-config {
  width: auto;
  margin: 0 0 0.5rem;
  white-space: nowrap;
  padding: 0.4rem 0.8rem;
  font-size: 0.8rem;
}
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

_SCRIPT = """
(function(){
  "use strict";
  var REFRESH_MS = window.__REFRESH_MS__ || 5000;

  // Last-known airport name per IATA code. The API sometimes returns a flight
  // with only the IATA code and no resolved airport name; without this the name
  // under the code would blank out on that poll. Remember names we've seen and
  // reuse them for the same code so the panel stays populated.
  var nameCache = {};
  function resolveName(iata, name){
    if(name){ if(iata) nameCache[iata] = name; return name; }
    return iata && nameCache[iata] ? nameCache[iata] : '';
  }

  function esc(s){
    if(s==null) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  // URL-encode for a query param; spaces -> '+'. encodeURIComponent leaves
  // -_.!~*'() unescaped, which Google Maps / FR24 handle fine.
  function urlQuotePlus(s){ return s ? encodeURIComponent(s).replace(/%20/g,'+') : ''; }

  function mapsLink(name){
    if(!name) return '';
    return '<a href="https://www.google.com/maps/search/?api=1&query='+urlQuotePlus(name)+'">'+esc(name)+'</a>';
  }
  function iataLink(iata){
    if(!iata) return iata || '';
    var q = urlQuotePlus(iata + ' airport');
    return '<a href="https://www.google.com/maps/search/?api=1&query='+q+'">'+esc(iata)+'</a>';
  }
  function fmtIntWithCommas(n){ return Math.trunc(n).toLocaleString('en-US'); }
  function fmtAltitude(ft, unit){
    if(ft==null) return '';
    if(unit==='m') return fmtIntWithCommas(Math.round(ft*0.3048))+' m';
    return fmtIntWithCommas(ft)+' ft';
  }
  function fmtDistance(km, unit){
    if(km==null) return '';
    var v = unit==='mi' ? km*0.621371 : km;
    v = v>=1 ? Math.round(v) : Math.round(v*10)/10;
    return v+' '+unit;
  }
  function verticalArrow(vs){
    if(vs==null) return '';
    if(vs>100) return ' <span class="vs-up" title="climbing">&#x2191;</span>';
    if(vs<-100) return ' <span class="vs-down" title="descending">&#x2193;</span>';
    return ' <span class="vs-level" title="level">&mdash;</span>';
  }
  function compass(deg){
    if(deg==null) return '';
    var dirs=['N','NE','E','SE','S','SW','W','NW'];
    return dirs[Math.floor((deg+22.5)/45)%8];
  }
  function fr24Url(f){
    if(f.callsign) return 'https://www.flightradar24.com/'+urlQuotePlus(f.callsign);
    var fn = f.flight_number;
    if(fn && fn!=='N/A') return 'https://www.flightradar24.com/data/flights/'+urlQuotePlus(fn.toLowerCase());
    return null;
  }
  function rssiLabel(r){
    if(r==null) return 'n/a';
    if(r>=-50) return r+' dBm (excellent)';
    if(r>=-65) return r+' dBm (good)';
    if(r>=-75) return r+' dBm (fair)';
    return r+' dBm (poor)';
  }
  function fmtUptime(s){
    if(s==null) return 'n/a';
    var h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
    if(h) return h+'h '+m+'m '+sec+'s';
    if(m) return m+'m '+sec+'s';
    return sec+'s';
  }
  function fmtAge(a){ return a==null ? 'never' : a+'s ago'; }
  function fmtBytes(n){
    if(n==null) return 'n/a';
    return n>=1024 ? Math.floor(n/1024)+' KB' : n+' bytes';
  }

  function renderFlight(f, config){
    if(!f){
      return '<p style="text-align:center;margin:1rem 0;color:var(--pico-muted-color)">'+
        '<em>No flight currently displayed.</em></p>';
    }
    var distUnit = (config && config.distance_unit) || 'km';
    var altUnit  = (config && config.altitude_unit) || 'ft';

    var meta = ['<span class="aircraft">'+esc(f.aircraft_model)+'</span>'];
    var d = fmtDistance(f.distance_km, distUnit);
    if(d) meta.push('<span class="distance">'+d+'</span>');
    var a = fmtAltitude(f.altitude_ft, altUnit);
    if(a) meta.push('<span class="altitude">'+a+'</span>'+verticalArrow(f.vertical_speed));

    var sub = [];
    if(f.registration) sub.push('<span class="reg">'+esc(f.registration)+'</span>');
    if(f.callsign) sub.push('<span class="callsign">'+esc(f.callsign)+'</span>');
    if(f.ground_speed!=null) sub.push('<span class="speed">'+Math.trunc(f.ground_speed)+' kts</span>');
    if(f.heading!=null) sub.push('<span class="heading">'+compass(f.heading)+' ('+Math.trunc(f.heading)+'&deg;)</span>');
    var subHtml = sub.length ? '<p class="hero-sub-meta">'+sub.join(' &middot; ')+'</p>' : '';

    var url = fr24Url(f);
    var fr24 = url ? '<p class="fr24-link"><a href="'+url+'" target="_blank" rel="noopener">&#x2197; Track on FlightRadar24</a></p>' : '';

    var originName = resolveName(f.origin_iata, f.origin_name);
    var destName = resolveName(f.destination_iata, f.destination_name);

    return '<p class="hero-flight-no"><strong>'+esc(f.flight_number)+'</strong></p>'+
      '<div class="hero-route">'+
      '<div class="leg">'+
      '<span class="iata">'+iataLink(f.origin_iata)+'</span>'+
      '<span class="airport-name">'+mapsLink(originName)+'</span>'+
      '</div>'+
      '<span class="arrow">&rarr;</span>'+
      '<div class="leg">'+
      '<span class="iata">'+iataLink(f.destination_iata)+'</span>'+
      '<span class="airport-name">'+mapsLink(destName)+'</span>'+
      '</div>'+
      '</div>'+
      '<p class="hero-meta">'+meta.join(' &middot; ')+'</p>'+
      subHtml+fr24;
  }

  function renderDevice(info){
    var fetch = 'n/a';
    if(info.last_fetch_ok===true) fetch='<span class="fetch-ok">OK</span>';
    else if(info.last_fetch_ok===false) fetch='<span class="fetch-fail">FAIL: '+esc(info.last_fetch_error)+'</span>';
    var interval = (info.config && info.config.refresh_interval_s!=null)
      ? 'every '+info.config.refresh_interval_s+'s' : 'n/a';
    return '<header>Device</header>'+
      '<table>'+
      '<tr><th>IP</th><td>'+esc(info.ip)+'</td></tr>'+
      '<tr><th>Uptime</th><td>'+fmtUptime(info.uptime_s)+'</td></tr>'+
      '<tr><th>WiFi RSSI</th><td>'+rssiLabel(info.rssi_dbm)+'</td></tr>'+
      '<tr><th>Free heap</th><td>'+fmtBytes(info.free_heap_bytes)+' (alloc: '+fmtBytes(info.alloc_heap_bytes)+')</td></tr>'+
      '<tr><th>Last fetch</th><td>'+fmtAge(info.last_fetch_age_s)+' &middot; '+fetch+'</td></tr>'+
      '<tr><th>Fetch interval</th><td>'+interval+'</td></tr>'+
      '</table>';
  }

  function render(info){
    document.getElementById('flight-content').innerHTML = renderFlight(info.current_flight, info.config || {});
    document.getElementById('device-card').innerHTML = renderDevice(info);
    syncCountdown(info);
  }

  // Mirror the display's green countdown bar: a vertical bar beside the flight
  // card that depletes over the fetch interval. Driven by /status
  // (last_fetch_age_s + refresh_interval_s) but animated locally so it ticks
  // smoothly between the 5s polls.
  var fetchAtMs = null, intervalS = null;
  function syncCountdown(info){
    var age = info.last_fetch_age_s;
    var iv = info.config && info.config.refresh_interval_s;
    if(age == null || !iv){ fetchAtMs = null; intervalS = null; return; }
    intervalS = iv;
    var at = Date.now() - age * 1000;
    // Only resync on a real change (a new fetch resets age, or genuine drift);
    // ignore the sub-second jitter from age being whole seconds so the bar
    // doesn't twitch on every poll.
    if(fetchAtMs == null || Math.abs(at - fetchAtMs) > 2000) fetchAtMs = at;
  }
  function tickBar(){
    var fill = document.getElementById('refresh-bar-fill');
    var bar = document.getElementById('refresh-bar');
    if(!fill) return;
    if(fetchAtMs == null || !intervalS){ fill.style.transform = 'scaleY(0)'; return; }
    var remaining = intervalS - (Date.now() - fetchAtMs) / 1000;
    var frac = remaining / intervalS;
    if(frac < 0) frac = 0; else if(frac > 1) frac = 1;
    fill.style.transform = 'scaleY(' + frac + ')';
    if(bar) bar.title = 'Next refresh in ' + Math.max(0, Math.ceil(remaining)) + 's';
  }

  var badge;
  function setBadge(text, offline){
    badge = badge || document.getElementById('refresh-badge');
    if(!badge) return;
    badge.textContent = text;
    badge.classList.toggle('offline', !!offline);
  }

  function refresh(){
    fetch('/status', {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
      .then(function(info){ render(info); setBadge('\\u21bb live', false); })
      .catch(function(){ setBadge('\\u26a0 offline', true); });
  }

  window.reboot = function(){
    if(confirm('Reboot the device?')){
      fetch('/reboot',{method:'POST'})
        .then(function(){ document.body.style.opacity='.4'; setTimeout(function(){location.reload();}, 3000); })
        .catch(function(e){ alert('Reboot failed: '+e); });
    }
  };

  // First paint from data embedded in the page (no round-trip), then poll.
  if(window.__INITIAL__) render(window.__INITIAL__);
  setInterval(refresh, REFRESH_MS);
  tickBar();
  setInterval(tickBar, 250);
})();
"""

# Static parts, assembled once at import (only the embedded JSON varies per request)
_HEAD = (
    '<!DOCTYPE html>'
    '<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>I75 Flight Display</title>'
    f'<link rel="icon" href="{_FAVICON}">'
    f'<link rel="stylesheet" href="{_PICO_CSS}">'
    f'<style>{_STYLES}</style>'
    '</head><body><main class="container">'
    '<header class="dash-header">'
    '<h1>Interstate 75 Flight Display'
    '<span class="refresh-badge" id="refresh-badge" title="auto-updates via /status">&#x21bb; live</span>'
    '</h1>'
    '<a href="/config-editor" role="button" class="edit-config">&#9881; Edit config</a>'
    '</header>'
    '<article id="flight-card">'
    '<header>Current flight</header>'
    '<div class="flight-row">'
    '<div id="flight-content" class="flight-main"></div>'
    '<div class="refresh-bar" id="refresh-bar" title="Time until next flight data refresh">'
    '<div class="refresh-bar-fill" id="refresh-bar-fill"></div>'
    '</div>'
    '</div>'
    '</article>'
    '<article id="device-card"></article>'
    '<footer class="dashboard-footer">'
    '<a href="/status">/status (JSON)</a>'
    '<a href="/logs">/logs</a>'
    '<a href="/config">/config</a>'
    '<button type="button" class="secondary outline" onclick="reboot()">Reboot</button>'
    '</footer>'
    '</main>'
)


def render_status_html(info):
    """Render the page shell with the status info (same shape as /status JSON)
    embedded for first paint. All further updates are client-side via /status."""
    # Escape '<' so the embedded JSON can never terminate the <script> early or inject markup (json.dumps does not escape '<' by default)
    initial = json.dumps(info).replace("<", "\\u003c")
    return (
        _HEAD
        + '<script>window.__INITIAL__=' + initial
        + ';window.__REFRESH_MS__=' + str(_REFRESH_SECONDS * 1000) + ';</script>'
        + '<script>' + _SCRIPT + '</script>'
        + '</body></html>'
    )

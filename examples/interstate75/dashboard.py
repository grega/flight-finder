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
_LEAFLET_CSS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"
_LEAFLET_JS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"
_REFRESH_SECONDS = 5

# Inline "plane" SVG used as the favicon
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
table.history { font-size: 0.85rem; margin: 0; }
table.history th, table.history td { padding: 0.3rem 0.5rem; }
table.history .route { white-space: nowrap; }
.map-details { margin-bottom: 0; }
#map-card { padding: 0; overflow: hidden; }
#map-details summary { cursor: pointer; padding: 1rem; font-weight: 600; }
#map-details summary:hover { background: var(--pico-card-sectioning-background-color); }
#map-details[open] summary { border-bottom: 1px solid var(--pico-muted-border-color); }
#map { height: 480px; margin: 1rem; border-radius: 8px; z-index: 0; }
#map-card .no-pos { color: var(--pico-muted-color); text-align: center; margin: 1rem; }
/* Insulate Leaflet from Pico: its zoom buttons are <a role="button"> and its
   markers are focusable, both of which Pico would otherwise style/box. */
.leaflet-control-zoom a {
  box-sizing: border-box;
  width: 30px; height: 30px; line-height: 30px;
  padding: 0; font-size: 1.2rem; font-weight: 700;
  background: #fff; color: #333; box-shadow: none; text-decoration: none;
}
.leaflet-control-zoom a:hover { background: #f4f4f4; }
.leaflet-marker-icon, .leaflet-marker-icon:focus { background: none; border: none; box-shadow: none; outline: none; }
.plane-icon svg { display: block; transform-origin: center; filter: drop-shadow(0 0 2px rgba(0,0,0,0.85)); }
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
  // Historical flight page (by flight number). Used for the history table, where
  // entries may have landed - unlike the live callsign URL used for the current flight.
  function fr24FlightUrl(fn){
    return (fn && fn !== 'N/A') ? 'https://www.flightradar24.com/data/flights/'+urlQuotePlus(fn.toLowerCase()) : null;
  }
  function fr24Url(f){
    if(f.callsign) return 'https://www.flightradar24.com/'+urlQuotePlus(f.callsign);
    return fr24FlightUrl(f.flight_number);
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

  var lastConfig = {}, lastFlightNo = false;
  function render(info){
    document.getElementById('flight-content').innerHTML = renderFlight(info.current_flight, info.config || {});
    document.getElementById('device-card').innerHTML = renderDevice(info);
    syncCountdown(info);
    lastConfig = info.config || {};
    // Refresh the history table when the current flight changes (and on first render).
    var fn = info.current_flight && info.current_flight.flight_number;
    var flightChanged = (fn !== lastFlightNo);
    if(flightChanged){ lastFlightNo = fn; refreshHistory(); }
    updateMap(info, flightChanged);
  }

  function fmtAgo(s){
    if(s == null) return '';
    if(s < 60) return s + 's ago';
    if(s < 3600) return Math.floor(s / 60) + 'm ago';
    return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm ago';
  }

  function renderHistory(items){
    var card = document.getElementById('history-card');
    if(!card) return;
    if(!items || !items.length){
      card.innerHTML = '<header>Recent flights</header>'+
        '<p style="color:var(--pico-muted-color)"><em>No flights recorded yet.</em></p>';
      return;
    }
    var unit = lastConfig.distance_unit || 'km';
    var rows = items.map(function(f){
      // Link the flight number to FlightRadar24's historical flight page (by
      // number) - these may have landed, so the live callsign URL isn't right here.
      var url = fr24FlightUrl(f.flight_number);
      var flightCell = url
        ? '<a href="'+url+'" target="_blank" rel="noopener">'+esc(f.flight_number)+'</a>'
        : esc(f.flight_number);
      return '<tr>'+
        '<td>'+fmtAgo(f.age_s)+'</td>'+
        '<td>'+flightCell+'</td>'+
        '<td class="route">'+esc(f.origin_iata)+' &rarr; '+esc(f.destination_iata)+'</td>'+
        '<td>'+esc(f.aircraft_model)+'</td>'+
        '<td>'+esc(f.registration)+'</td>'+
        '<td>'+fmtDistance(f.distance_km, unit)+'</td>'+
        '</tr>';
    }).join('');
    card.innerHTML = '<header>Recent flights</header>'+
      '<table class="history"><thead><tr>'+
      '<th>Seen</th><th>Flight</th><th>Route</th><th>Aircraft</th><th>Reg</th><th>Dist</th>'+
      '</tr></thead><tbody>'+rows+'</tbody></table>';
  }

  function refreshHistory(){
    fetch('/history', {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
      .then(function(d){ renderHistory(d.flights || []); })
      .catch(function(){ /* keep last table; the live badge already shows offline */ });
  }

  // --- Mini map (Leaflet/OpenStreetMap) -----------------------------------
  var map = null, planeMarker = null, homeMarker = null, mapFramed = false, mapWarned = false, lastMapInfo = null;

  function planeIcon(heading){
    var rot = (heading == null) ? 0 : heading;
    var size = 38;
    // Plane drawn pointing north (up); rotated clockwise by the heading. Bright
    // fill + thick white outline (and a dark drop-shadow via CSS) keep it legible
    // over any map background.
    var svg = '<svg viewBox="0 0 24 24" width="'+size+'" height="'+size+'" style="transform:rotate('+rot+'deg)">'+
      '<path fill="#e11d2a" stroke="#fff" stroke-width="1.4" stroke-linejoin="round" d="M12 2 L13.4 9 L22 13 L22 14.6 L13.4 12.6 L13 18.5 L15.6 20.4 L15.6 21.6 L12 20.6 L8.4 21.6 L8.4 20.4 L11 18.5 L10.6 12.6 L2 14.6 L2 13 L10.6 9 Z"/>'+
      '</svg>';
    return L.divIcon({html: svg, className: 'plane-icon', iconSize: [size, size], iconAnchor: [size / 2, size / 2]});
  }

  function ensureMap(cfg){
    if(map || typeof L === 'undefined') return map;
    var el = document.getElementById('map');
    if(!el) return null;
    map = L.map(el);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 13, attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
    if(cfg.home_lat != null && cfg.home_lon != null){
      homeMarker = L.circleMarker([cfg.home_lat, cfg.home_lon],
        {radius: 6, color: '#fff', weight: 2, fillColor: '#4a6fa5', fillOpacity: 1}).addTo(map);
      homeMarker.bindTooltip('Home');
      map.setView([cfg.home_lat, cfg.home_lon], 9);
    } else {
      map.setView([0, 0], 2);
    }
    // The container is laid out after creation; recompute tile sizing.
    setTimeout(function(){ if(map) map.invalidateSize(); }, 100);
    return map;
  }

  function updateMap(info, flightChanged){
    if(info) lastMapInfo = info;
    // Only build/update the map while the panel is open
    var details = document.getElementById('map-details');
    if(!details || !details.open) return;
    info = lastMapInfo || {};
    if(typeof L === 'undefined'){
      var card = document.getElementById('map-card');
      if(card && !mapWarned){
        mapWarned = true;
        card.innerHTML = '<header>Position</header>'+
          '<p class="no-pos"><em>Map unavailable (could not load Leaflet).</em></p>';
      }
      return;
    }
    var cfg = info.config || {};
    var f = info.current_flight;
    ensureMap(cfg);
    if(!map) return;
    var hasPos = f && f.latitude != null && f.longitude != null;
    if(!hasPos){
      if(planeMarker){ map.removeLayer(planeMarker); planeMarker = null; }
      return;
    }
    var ll = [f.latitude, f.longitude];
    if(!planeMarker){
      planeMarker = L.marker(ll, {icon: planeIcon(f.heading), zIndexOffset: 1000, keyboard: false}).addTo(map);
    } else {
      planeMarker.setLatLng(ll);
      planeMarker.setIcon(planeIcon(f.heading));
    }
    planeMarker.bindTooltip(f.flight_number || 'Aircraft');
    // Frame home + aircraft when a flight first appears or changes; otherwise just
    // move the marker so the view doesn't jump on every position update.
    if(flightChanged || !mapFramed){
      var pts = [ll];
      if(cfg.home_lat != null && cfg.home_lon != null) pts.push([cfg.home_lat, cfg.home_lon]);
      if(pts.length > 1) map.fitBounds(pts, {padding: [30, 30], maxZoom: 11});
      else map.setView(ll, 10);
      mapFramed = true;
    }
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
  // Refresh history periodically too, so the "seen N ago" ages stay current even while the same flight remains closest
  setInterval(refreshHistory, 30000);
  // Build/redraw the (collapsed-by-default) map when expanded; invalidateSize makes
  // Leaflet recompute tiles now that the container has real dimensions.
  var mapDetails = document.getElementById('map-details');
  if(mapDetails) mapDetails.addEventListener('toggle', function(){
    if(!mapDetails.open) return;
    updateMap(lastMapInfo, true);
    setTimeout(function(){ if(map) map.invalidateSize(); }, 60);
  });
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
    f'<link rel="stylesheet" href="{_LEAFLET_CSS}">'
    f'<style>{_STYLES}</style>'
    # Loaded in <head> without defer so window.L is available when the inline
    # script (end of body) runs. Map rendering degrades gracefully if it's blocked.
    f'<script src="{_LEAFLET_JS}"></script>'
    '</head><body><main class="container">'
    '<header class="dash-header">'
    '<h1>Interstate 75 Flight Display'
    '<span class="refresh-badge" id="refresh-badge" title="auto-updates via /status">&#x21bb; live</span>'
    '</h1>'
    '<a href="/config-editor" role="button" class="edit-config">Edit config</a>'
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
    '<article id="map-card">'
    '<details class="map-details" id="map-details">'
    '<summary>Position</summary>'
    '<div id="map"></div>'
    '</details>'
    '</article>'
    '<article id="device-card"></article>'
    '<article id="history-card"></article>'
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

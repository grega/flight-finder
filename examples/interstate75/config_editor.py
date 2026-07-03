"""Web-based editor for the device's config.py, served at /config-editor.

The page fetches the raw config.py via GET /config, presents
a form for the simple literal settings, and on save rewrites only those values
(leaving comments, layout, imports and the DISPLAY_TYPE/COLOR_ORDER expressions
untouched) before POSTing back via /upload?path=config.py and /reboot.

A collapsible map picker (Leaflet + OpenStreetMap tiles + Nominatim address
search) helps fill LATITUDE/LONGITUDE. It runs entirely in the visitor's
browser and is only fetched from CDN when the section is first opened.
"""

_PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
_LEAFLET_CSS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"
_LEAFLET_JS = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"
_FAVICON = (
    'data:image/svg+xml,'
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<text y='.9em' font-size='90'>%E2%9C%88</text></svg>"
)

_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>I75 Config Editor</title>
<link rel="icon" href="__FAVICON__">
<link rel="stylesheet" href="__PICO__">
<style>
h2 { margin: 1.4rem 0 0.5rem; padding-top: 1rem; border-top: 1px solid var(--pico-muted-border-color); color: var(--pico-muted-color); font-size: 1.25rem; }
#cfg h2:first-child { margin-top: 0; padding-top: 0; border-top: none; }
.field { margin-bottom: 0.7rem; }
.field label { font-size: 0.875rem; }
.field small { display: block; color: var(--pico-muted-color); }
.field input[type="text"], .field input[type="number"], .field select { font-size: 0.875rem; }
.utc-hint { font-variant-numeric: tabular-nums; }
.actions { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 1.2rem; }
.actions button { width: auto; margin: 0; }
.page-header { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem 1.5rem; flex-wrap: wrap; }
details pre { white-space: pre-wrap; font-size: 0.72rem; max-height: 14rem; overflow: auto; }
summary[role="button"] { font-size: 0.875rem; }
#map { height: 280px; margin: 0.6rem 0; border-radius: var(--pico-border-radius); }
.map-row { display: flex; gap: 0.6rem; align-items: center; }
.map-row input { margin: 0; }
.map-row button { width: auto; margin: 0; }
#map-results { font-size: 0.8rem; }
#map-results a { display: block; margin: 0.2rem 0; }
#map-coords { color: var(--pico-muted-color); font-variant-numeric: tabular-nums; }
/* Leaflet marks its zoom links and marker icons with role="button" (for a11y), which
   Pico then paints as buttons (padding, blue border, filled background). Undo that
   inside the map only, restoring Leaflet's stock look. */
#map [role="button"] { padding: 0; margin: 0; border: none; border-radius: 0; box-shadow: none; }
#map img[role="button"] { background: none; }
#map .leaflet-bar a { background-color: #fff; color: #000; border-radius: 2px; }
#map .leaflet-bar a:not(:last-child) { border-bottom: 1px solid #ccc; }
@keyframes field-flash {
  0% { background-color: rgba(46, 160, 67, 0.35); border-color: rgba(46, 160, 67, 0.9); }
  100% { background-color: transparent; }
}
.field-flash { animation: field-flash 1.6s ease-out; }
</style>
</head><body><main class="container">
<header class="page-header">
<h1>Config editor</h1>
<a href="/">&larr; Back to dashboard</a>
</header>
<article>
<form id="cfg"></form>
<div id="status">Loading…</div>
<div class="actions">
<button id="save">Save &amp; reboot</button>
<button id="reload" class="secondary outline">Reload from device</button>
</div>
<br/>
<details><summary role="button" class="outline secondary">Raw config.py</summary><pre id="raw"></pre></details>
</article>
</main>
<script>
(function(){
  "use strict";

  // Only simple literal settings are editable here. DISPLAY_TYPE/COLOR_ORDER are
  // Python expressions and the imports are structural, so they're left to push.py.
  var FIELDS = [
    {section:"Location"},
    {key:"LATITUDE",  label:"Latitude",  type:"number", step:"any", min:"-90", max:"90"},
    {key:"LONGITUDE", label:"Longitude", type:"number", step:"any", min:"-180", max:"180"},
    {map:true},
    {key:"RADIUS",    label:"Radius (km)", type:"number", step:"any", min:"1", max:"500",
     help:"1-500 km."},
    {section:"Display"},
    {key:"BRIGHT_MODE",   label:"Bright mode", type:"bool"},
    {key:"SHOW_ALTITUDE", label:"Cycle altitude & distance", type:"bool"},
    {key:"DISTANCE_UNIT", label:"Distance unit", type:"select", options:["km","mi"]},
    {key:"ALTITUDE_UNIT", label:"Altitude unit", type:"select", options:["ft","m"]},
    {key:"VALUE_SWAP_INTERVAL", label:"Distance/altitude swap interval (s)", type:"number", step:"1", min:"1"},
    {key:"ALTITUDE_CEILING_FT", label:"Altitude ceiling (ft)", type:"number_or_none", step:"1", min:"0",
     help:"Ignore flights above this altitude. Blank = no ceiling."},
    {section:"Fetching"},
    {key:"REFRESH_INTERVAL", label:"Refresh interval (s)", type:"number", step:"1", min:"30",
     help:"How often new flight data is fetched. 30s or more."},
    {key:"API_URL",       label:"API URL", type:"url"},
    {key:"USER_AGENT_ID", label:"User-agent ID", type:"string"},
    {section:"Scrolling"},
    {key:"SCROLL_ENABLED",          label:"Scroll long text", type:"bool"},
    {key:"SCROLL_PAUSE_MS",         label:"Scroll pause (ms)", type:"number", step:"1", min:"0"},
    {key:"SCROLL_SPEED_PX_PER_SEC", label:"Scroll speed (px/s)", type:"number", step:"1", min:"1"},
    {section:"Quiet time"},
    {key:"QUIET_ENABLED",      label:"Enable quiet time", type:"bool", "default":true},
    {key:"UTC_OFFSET",         label:"UTC offset (hours)", type:"number", step:"0.25", min:"-12", max:"14"},
    {key:"QUIET_START_HOUR",   label:"Quiet start hour", type:"number", step:"1", min:"0", max:"23"},
    {key:"QUIET_START_MINUTE", label:"Quiet start minute", type:"number", step:"1", min:"0", max:"59"},
    {key:"QUIET_END_HOUR",     label:"Quiet end hour", type:"number", step:"1", min:"0", max:"23"},
    {key:"QUIET_END_MINUTE",   label:"Quiet end minute", type:"number", step:"1", min:"0", max:"59"}
  ];

  var originalText = "";
  var form = document.getElementById("cfg");
  var statusEl = document.getElementById("status");

  function setStatus(msg, kind){ statusEl.textContent = msg; statusEl.className = kind || ""; }

  function el(tag, attrs, text){
    var e = document.createElement(tag);
    if(attrs){ for(var k in attrs) e.setAttribute(k, attrs[k]); }
    if(text != null) e.textContent = text;
    return e;
  }

  function buildForm(){
    form.innerHTML = "";
    FIELDS.forEach(function(f){
      if(f.section){ form.appendChild(el("h2", null, f.section)); return; }
      if(f.map){ form.appendChild(buildMapPicker()); return; }
      var wrap = el("div", {"class":"field"});
      var id = "f_" + f.key;
      if(f.type === "bool"){
        var lab = el("label", {"for":id});
        var cb = el("input", {type:"checkbox", id:id, role:"switch"});
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" " + f.label));
        wrap.appendChild(lab);
      } else if(f.type === "select"){
        wrap.appendChild(el("label", {"for":id}, f.label));
        var sel = el("select", {id:id});
        f.options.forEach(function(o){ sel.appendChild(el("option", {value:o}, o)); });
        wrap.appendChild(sel);
      } else {
        wrap.appendChild(el("label", {"for":id}, f.label));
        var attrs = {id:id};
        if(f.type === "number" || f.type === "number_or_none"){
          attrs.type = "number"; attrs.step = f.step || "1";
          if(f.min != null) attrs.min = f.min; if(f.max != null) attrs.max = f.max;
          // blank is only meaningful for number_or_none (saved as None); a blank plain
          // number would be written as "KEY = " which breaks config.py
          if(f.type === "number") attrs.required = "";
        } else {
          attrs.type = (f.type === "url") ? "url" : "text";
          if(f.type === "url") attrs.required = "";
        }
        wrap.appendChild(el("input", attrs));
      }
      if(f.help) wrap.appendChild(el("small", null, f.help));
      // Live UTC clock under the offset field, as a reminder for setting it.
      if(f.key === "UTC_OFFSET") wrap.appendChild(el("small", {id:"utc-now", "class":"utc-hint"}));
      form.appendChild(wrap);
    });
  }

  // Map-based lat/lon picker: Leaflet + OSM tiles + Nominatim address search.
  // Leaflet is only fetched from CDN the first time the section is opened.
  var map = null, marker = null;

  function buildMapPicker(){
    var d = el("details", {id:"map-details"});
    d.appendChild(el("summary", {role:"button", "class":"outline secondary"}, "Pick location on a map"));
    var row = el("div", {"class":"map-row"});
    var q = el("input", {id:"map-q", type:"search", placeholder:"Search for an address…"});
    var go = el("button", {type:"button", "class":"secondary"}, "Search");
    row.appendChild(q); row.appendChild(go);
    d.appendChild(row);
    d.appendChild(el("div", {id:"map-results"}));
    d.appendChild(el("div", {id:"map"}));
    var foot = el("div", {"class":"map-row"});
    var use = el("button", {type:"button"}, "Use this location");
    foot.appendChild(use);
    foot.appendChild(el("small", {id:"map-coords"}));
    d.appendChild(foot);

    q.addEventListener("keydown", function(ev){ if(ev.key === "Enter"){ ev.preventDefault(); searchAddress(); } });
    go.addEventListener("click", searchAddress);
    use.addEventListener("click", useMapLocation);
    d.addEventListener("toggle", function(){
      if(!d.open) return;
      if(map){ map.invalidateSize(); return; }
      loadLeaflet(initMap);
    });
    return d;
  }

  function loadLeaflet(onReady){
    document.head.appendChild(el("link", {rel:"stylesheet", href:"__LEAFLET_CSS__"}));
    var s = document.createElement("script");
    s.src = "__LEAFLET_JS__";
    s.onload = onReady;
    s.onerror = function(){ setStatus("Failed to load the map library from CDN", "err"); };
    document.head.appendChild(s);
  }

  function updateCoordsReadout(){
    var ll = marker.getLatLng().wrap();
    document.getElementById("map-coords").textContent = ll.lat.toFixed(6) + ", " + ll.lng.toFixed(6);
  }

  function initMap(){
    var lat = parseFloat(document.getElementById("f_LATITUDE").value);
    var lon = parseFloat(document.getElementById("f_LONGITUDE").value);
    var have = !isNaN(lat) && !isNaN(lon);
    var start = have ? [lat, lon] : [0, 0];
    map = L.map("map").setView(start, have ? 12 : 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    marker = L.marker(start, {draggable:true}).addTo(map);
    marker.on("move", updateCoordsReadout);
    map.on("click", function(ev){ marker.setLatLng(ev.latlng); });
    updateCoordsReadout();
  }

  function searchAddress(){
    var q = document.getElementById("map-q").value.trim();
    var box = document.getElementById("map-results");
    if(!q || !map) return;
    box.textContent = "Searching…";
    fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=" + encodeURIComponent(q))
      .then(function(r){ if(!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function(results){
        box.textContent = results.length ? "" : "No results.";
        results.forEach(function(res){
          var a = el("a", {href:"#"}, res.display_name);
          a.addEventListener("click", function(ev){
            ev.preventDefault();
            var ll = [parseFloat(res.lat), parseFloat(res.lon)];
            marker.setLatLng(ll);
            map.setView(ll, 14);
          });
          box.appendChild(a);
        });
      })
      .catch(function(e){ box.textContent = "Search failed: " + e.message; });
  }

  function setAndFlash(id, value){
    var e = document.getElementById(id);
    e.value = value;
    e.classList.remove("field-flash");
    void e.offsetWidth; // force a reflow so the animation restarts on repeated clicks
    e.classList.add("field-flash");
  }

  function useMapLocation(){
    var ll = marker.getLatLng().wrap(); // wrap() keeps lng within +/-180 if the map was panned across the dateline
    setAndFlash("f_LATITUDE", ll.lat.toFixed(6));
    setAndFlash("f_LONGITUDE", ll.lng.toFixed(6));
    document.getElementById("f_LATITUDE").scrollIntoView({behavior:"smooth", block:"nearest"});
  }

  function pad2(n){ return (n < 10 ? "0" : "") + n; }

  // "UTC now HH:MM:SS  ·  offset +N -> HH:MM local" so the entered offset can be
  // checked against the user's wall clock.
  function tickUtc(){
    var e = document.getElementById("utc-now");
    if(!e) return;
    var d = new Date();
    var uh = d.getUTCHours(), um = d.getUTCMinutes();
    var txt = "UTC now " + pad2(uh) + ":" + pad2(um) + ":" + pad2(d.getUTCSeconds());
    var offEl = document.getElementById("f_UTC_OFFSET");
    var off = offEl ? parseFloat(offEl.value) : NaN;
    if(!isNaN(off)){
      var t = ((uh * 60 + um + Math.round(off * 60)) % 1440 + 1440) % 1440;
      txt += "  ·  offset " + (off >= 0 ? "+" : "") + off + " → " + pad2(Math.floor(t / 60)) + ":" + pad2(t % 60) + " local";
    }
    e.textContent = txt;
  }

  // Matches "KEY = value  # comment" capturing the prefix (incl. alignment
  // spaces) and any trailing comment, so a rewrite preserves both. Assumes '#'
  // does not appear inside a value, which holds for this config.
  function lineRe(key){
    return new RegExp("^(" + key + "[ \\t]*=[ \\t]*)([^#\\n]*?)([ \\t]*(?:#.*)?)$", "m");
  }

  function stripQuotes(s){
    if(s.length >= 2){
      var a = s.charAt(0), b = s.charAt(s.length - 1);
      if((a === '"' && b === '"') || (a === "'" && b === "'")) return s.slice(1, -1);
    }
    return s;
  }

  function getRaw(text, key){
    var m = text.match(lineRe(key));
    return m ? m[2].trim() : null;
  }

  function hydrate(text){
    FIELDS.forEach(function(f){
      if(!f.key) return;
      var e = document.getElementById("f_" + f.key);
      if(!e) return;
      var raw = getRaw(text, f.key);
      if(raw == null){
        // if key absent from config apply the field's default so a save doesn't write a misleading value
        if(f.type === "bool" && f["default"] != null) e.checked = f["default"];
        return;
      }
      if(f.type === "bool") e.checked = (raw === "True");
      else if(f.type === "number") e.value = raw;
      else if(f.type === "number_or_none") e.value = (raw === "None" ? "" : raw);
      else e.value = stripQuotes(raw); // string / select
    });
    document.getElementById("raw").textContent = text;
  }

  function sanitizeStr(s){
    var out = "";
    for(var i = 0; i < s.length; i++){
      var c = s.charAt(i);
      if(c === '\\' || c === '"') out += '\\' + c;        // escape backslash and quote
      else if(c === '\n' || c === '\r') out += ' ';        // no newlines in a value
      else out += c;
    }
    return out;
  }

  function literalFor(f){
    var e = document.getElementById("f_" + f.key);
    if(f.type === "bool") return e.checked ? "True" : "False";
    if(f.type === "number") return e.value.trim();
    if(f.type === "number_or_none"){ var v = e.value.trim(); return v === "" ? "None" : v; }
    return '"' + sanitizeStr(e.value) + '"'; // string / select
  }

  function setValue(text, key, literal){
    var re = lineRe(key);
    if(re.test(text)){
      return text.replace(re, function(m, pre, val, tail){ return pre + literal + tail; });
    }
    return text.replace(/\s*$/, "") + "\n" + key + " = " + literal + "\n";
  }

  function load(){
    setStatus("Loading…");
    fetch("/config", {cache:"no-store"})
      .then(function(r){ if(!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
      .then(function(t){ originalText = t; hydrate(t); setStatus(""); })
      .catch(function(e){ setStatus("Failed to load config: " + e.message, "err"); });
  }

  function save(){
    if(!form.reportValidity()) return;
    if(!confirm("Save config to the device and reboot?")) return;
    var out = originalText;
    FIELDS.forEach(function(f){ if(f.key) out = setValue(out, f.key, literalFor(f)); });
    setStatus("Saving…");
    fetch("/upload?path=config.py", {method:"POST", body:out})
      .then(function(r){
        if(!r.ok) return r.text().then(function(t){ throw new Error(t || ("HTTP " + r.status)); });
      })
      .then(function(){ return fetch("/reboot", {method:"POST"}); })
      .then(function(){
        setStatus("Saved. Device rebooting…", "ok");
        setTimeout(function(){ location.href = "/"; }, 6000);
      })
      .catch(function(e){ setStatus("Save failed: " + e.message, "err"); });
  }

  // Nothing submits the form natively (Save lives outside it); block implicit
  // submission so Enter in a field can't navigate away and lose edits
  form.addEventListener("submit", function(ev){ ev.preventDefault(); });
  document.getElementById("save").addEventListener("click", save);
  document.getElementById("reload").addEventListener("click", load);
  buildForm();
  var offEl = document.getElementById("f_UTC_OFFSET");
  if(offEl) offEl.addEventListener("input", tickUtc);
  tickUtc();
  setInterval(tickUtc, 1000);
  load();
})();
</script>
</body></html>
"""

_PAGE = (_PAGE.replace("__FAVICON__", _FAVICON).replace("__PICO__", _PICO_CSS)
         .replace("__LEAFLET_CSS__", _LEAFLET_CSS).replace("__LEAFLET_JS__", _LEAFLET_JS))


def render():
    """Return the (static) config editor HTML page."""
    return _PAGE

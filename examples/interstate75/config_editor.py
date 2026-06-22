"""Web-based editor for the device's config.py, served at /config-editor.

The page fetches the raw config.py via GET /config, presents
a form for the simple literal settings, and on save rewrites only those values
(leaving comments, layout, imports and the DISPLAY_TYPE/COLOR_ORDER expressions
untouched) before POSTing back via /upload?path=config.py and /reboot.
"""

_PICO_CSS = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
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
h2 { margin: 0 0 0.5rem; color: var(--pico-muted-color); }
.field { margin-bottom: 0.7rem; }
.field label { font-weight: 600; }
.field small { display: block; color: var(--pico-muted-color); }
.utc-hint { font-variant-numeric: tabular-nums; }
.actions { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 1.2rem; }
.actions button { width: auto; margin: 0; }
#status { min-height: 1.3em; margin-top: 0.6rem; }
#status.err { color: #c0392b; }
#status.ok { color: #2e7d32; }
details pre { white-space: pre-wrap; font-size: 0.72rem; max-height: 14rem; overflow: auto; }
</style>
</head><body><main class="container">
<header>
<h1>Config editor</h1>
<p><a href="/">&larr; Back to dashboard</a></p>
</header>
<article>
<form id="cfg"></form>
<div id="status">Loading…</div>
<div class="actions">
<button id="save">Save &amp; reboot</button>
<button id="reload" class="secondary outline">Reload from device</button>
</div>
<br/>
<details><summary>Raw config.py</summary><pre id="raw"></pre></details>
</article>
</main>
<script>
(function(){
  "use strict";

  // Only simple literal settings are editable here. DISPLAY_TYPE/COLOR_ORDER are
  // Python expressions and the imports are structural, so they're left to push.py.
  var FIELDS = [
    {section:"Location"},
    {key:"LATITUDE",  label:"Latitude",  type:"number", step:"any"},
    {key:"LONGITUDE", label:"Longitude", type:"number", step:"any"},
    {key:"RADIUS",    label:"Radius (km)", type:"number", step:"any"},
    {section:"Display"},
    {key:"BRIGHT_MODE",   label:"Bright mode", type:"bool"},
    {key:"SHOW_ALTITUDE", label:"Cycle altitude & distance", type:"bool"},
    {key:"DISTANCE_UNIT", label:"Distance unit", type:"select", options:["km","mi"]},
    {key:"ALTITUDE_UNIT", label:"Altitude unit", type:"select", options:["ft","m"]},
    {key:"VALUE_SWAP_INTERVAL", label:"Distance/altitude swap interval (s)", type:"number", step:"1"},
    {key:"ALTITUDE_CEILING_FT", label:"Altitude ceiling (ft)", type:"number_or_none", step:"1",
     help:"Ignore flights above this altitude. Blank = no ceiling."},
    {section:"Fetching"},
    {key:"REFRESH_INTERVAL", label:"Refresh interval (s)", type:"number", step:"1",
     help:"How often new flight data is fetched. 30s or more recommended."},
    {key:"API_URL",       label:"API URL", type:"string"},
    {key:"USER_AGENT_ID", label:"User-agent ID", type:"string"},
    {section:"Scrolling"},
    {key:"SCROLL_ENABLED",          label:"Scroll long text", type:"bool"},
    {key:"SCROLL_PAUSE_MS",         label:"Scroll pause (ms)", type:"number", step:"1"},
    {key:"SCROLL_SPEED_PX_PER_SEC", label:"Scroll speed (px/s)", type:"number", step:"1"},
    {section:"Quiet time"},
    {key:"UTC_OFFSET",         label:"UTC offset (hours)", type:"number", step:"1"},
    {key:"QUIET_START_HOUR",   label:"Quiet start hour", type:"number", step:"1"},
    {key:"QUIET_START_MINUTE", label:"Quiet start minute", type:"number", step:"1"},
    {key:"QUIET_END_HOUR",     label:"Quiet end hour", type:"number", step:"1"},
    {key:"QUIET_END_MINUTE",   label:"Quiet end minute", type:"number", step:"1"}
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
        if(f.type === "number" || f.type === "number_or_none"){ attrs.type = "number"; attrs.step = f.step || "1"; }
        else { attrs.type = "text"; }
        wrap.appendChild(el("input", attrs));
      }
      if(f.help) wrap.appendChild(el("small", null, f.help));
      // Live UTC clock under the offset field, as a reminder for setting it.
      if(f.key === "UTC_OFFSET") wrap.appendChild(el("small", {id:"utc-now", "class":"utc-hint"}));
      form.appendChild(wrap);
    });
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
      var raw = getRaw(text, f.key);
      if(raw == null) return;
      var e = document.getElementById("f_" + f.key);
      if(!e) return;
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

  function validate(){
    for(var i = 0; i < FIELDS.length; i++){
      var f = FIELDS[i];
      if(!f.key) continue;
      var e = document.getElementById("f_" + f.key);
      if(f.type === "number" && (e.value.trim() === "" || isNaN(Number(e.value)))){
        setStatus("Invalid number for " + f.label, "err"); e.focus(); return false;
      }
      if(f.type === "number_or_none"){
        var v = e.value.trim();
        if(v !== "" && isNaN(Number(v))){
          setStatus("Invalid number for " + f.label, "err"); e.focus(); return false;
        }
      }
    }
    return true;
  }

  function load(){
    setStatus("Loading…");
    fetch("/config", {cache:"no-store"})
      .then(function(r){ if(!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
      .then(function(t){ originalText = t; hydrate(t); setStatus("Loaded.", "ok"); })
      .catch(function(e){ setStatus("Failed to load config: " + e.message, "err"); });
  }

  function save(){
    if(!validate()) return;
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

_PAGE = _PAGE.replace("__FAVICON__", _FAVICON).replace("__PICO__", _PICO_CSS)


def render():
    """Return the (static) config editor HTML page."""
    return _PAGE

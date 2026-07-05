"""WiFi provisioning ("setup mode") for the flight display, served at /wifi.

When the device has no WiFi credentials, can't connect, or is booted with SW_A
held, flight_display.run_setup_mode() brings up an open access point named
FlightDisplay-XXXX and serves this module's page at http://192.168.4.1/wifi.
The CYW43 radio keeps the AP up while the STA interface tests the submitted
credentials, so the page can poll /wifi/status and show the device's new LAN
IP before the user leaves the hotspot. Credentials are persisted to secrets.py
only after a verified join, then the device reboots into normal operation.

The page is fully self-contained (no CDN assets) because AP clients have no
internet. In normal (connected) mode the same page is served at /wifi for
changing credentials; flight_display registers those routes lazily.

All join state transitions happen in tick(), driven from the provisioning
loop - never inside HTTP handlers - so responses always flush before a reset.
"""

import json
import machine
import network
import time

import webserver

try:
    from binascii import hexlify
except ImportError:
    from ubinascii import hexlify

AP_SSID_PREFIX = "FlightDisplay-"
AP_IP = "192.168.4.1"        # this appears to be the default AP IP on the Pico
AP_NETMASK = "255.255.255.0"
STA_JOIN_TIMEOUT_S = 20
AUTO_RETRY_INTERVAL_S = 60   # retry saved creds this often when reason == "connect-failed"
REBOOT_AFTER_SAVE_S = 60
FAILED_SCREEN_S = 5
SECRETS_PATH = "secrets.py"
SECRETS_BACKUP_PATH = "secrets_backup.py" # rollback copy; flight_display restores it if new creds fail

_SECRET_KEYS = ("WIFI_SSID", "WIFI_PASSWORD", "FLIGHT_FINDER_API_KEY")

_state = None    # provisioning state dict; None means normal (non-setup) mode
_saved = {"ssid": None, "password": None}
_pending = None  # creds from POST /wifi/connect awaiting pickup by tick()


def in_setup_mode():
    return _state is not None


def state():
    return _state


def ap_ssid():
    try:
        uid = hexlify(machine.unique_id()).decode()[-4:].upper()
    except Exception:
        uid = "0000"
    return AP_SSID_PREFIX + uid


def start_ap():
    """Bring up the open setup AP, plus the (unconnected) STA interface so
    /wifi/scan works and joins start quickly. Returns the AP's IP."""
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    ap = network.WLAN(network.AP_IF)
    try:
        ap.config(essid=ap_ssid(), security=0)
    except Exception:
        ap.config(essid=ap_ssid())  # older firmware: essid without password = open
    try:
        # Keep provisioning docs/UI stable by pinning the setup AP address.
        ap.ifconfig((AP_IP, AP_NETMASK, AP_IP, AP_IP))
    except Exception:
        # Some firmware builds may reject static AP ifconfig; fall back to default.
        pass
    ap.active(True)
    return ap.ifconfig()[0]


def begin(reason, saved_ssid, saved_password):
    """Enter setup mode. reason is "no-creds", "connect-failed" or "button";
    the saved creds (may be None) are what auto-retry uses for "connect-failed"."""
    global _state, _saved, _pending
    _saved = {"ssid": saved_ssid, "password": saved_password}
    _pending = None
    _state = {
        "reason": reason,
        "phase": "idle",           # idle | joining | joined | failed
        "target_ssid": None,
        "target_password": None,
        "target_api_key": "",
        "ip": None,
        "error": None,
        "saved": False,
        "auto": False,             # True while retrying the saved creds in the background
        "reboot_at_ms": None,
        "join_deadline_ms": None,
        "failed_at_ms": None,
        "next_auto_ms": None,
    }


def _status_error(status):
    if status == getattr(network, "STAT_WRONG_PASSWORD", -3):
        return "wrong password"
    if status == getattr(network, "STAT_NO_AP_FOUND", -2):
        return "network not found"
    return "connect failed"


def _start_join(ssid, password, api_key, now_ms, auto):
    st = _state
    st["target_ssid"] = ssid
    st["target_password"] = password
    st["target_api_key"] = api_key
    st["phase"] = "joining"
    st["error"] = None
    st["auto"] = auto
    st["join_deadline_ms"] = time.ticks_add(now_ms, STA_JOIN_TIMEOUT_S * 1000)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        wlan.disconnect()
    except Exception:
        pass
    wlan.connect(ssid, password)
    print(f"SETUP: trying to join '{ssid}'" + (" (auto)" if auto else ""))


def tick(now_ms):
    """Advance the join state machine; call every ~100ms from the setup loop."""
    global _pending
    st = _state
    if st is None:
        return None

    if st["saved"] and st["reboot_at_ms"] is not None:
        if time.ticks_diff(st["reboot_at_ms"], now_ms) <= 0:
            print("SETUP: reboot countdown expired, resetting")
            machine.reset()

    if st["phase"] == "joining":
        wlan = network.WLAN(network.STA_IF)
        status = wlan.status()
        if wlan.isconnected() and status == getattr(network, "STAT_GOT_IP", 3):
            st["ip"] = wlan.ifconfig()[0]
            print(f"SETUP: joined, IP {st['ip']}")
            if st["auto"]:
                # The saved creds work again (router came back) - nothing to
                # persist, reboot straight into normal operation
                print("SETUP: saved WiFi is back, resetting")
                machine.reset()
            st["phase"] = "joined"
            try:
                write_secrets(st["target_ssid"], st["target_password"], st["target_api_key"])
                st["saved"] = True
                st["reboot_at_ms"] = time.ticks_add(now_ms, REBOOT_AFTER_SAVE_S * 1000)
            except Exception as e:
                st["error"] = f"save failed: {e}"
        elif status < 0 or time.ticks_diff(now_ms, st["join_deadline_ms"]) > 0:
            st["error"] = _status_error(status) if status < 0 else "timed out"
            print(f"SETUP: join failed: {st['error']}")
            st["phase"] = "failed"
            st["failed_at_ms"] = now_ms
            st["auto"] = False
            try:
                wlan.disconnect()
            except Exception:
                pass
    elif st["phase"] == "failed":
        if time.ticks_diff(now_ms, st["failed_at_ms"]) > FAILED_SCREEN_S * 1000:
            st["phase"] = "idle"
            st["next_auto_ms"] = None
    elif st["phase"] == "idle":
        if _pending is not None:
            creds = _pending
            _pending = None
            _start_join(creds["ssid"], creds["password"], creds["api_key"], now_ms, False)
        elif st["reason"] == "connect-failed" and _saved["ssid"]:
            if st["next_auto_ms"] is None:
                st["next_auto_ms"] = time.ticks_add(now_ms, AUTO_RETRY_INTERVAL_S * 1000)
            elif time.ticks_diff(now_ms, st["next_auto_ms"]) >= 0:
                st["next_auto_ms"] = None
                _start_join(_saved["ssid"], _saved["password"], "", now_ms, True)

    return st["phase"]


def _quote(value):
    value = value if value is not None else ""
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    # A raw newline would end the quoted literal early and break secrets.py's
    # import on every subsequent boot (WPA passphrases are printable-ASCII
    # only, so nothing legitimate is lost)
    value = value.replace("\r", " ").replace("\n", " ")
    return '"' + value + '"'


def write_secrets(ssid, password, api_key=""):
    """Rewrite the known KEY = "value" lines of secrets.py in place, preserving
    any other lines/comments a user hand-added. A blank api_key leaves the
    existing FLIGHT_FINDER_API_KEY line untouched (a trailing comment on a
    rewritten line is not preserved). Creates the file if it doesn't exist."""
    values = {"WIFI_SSID": ssid or "", "WIFI_PASSWORD": password or ""}
    if api_key:
        values["FLIGHT_FINDER_API_KEY"] = api_key

    try:
        with open(SECRETS_PATH) as f:
            content = f.read()
    except OSError:
        content = ""

    if content:
        try:
            compile(content, SECRETS_PATH, "exec")
        except Exception:
            # The existing file doesn't parse; preserving its lines would keep
            # the boot import broken forever, so rebuild from scratch
            content = ""

    lines = content.split("\n") if content else []
    remaining = dict(values)
    have_api_key_line = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("FLIGHT_FINDER_API_KEY") and \
                stripped[len("FLIGHT_FINDER_API_KEY"):].lstrip().startswith("="):
            have_api_key_line = True
        for key in tuple(remaining):
            if stripped.startswith(key) and stripped[len(key):].lstrip().startswith("="):
                lines[i] = key + " = " + _quote(remaining.pop(key))
                break

    while lines and lines[-1].strip() == "":
        lines.pop()
    for key in _SECRET_KEYS:
        if key in remaining:
            lines.append(key + " = " + _quote(remaining[key]))
    if not api_key and not have_api_key_line:
        # keep `from secrets import FLIGHT_FINDER_API_KEY` importable on next boot
        lines.append('FLIGHT_FINDER_API_KEY = ""')

    with open(SECRETS_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---- HTTP handlers (webserver contract: (status, content_type, body)) ----

def handle_page(body, query):
    return (200, "text/html; charset=utf-8", render())


def handle_scan(body, query):
    try:
        nets = network.WLAN(network.STA_IF).scan()
    except Exception as e:
        return (500, "application/json", json.dumps({"error": f"scan failed: {e}"}))
    best = {}  # ssid -> (rssi, secure), keeping the strongest signal per ssid
    for net in nets:
        try:
            ssid = net[0].decode()
        except Exception:
            continue
        if not ssid:
            continue  # hidden networks can't be picked from a list anyway
        rssi = net[3]
        if ssid not in best or rssi > best[ssid][0]:
            best[ssid] = (rssi, net[4] != 0)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:15]
    return (200, "application/json", json.dumps(
        {"networks": [{"ssid": s, "rssi": r, "secure": sec} for s, (r, sec) in ranked]}))


def _parse_creds(body):
    try:
        req = json.loads(body)
        ssid = req["ssid"]
    except Exception:
        return None
    if not ssid:
        return None
    return {"ssid": ssid, "password": req.get("password", ""),
            "api_key": req.get("api_key", "")}


def handle_connect(body, query):
    """Setup mode only: stash creds for tick() to test asynchronously."""
    global _pending
    creds = _parse_creds(body)
    if creds is None:
        return (400, "application/json", json.dumps({"error": "need JSON with a non-empty ssid"}))
    _pending = creds
    return (200, "application/json", json.dumps({"ok": True}))


def _backup_secrets():
    """Keep the current (working) secrets.py as a rollback copy before a
    normal-mode save overwrites it with untested creds. Skips a file that
    doesn't parse - restoring broken secrets would be worse than setup mode."""
    try:
        with open(SECRETS_PATH) as f:
            content = f.read()
        compile(content, SECRETS_PATH, "exec")
    except Exception:
        return
    try:
        with open(SECRETS_BACKUP_PATH, "w") as f:
            f.write(content)
    except OSError:
        pass


def handle_save(body, query):
    """Write secrets without testing them first. Normal-mode save path, and a
    manual fallback in setup mode (starts the reboot countdown there)."""
    creds = _parse_creds(body)
    if creds is None:
        return (400, "application/json", json.dumps({"error": "need JSON with a non-empty ssid"}))
    if _state is None:
        # Normal mode: the creds being replaced got us online, so keep them
        # for the automatic rollback if the new ones can't connect
        _backup_secrets()
    try:
        write_secrets(creds["ssid"], creds["password"], creds["api_key"])
    except Exception as e:
        return (500, "application/json", json.dumps({"error": f"write failed: {e}"}))
    if _state is not None:
        _state["saved"] = True
        _state["reboot_at_ms"] = time.ticks_add(time.ticks_ms(), REBOOT_AFTER_SAVE_S * 1000)
    return (200, "application/json", json.dumps({"ok": True}))


def handle_status(body, query):
    if _state is not None:
        st = _state
        reboot_in_s = None
        if st["reboot_at_ms"] is not None:
            reboot_in_s = max(0, time.ticks_diff(st["reboot_at_ms"], time.ticks_ms()) // 1000)
        payload = {
            "mode": "setup",
            "reason": st["reason"],
            "phase": st["phase"],
            "ssid": st["target_ssid"] or _saved["ssid"],
            "ip": st["ip"],
            "error": st["error"],
            "saved": st["saved"],
            "reboot_in_s": reboot_in_s,
            "auto": st["auto"],
            "ap_ssid": ap_ssid(),
        }
    else:
        wlan = network.WLAN(network.STA_IF)
        ip = None
        ssid = None
        try:
            if wlan.isconnected():
                ip = wlan.ifconfig()[0]
                ssid = wlan.config("ssid")
        except Exception:
            pass
        payload = {
            "mode": "normal",
            "reason": None,
            "phase": "connected" if ip else "idle",
            "ssid": ssid,
            "ip": ip,
            "error": None,
            "saved": False,
            "reboot_in_s": None,
            "auto": False,
            "ap_ssid": ap_ssid(),
        }
    return (200, "application/json", json.dumps(payload))


def register_routes(setup_mode):
    """Register the /wifi routes directly (setup mode). In normal mode
    flight_display registers lazy-import wrappers instead, and /wifi/connect
    is deliberately unavailable (it would drop the live connection)."""
    webserver.route("GET", "/wifi", handle_page)
    webserver.route("GET", "/wifi/scan", handle_scan)
    webserver.route("GET", "/wifi/status", handle_status)
    webserver.route("POST", "/wifi/save", handle_save)
    if setup_mode:
        webserver.route("POST", "/wifi/connect", handle_connect)


def render():
    """Return the (static, self-contained) WiFi setup page."""
    return _PAGE


_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flight Display WiFi Setup</title>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #f5f6f8; color: #1c1e21; }
main { max-width: 26rem; margin: 0 auto; padding: 1.2rem; }
h1 { font-size: 1.35rem; margin: 0 0 0.4rem; }
#mode-note { font-size: 0.85rem; color: #555; margin-bottom: 1rem; }
label { display: block; margin: 0.9rem 0 0.25rem; font-size: 0.9rem; font-weight: 600; }
input:not([type=checkbox]) { width: 100%; padding: 0.55rem; font-size: 1rem; border: 1px solid #b7bcc4; border-radius: 6px; box-sizing: border-box; background: #fff; }
button { margin-top: 0.7rem; padding: 0.55rem 1rem; font-size: 0.95rem; border: 1px solid #8a919c; border-radius: 6px; background: #fff; cursor: pointer; }
#go { display: block; width: 100%; margin-top: 1.2rem; padding: 0.7rem; background: #1667c1; border-color: #1667c1; color: #fff; font-size: 1.05rem; }
.net { display: block; width: 100%; text-align: left; margin-top: 0.4rem; font-size: 0.9rem; }
.inline { display: inline-flex; gap: 0.35rem; align-items: center; font-weight: 400; font-size: 0.85rem; margin-top: 0.4rem; }
small { color: #666; }
#status { margin-top: 1rem; font-size: 0.95rem; line-height: 1.4; min-height: 1.4em; }
#status.ok { color: #157347; font-weight: 600; }
#status.err { color: #c62828; }
</style>
</head><body><main>
<h1>Flight Display WiFi setup</h1>
<div id="mode-note"></div>
<label for="ssid">WiFi network (SSID)</label>
<input id="ssid" autocomplete="off">
<button type="button" id="scan">Scan for networks</button>
<div id="networks"></div>
<label for="password">WiFi password</label>
<input id="password" type="password">
<label class="inline"><input type="checkbox" id="show-pw"> show password</label>
<label for="api-key">Flight Finder API key</label>
<input id="api-key" autocomplete="off">
<small>Leave blank to keep the key already on the device.</small>
<button type="button" id="go">Connect &amp; save</button>
<div id="status"></div>
</main>
<script>
(function(){
  "use strict";
  var mode = null, apSsid = "", timer = null, failures = 0;

  function $(id){ return document.getElementById(id); }
  function setStatus(msg, kind){ $("status").textContent = msg; $("status").className = kind || ""; }

  function applyStatus(s){
    failures = 0;
    mode = s.mode;
    if(s.ap_ssid) apSsid = s.ap_ssid;
    if(mode === "normal"){
      $("mode-note").textContent = "Connected to " + (s.ssid || "WiFi") + (s.ip ? " (" + s.ip + ")" : "") +
        ". Saving reboots the device onto the new network; if it can't connect, it restores " +
        "the previous credentials and reboots back onto this one.";
      $("go").textContent = "Save & reboot";
      return;
    }
    $("mode-note").textContent = "Enter your WiFi details. The display keeps this hotspot up while it tests them, then shows the device's new address here.";
    if(!timer) timer = setInterval(poll, 2000);
    if(s.phase === "joining"){
      setStatus("Trying to join " + (s.ssid || "") + "…" + (s.auto ? " (automatic retry of the saved WiFi)" : ""));
    } else if(s.phase === "joined"){
      setStatus("Connected and saved! Rejoin your normal WiFi, then open http://" + s.ip + "/" +
        (s.reboot_in_s != null ? " - rebooting in " + s.reboot_in_s + "s." : ""), "ok");
    } else if(s.error){
      setStatus("Join failed: " + s.error + ". Check the details and try again.", "err");
    } else if(s.reason === "connect-failed"){
      setStatus("The saved WiFi network is unreachable; the device retries it every minute. Enter new details, or just wait if the router was only offline.");
    }
  }

  function poll(){
    fetch("/wifi/status", {cache:"no-store"})
      .then(function(r){ return r.json(); })
      .then(applyStatus)
      .catch(function(){
        // The hotspot briefly drops clients while the radio hops to the target
        // network's channel - keep polling quietly and only hint after a while
        failures++;
        if(failures === 5){
          setStatus("Still working… if this page stops updating, rejoin the " + (apSsid || "setup") +
            " hotspot and reload - or read the new IP straight off the LED display.");
        }
        if(!timer) setTimeout(poll, 2000);
      });
  }

  $("scan").addEventListener("click", function(){
    $("networks").textContent = "Scanning…";
    fetch("/wifi/scan")
      .then(function(r){ return r.json(); })
      .then(function(d){
        var box = $("networks");
        var nets = d.networks || [];
        box.textContent = nets.length ? "" : "No networks found.";
        nets.forEach(function(n){
          var b = document.createElement("button");
          b.type = "button"; b.className = "net";
          b.textContent = n.ssid + " (" + n.rssi + " dBm" + (n.secure ? "" : ", open") + ")";
          b.addEventListener("click", function(){ $("ssid").value = n.ssid; $("password").focus(); });
          box.appendChild(b);
        });
      })
      .catch(function(){ $("networks").textContent = "Scan failed - try again."; });
  });

  $("show-pw").addEventListener("change", function(){
    $("password").type = this.checked ? "text" : "password";
  });

  function submit(){
    var ssid = $("ssid").value.trim();
    if(!ssid){ setStatus("WiFi network name is required.", "err"); return; }
    var body = JSON.stringify({ssid: ssid, password: $("password").value, api_key: $("api-key").value.trim()});
    var path = (mode === "normal") ? "/wifi/save" : "/wifi/connect";
    setStatus(mode === "normal" ? "Saving…" : "Testing connection to " + ssid + "…");
    fetch(path, {method: "POST", body: body})
      .then(function(r){
        return r.json().then(function(d){
          if(!r.ok) throw new Error(d.error || ("HTTP " + r.status));
          return d;
        });
      })
      .then(function(){
        if(mode === "normal"){
          setStatus("Saved - the device is rebooting onto the new network. If it can't connect, it restores the previous credentials.", "ok");
        }
      })
      .catch(function(e){ setStatus("Failed: " + e.message, "err"); });
  }
  $("go").addEventListener("click", submit);
  ["ssid", "password", "api-key"].forEach(function(id){
    $(id).addEventListener("keydown", function(ev){ if(ev.key === "Enter") submit(); });
  });

  poll();
})();
</script>
</body></html>
"""

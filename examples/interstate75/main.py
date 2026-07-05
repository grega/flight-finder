"""Boot stub for the flight display

MicroPython runs main.py at power-on. This stub delegates straight to
flight_display.main().
"""

import sys
import time

RUNTIME_CRASH_REBOOT_S = 600
CRASH_FILE = "last_crash.txt"  # kept in sync with flight_display.CRASH_FILE


def _persist_crash(text):
    """Save the crash so it survives the self-reboot below and shows up on the
    next boot's /status. Duplicated (not imported) so recovery never depends on
    the module that just crashed."""
    try:
        stamp = "boot crash"
        try:
            t = time.localtime()
            if t[0] >= 2024:
                stamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}Z".format(t[0], t[1], t[2], t[3], t[4], t[5])
        except Exception:
            pass
        with open(CRASH_FILE, "w") as f:
            f.write(stamp + "\n" + text)
    except OSError:
        pass


def _traceback_text(exc):
    try:
        import io
        buf = io.StringIO()
        sys.print_exception(exc, buf)
        return buf.getvalue()
    except Exception:
        return repr(exc)


def _init_display():
    """Best-effort display init so the matrix can say RECOVERY + where to look."""
    fd = sys.modules.get("flight_display")
    if fd is not None:
        return getattr(fd, "i75", None)
    try:
        from interstate75 import Interstate75
    except Exception:
        return None
    try:
        import config
        return Interstate75(display=config.DISPLAY_TYPE, color_order=config.COLOR_ORDER)
    except Exception:
        pass
    try:
        from interstate75 import DISPLAY_INTERSTATE75_64X32
        return Interstate75(display=DISPLAY_INTERSTATE75_64X32)
    except Exception:
        return None


def _show(i75, lines):
    if i75 is None:
        return
    try:
        d = i75.display
        d.set_pen(d.create_pen(0, 0, 0))
        d.clear()
        d.set_pen(d.create_pen(255, 128, 0))
        rows = (2, 13, 23)
        for i in range(min(len(lines), 3)):
            d.text(lines[i], 2, rows[i], i75.width, 1)
        i75.update()
    except Exception:
        pass


def _connect_sta():
    """Join WiFi with the saved creds. Returns the IP, or None."""
    try:
        import secrets
        ssid = getattr(secrets, "WIFI_SSID", "")
        password = getattr(secrets, "WIFI_PASSWORD", "")
    except Exception:
        return None
    if not ssid:
        return None
    try:
        import network
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            wlan.connect(ssid, password)
            deadline = time.ticks_add(time.ticks_ms(), 20 * 1000)
            while not wlan.isconnected() and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                time.sleep_ms(250)
        if wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return None


def _start_ap():
    """Fall back to the provisioning hotspot. Returns (ip, ap_name, wifi_setup)."""
    try:
        import wifi_setup
        wifi_setup.begin("no-creds", None, None)
        ip = wifi_setup.start_ap()
        return ip, wifi_setup.ap_ssid(), wifi_setup
    except Exception:
        return None, None, None


def _safe_target(name):
    # Mirrors flight_display._is_safe_upload_target, duplicated so recovery never depends on the module (that might have just crashed)
    if not name or not name.endswith(".py") or len(name) == 3:
        return False
    for ch in name[:-3]:
        if not (("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch == "_"):
            return False
    return True


def _recovery(exc, reboot_after_s=None):
    tb = _traceback_text(exc)
    print("RECOVERY: flight display crashed:")
    print(tb)
    _persist_crash(tb)

    import machine
    try:
        import webserver
    except Exception as e:
        # webserver.py itself is broken
        print(f"RECOVERY: webserver unusable ({e})")
        if reboot_after_s is not None:
            time.sleep(reboot_after_s)
            machine.reset()
        raise exc

    i75 = _init_display()
    _show(i75, ("RECOVERY", "starting..."))

    ip = _connect_sta()
    ap_name = None
    wifi_setup = None
    if ip is None:
        ip, ap_name, wifi_setup = _start_ap()

    state = {"reboot": False}

    def handle_index(body, query):
        report = (
            "Flight display RECOVERY MODE\n\n"
            "The main program failed; this fallback stays up so a fix can be\n"
            "pushed with push.py (POST /upload?path=<name>.py), then POST /reboot.\n\n"
            + tb
        )
        fd = sys.modules.get("flight_display")
        if fd is not None:
            try:
                report += "\n--- captured log ---\n" + bytes(fd._log_buffer.buf).decode()
            except Exception:
                pass
        return (200, "text/plain; charset=utf-8", report)

    def handle_upload(body, query):
        target = None
        for pair in query.split("&"):
            if pair.startswith("path="):
                target = pair[len("path="):]
                break
        if not _safe_target(target):
            return (400, "text/plain", f"path must be a simple .py filename, got {target!r}")
        try:
            import os
            tmp = target + ".tmp"
            with open(tmp, "wb") as f:
                f.write(body)
            os.rename(tmp, target)
        except OSError as e:
            return (500, "text/plain", f"Write failed: {e}")
        return (200, "text/plain", f"Wrote {len(body)} bytes to {target}")

    def handle_reboot(body, query):
        state["reboot"] = True
        return (200, "text/plain", "Rebooting")

    webserver.route("GET", "/", handle_index)
    webserver.route("POST", "/upload", handle_upload)
    webserver.route("POST", "/reboot", handle_reboot)
    if wifi_setup is not None:
        try:
            wifi_setup.register_routes(True)
        except Exception:
            pass
    try:
        webserver.start()
    except Exception as e:
        print(f"RECOVERY: webserver.start: {e}")

    if ap_name:
        _show(i75, ("RECOVERY", "Join " + ap_name, ip or ""))
    elif ip:
        _show(i75, ("RECOVERY", ip))
    else:
        _show(i75, ("RECOVERY", "no network"))
    print(f"RECOVERY: serving on {ip or 'no network'}" + (f" (AP {ap_name})" if ap_name else ""))

    reboot_at = None
    if reboot_after_s is not None:
        reboot_at = time.ticks_add(time.ticks_ms(), reboot_after_s * 1000)
    while True:
        webserver.poll()
        if state["reboot"]:
            time.sleep_ms(500)
            machine.reset()
        if wifi_setup is not None:
            try:
                wifi_setup.tick(time.ticks_ms())
            except Exception:
                pass
        if reboot_at is not None and time.ticks_diff(time.ticks_ms(), reboot_at) >= 0:
            print("RECOVERY: window expired, rebooting")
            machine.reset()
        time.sleep_ms(100)


if __name__ == "__main__":
    try:
        import flight_display
    except Exception as e:
        _recovery(e)  # broken code/config: stay reachable for a re-push
    else:
        try:
            flight_display.main()
            print("main() returned; rebooting")
            import machine
            machine.reset()
        except Exception as e:
            _recovery(e, RUNTIME_CRASH_REBOOT_S)

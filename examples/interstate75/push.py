#!/usr/bin/env python3
"""Push code/config to the Interstate 75 W's webserver, and round-trip its config.

The device is the source of truth for its own per-device config; this script
fetches it into _device/config.py (gitignored), and pushes that local working
copy back when you've edited it.

Examples:
    ./push.py code                  # push flight_display.py + the main.py boot stub and reboot
    ./push.py all                   # push every code module (not config.py/secrets.py) and reboot
    ./push.py file <filename>.py    # push any .py file and reboot
    ./push.py config fetch          # download device's config.py to _device/config.py
    ./push.py config push           # upload _device/config.py and reboot
    ./push.py reboot                # just reboot

Host can be set via --host, the I75_HOST env var, or a .push_host file next to
this script.
"""

import argparse
import os
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICE_DIR = os.path.join(HERE, "_device")
LOCAL_CONFIG = os.path.join(DEVICE_DIR, "config.py")
HOST_FILE = os.path.join(HERE, ".push_host")

DISPLAY_SRC = os.path.join(HERE, "flight_display.py")
MAIN_STUB_SRC = os.path.join(HERE, "main.py")

# Everything `all` pushes. Deliberately excludes the per-device files:
# config.py (round-tripped via `config fetch/push`) and secrets.py (managed
# on-device via /wifi). main.py goes last so an aborted run can't leave a
# device whose boot stub imports modules newer than the ones on flash.
ALL_CODE_FILES = ("webserver.py", "wifi_setup.py", "dashboard.py",
                  "config_editor.py", "flight_display.py", "main.py")


def resolve_host(cli_host):
    if cli_host:
        return cli_host
    env = os.environ.get("I75_HOST")
    if env:
        return env
    if os.path.exists(HOST_FILE):
        with open(HOST_FILE) as f:
            value = f.read().strip()
        if value:
            return value
    sys.exit(
        "No host. Pass --host, set I75_HOST, or write the IP/hostname to "
        f"{os.path.relpath(HOST_FILE, HERE)}"
    )


def _http(method, host, path, body=None, timeout=30):
    url = f"http://{host}{path}"
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        sys.exit(f"{method} {url} failed: {e.reason}")


def _upload_file(host, src_path, remote_name):
    if not os.path.exists(src_path):
        sys.exit(f"Source file not found: {src_path}")
    with open(src_path, "rb") as f:
        body = f.read()
    status, response = _http("POST", host, f"/upload?path={remote_name}", body)
    print(f"upload {os.path.basename(src_path)} -> {remote_name}: HTTP {status} {response.decode(errors='replace').strip()}")
    if status != 200:
        sys.exit(1)


def _reboot(host):
    try:
        status, response = _http("POST", host, "/reboot", b"", timeout=5)
        print(f"reboot: HTTP {status} {response.decode(errors='replace').strip()}")
    except (ConnectionResetError, ConnectionAbortedError):
        print("reboot: connection reset (expected - device is rebooting)")


def cmd_code(args):
    host = resolve_host(args.host)
    # main.py is a thin boot/recovery stub that imports flight_display.
    _upload_file(host, DISPLAY_SRC, "flight_display.py")
    _upload_file(host, MAIN_STUB_SRC, "main.py")
    _reboot(host)


def cmd_all(args):
    host = resolve_host(args.host)
    for name in ALL_CODE_FILES:
        _upload_file(host, os.path.join(HERE, name), name)
    _reboot(host)


def cmd_file(args):
    host = resolve_host(args.host)
    src_path = os.path.join(HERE, args.path) if not os.path.isabs(args.path) else args.path
    if not os.path.exists(src_path):
        sys.exit(f"Source file not found: {src_path}")
    remote_name = os.path.basename(src_path)
    _upload_file(host, src_path, remote_name)
    _reboot(host)


def cmd_config_fetch(args):
    host = resolve_host(args.host)
    status, body = _http("GET", host, "/config")
    if status != 200:
        sys.exit(f"GET /config returned HTTP {status}: {body.decode(errors='replace')}")
    os.makedirs(DEVICE_DIR, exist_ok=True)
    with open(LOCAL_CONFIG, "wb") as f:
        f.write(body)
    print(f"Wrote {len(body)} bytes to {os.path.relpath(LOCAL_CONFIG, HERE)}")


def cmd_config_push(args):
    host = resolve_host(args.host)
    if not os.path.exists(LOCAL_CONFIG):
        sys.exit(
            f"No local config at {os.path.relpath(LOCAL_CONFIG, HERE)} - "
            "run `./push.py config fetch` first."
        )
    _upload_file(host, LOCAL_CONFIG, "config.py")
    _reboot(host)


def cmd_reboot(args):
    _reboot(resolve_host(args.host))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="IP or hostname of the I75 (overrides I75_HOST and .push_host)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("code", help="push flight_display.py and the main.py boot stub, then reboot").set_defaults(func=cmd_code)
    sub.add_parser("all", help="push every code module (not config.py/secrets.py) and reboot").set_defaults(func=cmd_all)
    file_parser = sub.add_parser("file", help="push any local .py file (eg. webserver.py, dashboard.py) under its same name and reboot")
    file_parser.add_argument("path", help="path to local .py file (relative to this script, or absolute)")
    file_parser.set_defaults(func=cmd_file)
    sub.add_parser("reboot", help="reboot the device").set_defaults(func=cmd_reboot)

    config = sub.add_parser("config", help="manage device config").add_subparsers(dest="config_cmd", required=True)
    config.add_parser("fetch", help="download device config into _device/").set_defaults(func=cmd_config_fetch)
    config.add_parser("push",  help="upload _device/config.py and reboot").set_defaults(func=cmd_config_push)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

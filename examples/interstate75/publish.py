#!/usr/bin/env python3
"""Publish the current device code to the Flight Finder service as an OTA build.

Unlike push.py (which uploads directly to a device on your LAN), this uploads to
the *service*, which hosts a manifest the fleet pulls on their next check-in. A
publish reaches only the canary device; run a Promote from the /fleet page once
you've verified it, to roll it out to everyone else.

The published version is the `VERSION` constant in flight_display.py - bump that
constant to ship an update. (The device compares its running VERSION to the
published one to decide whether to update, so they must match; publishing a
different number would loop the device.)

Config (kept out of the repo - this is a public repo):
    service URL:  --url, or FLIGHT_FINDER_URL, or a .publish_url file next to this script
    admin token:  --token, or FLIGHT_FINDER_ADMIN_TOKEN

Examples:
    ./publish.py                 # publish flight_display.py's VERSION to the canary
    ./publish.py --version 1.3.0 # same, but assert the VERSION constant is 1.3.0 first
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

from push import ALL_CODE_FILES, HERE  # same module set push.py ships

URL_FILE = os.path.join(HERE, ".publish_url")


def resolve_url(cli_url):
    url = cli_url or os.environ.get("FLIGHT_FINDER_URL")
    if not url and os.path.exists(URL_FILE):
        with open(URL_FILE) as f:
            url = f.read().strip()
    if not url:
        sys.exit("No service URL. Pass --url, set FLIGHT_FINDER_URL, or write it to "
                 f"{os.path.relpath(URL_FILE, HERE)}")
    return url.rstrip("/")


def resolve_token(cli_token):
    token = cli_token or os.environ.get("FLIGHT_FINDER_ADMIN_TOKEN")
    if not token:
        sys.exit("No admin token. Pass --token or set FLIGHT_FINDER_ADMIN_TOKEN.")
    return token


def read_version():
    """The VERSION constant from flight_display.py - the authoritative version."""
    path = os.path.join(HERE, "flight_display.py")
    with open(path) as f:
        for line in f:
            m = re.match(r'''\s*VERSION\s*=\s*["']([^"']+)["']''', line)
            if m:
                return m.group(1)
    sys.exit("Could not find a VERSION = \"...\" line in flight_display.py")


def bundle_files():
    files = {}
    for name in ALL_CODE_FILES:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            sys.exit(f"Missing module: {name}")
        with open(path, encoding="utf-8") as f:
            files[name] = f.read()
    return files


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="service base URL (overrides FLIGHT_FINDER_URL / .publish_url)")
    parser.add_argument("--token", help="admin token (overrides FLIGHT_FINDER_ADMIN_TOKEN)")
    parser.add_argument("--version", help="assert flight_display.py's VERSION equals this before publishing")
    args = parser.parse_args()

    url = resolve_url(args.url)
    token = resolve_token(args.token)
    version = read_version()

    # A mismatch here would ship code whose running VERSION != the published
    # target, looping the device - so refuse rather than warn.
    if args.version and args.version != version:
        sys.exit(f"flight_display.py VERSION is {version}, not {args.version}. "
                 "Bump the VERSION constant to publish a new version.")

    files = bundle_files()
    total = sum(len(c) for c in files.values())
    print(f"Publishing version {version} ({len(files)} files, {total} bytes) to {url}")

    body = json.dumps({"version": version, "files": files}).encode()
    req = urllib.request.Request(f"{url}/ota/publish", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, payload = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            msg = json.loads(payload).get("error", payload.decode(errors="replace"))
        except ValueError:
            msg = payload.decode(errors="replace")
        sys.exit(f"Publish failed (HTTP {e.code}): {msg}")
    except urllib.error.URLError as e:
        sys.exit(f"Publish failed: {e.reason}")

    print(f"HTTP {status}: {payload.decode(errors='replace')}")
    print(f"\nPublished {version} to the canary. Verify it on /fleet, then click "
          "\"Promote\" to roll it out to the rest of the fleet.")


if __name__ == "__main__":
    main()

"""Storage for published device firmware (the OTA payload the fleet pulls).

`POST /ota/publish` writes the device modules + a generated `manifest.json`
here; devices fetch `GET /ota/manifest` then `GET /ota/file/<name>` and verify
each file against the manifest's checksums before swapping it in.

Files live on the Dokku volume (same mount as fleet.db) so they survive
redeploys. The manifest carries sha256 (primary), crc32 (fallback for devices
whose MicroPython build lacks hashlib), and size, so the device can verify with
whatever it has. Kept separate from flight_service so publishing stays a small,
self-contained concern - the canary/fleet version *pointers* live in
fleet_store (fleet_meta); this module only owns the bytes + manifest.
"""

import binascii
import hashlib
import json
import os

# Production points this at the mounted volume (/app/storage/ota); the default
# keeps local dev and tests self-contained.
OTA_DIR = os.getenv("OTA_DIR", "ota")
_MANIFEST_NAME = "manifest.json"


class PublishError(Exception):
    """A publish was rejected. `status` is the HTTP code the endpoint returns
    (400 malformed, 409 not-newer-than-published)."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _manifest_path():
    return os.path.join(OTA_DIR, _MANIFEST_NAME)


def _parse_version(value):
    """Dotted-int version -> comparable tuple. Raises PublishError(400) on
    anything unparseable, so the monotonic guard can't be sidestepped."""
    if not isinstance(value, str) or not value.strip():
        raise PublishError("version must be a non-empty string", 400)
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        raise PublishError(f"version {value!r} must be dotted integers (eg. 1.2.3)", 400)


def _is_safe_name(name):
    """A simple `<module>.py` name - blocks path traversal (no '/', '..')."""
    if not isinstance(name, str) or not name.endswith(".py") or len(name) <= 3:
        return False
    stem = name[:-3]
    return all(("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c == "_"
               for c in stem)


def _write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def get_manifest():
    """The current published manifest as a dict, or None if nothing published."""
    try:
        with open(_manifest_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def current_version():
    manifest = get_manifest()
    return manifest["version"] if manifest else None


def publish(version, files):
    """Publish a new firmware set. `files` is {name: source_text}. Enforces a
    strictly-increasing version (correctness, not just a footgun guard: devices
    converge by comparing version strings, so re-publishing different code under
    the same version would strand every device already on it). Writes the files
    then the manifest (manifest last, so a torn write leaves the old one intact),
    and returns the new manifest. Does NOT touch canary/fleet pointers - the
    caller sets canary_version after this succeeds."""
    new_tuple = _parse_version(version)  # 400 on malformed
    if not files:
        raise PublishError("no files provided", 400)
    for name in files:
        if not _is_safe_name(name):
            raise PublishError(f"unsafe file name: {name!r}", 400)

    current = current_version()
    if current is not None and new_tuple <= _parse_version(current):
        raise PublishError(
            f"version {version} is not newer than the published {current}", 409)

    os.makedirs(OTA_DIR, exist_ok=True)
    entries = []
    for name in sorted(files):
        content = files[name]
        data = content.encode() if isinstance(content, str) else content
        _write_atomic(os.path.join(OTA_DIR, name), data)
        entries.append({
            "name": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "crc32": "%08x" % (binascii.crc32(data) & 0xffffffff),
        })

    manifest = {"version": version, "files": entries}
    _write_atomic(_manifest_path(), json.dumps(manifest).encode())
    return manifest


def read_file(name):
    """Bytes of a published file, or None. Validated against the manifest's file
    list rather than the raw filesystem, so `<name>` can't escape OTA_DIR."""
    manifest = get_manifest()
    if not manifest or not any(entry["name"] == name for entry in manifest["files"]):
        return None
    try:
        with open(os.path.join(OTA_DIR, name), "rb") as f:
            return f.read()
    except OSError:
        return None

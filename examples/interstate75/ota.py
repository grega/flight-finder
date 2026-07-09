"""Pull-based OTA updater for the flight display (device side).

flight_display lazily imports this only when a check-in reports `update_available`,
so an up-to-date device never pays its heap. It fetches the service manifest,
downloads each changed module, verifies its checksum and that it compiles, and
only then swaps them in (main.py last) - writing an `ota_pending` marker *before*
the swap so main.py's boot rollback catches even an interrupted swap.

This module never imports flight_display (avoids a cycle, and keeps the updater
independent). The *rollback* on a crash-looping update lives in main.py, self-
contained, so it works even if a bad update broke this file.
"""

import binascii
import gc
import json
import os

import urequests

# Marker filenames - duplicated (as plain strings) in main.py and flight_display
# so neither has to import this module just to read them.
OTA_PENDING = "ota_pending.json"   # {"version": ..., "attempts": N} - update in flight
OTA_BAK_DIR = "ota_bak"            # pre-update copies of the changed files
OTA_FAILED = "ota_failed.txt"      # a version that was auto-rolled-back; don't re-pull it

_CHUNK = 512
_TIMEOUT_S = 20

# sha256 if the firmware has it (primary), else crc32 (fallback). Both are in
# the manifest, so we verify with whatever this build supports.
try:
    import hashlib
    hashlib.sha256
    _HAS_SHA256 = True
except (ImportError, AttributeError):
    try:
        import uhashlib as hashlib
        hashlib.sha256
        _HAS_SHA256 = True
    except (ImportError, AttributeError):
        _HAS_SHA256 = False

try:
    from binascii import hexlify
except ImportError:
    from ubinascii import hexlify


def _http_get(url, headers):
    try:
        return urequests.get(url, headers=headers, timeout=_TIMEOUT_S)
    except TypeError:
        return urequests.get(url, headers=headers)  # older urequests: no timeout kwarg


def _sha256_hex(data):
    return hexlify(hashlib.sha256(data).digest()).decode()


def _crc32_hex(data):
    return "%08x" % (binascii.crc32(data) & 0xffffffff)


def _checksum_ok(data, entry):
    if _HAS_SHA256 and entry.get("sha256"):
        return _sha256_hex(data) == entry["sha256"]
    if entry.get("crc32"):
        return _crc32_hex(data) == entry["crc32"]
    return len(data) == entry.get("size")  # last resort


def _local_matches(entry):
    """True if the on-disk file already equals the manifest entry."""
    name = entry["name"]
    try:
        with open(name, "rb") as f:
            data = f.read()
    except OSError:
        return False  # missing -> needs download
    return _checksum_ok(data, entry)


def read_failed():
    try:
        with open(OTA_FAILED) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _clean_temp():
    """Drop any stale .ota download temp files from a prior interrupted attempt."""
    try:
        for name in os.listdir():
            if name.endswith(".ota"):
                _remove(name)
    except OSError:
        pass


def _download_verify(base_url, api_key, entry):
    """Download one module to `<name>.ota`, verify checksum + that it compiles.
    Returns True on success. One file in RAM at a time to bound heap."""
    name = entry["name"]
    gc.collect()
    resp = _http_get(base_url + "/ota/file/" + name, {"X-API-Key": api_key})
    try:
        if resp.status_code != 200:
            print(f"OTA: {name} HTTP {resp.status_code}")
            return False
        data = resp.content
    finally:
        resp.close()
    if not _checksum_ok(data, entry):
        print(f"OTA: {name} checksum mismatch")
        return False
    try:
        compile(data.decode(), name, "exec")
    except Exception as e:
        print(f"OTA: {name} does not compile: {e}")
        return False
    with open(name + ".ota", "wb") as f:
        f.write(data)
    return True


def _swap_order(changed):
    """Swap main.py last - it's the boot stub, so a mid-swap power loss leaves
    the old (working) boot stub until the very end."""
    return ([e for e in changed if e["name"] != "main.py"]
            + [e for e in changed if e["name"] == "main.py"])


def _write_pending(version):
    with open(OTA_PENDING, "w") as f:
        json.dump({"version": version, "attempts": 0}, f)


def apply_update(base_url, api_key, target_version, on_status=None):
    """Fetch the manifest and, if there's a newer verified build, install it.
    Returns 'updated' (caller should reboot), or a reason string otherwise.
    Never raises - a failed update must not disrupt the display."""
    def status(msg):
        print("OTA:", msg)
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass
    try:
        resp = _http_get(base_url + "/ota/manifest", {"X-API-Key": api_key})
        try:
            if resp.status_code != 200:
                return "no-manifest"
            manifest = resp.json()
        finally:
            resp.close()

        version = manifest.get("version")
        files = manifest.get("files") or []
        if not version or not files:
            return "no-manifest"

        # Don't re-pull a build that already crash-looped this device; wait for a
        # newer one. A different target clears the stale failed marker.
        failed = read_failed()
        if failed == version:
            return "skipped-failed"
        if failed and failed != version:
            _remove(OTA_FAILED)

        changed = [e for e in files if not _local_matches(e)]
        if not changed:
            return "uptodate"

        status("Updating " + version)
        _clean_temp()
        try:
            os.mkdir(OTA_BAK_DIR)
        except OSError:
            pass

        # Download + verify + compile EVERYTHING before touching a live file.
        for entry in changed:
            if not _download_verify(base_url, api_key, entry):
                _clean_temp()
                return "verify-failed"

        ordered = _swap_order(changed)
        # Back up current copies (a brand-new module has nothing to back up).
        for entry in ordered:
            name = entry["name"]
            try:
                with open(name, "rb") as src:
                    with open(OTA_BAK_DIR + "/" + name, "wb") as dst:
                        dst.write(src.read())
            except OSError:
                pass
        # Marker BEFORE the swap, so an interrupted swap is still rolled back.
        _write_pending(version)
        for entry in ordered:
            name = entry["name"]
            os.rename(name + ".ota", name)
        status("Updated - rebooting")
        return "updated"
    except Exception as e:
        print(f"OTA: update failed: {e}")
        _clean_temp()
        return "error"

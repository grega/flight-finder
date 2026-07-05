"""SQLite-backed store of device heartbeats for the fleet view.

Every authenticated flight poll doubles as a heartbeat (see flight_service),
so this records one row per device keyed by its self-reported X-Device-Id.

Deployed under gunicorn with multiple workers (separate processes), so an
in-memory dict would not be shared - a single SQLite file on a persistent
volume is the store. WAL mode plus a busy timeout let the workers write
concurrently; each call uses a short-lived connection so nothing is shared
across threads.
"""

import os
import sqlite3
from datetime import datetime, timezone

# Production points this at the mounted Dokku volume (/app/storage/fleet.db);
# the default keeps local dev and tests self-contained.
DB_PATH = os.getenv("FLEET_DB_PATH", "fleet.db")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    # WAL: concurrent readers + one serialized writer across worker processes.
    # busy_timeout: wait rather than raise if another worker holds the write lock.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    """Create the table if absent. Idempotent, so every worker can run it at
    import with no coordination."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id     TEXT PRIMARY KEY,
                label         TEXT,
                version       TEXT,
                last_ip       TEXT,
                first_seen    TEXT,
                last_seen     TEXT,
                request_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def record(device_id, label, version, ip):
    """Upsert a device's heartbeat: create it on first sighting, otherwise
    refresh its details and bump request_count. first_seen is never overwritten."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO devices (device_id, label, version, last_ip,
                                 first_seen, last_seen, request_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(device_id) DO UPDATE SET
                label         = excluded.label,
                version       = excluded.version,
                last_ip       = excluded.last_ip,
                last_seen     = excluded.last_seen,
                request_count = request_count + 1
            """,
            (device_id, label, version, ip, now, now),
        )


def list_devices():
    """All known devices, most-recently-seen first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT device_id, label, version, last_ip, first_seen, "
            "last_seen, request_count FROM devices ORDER BY last_seen DESC"
        ).fetchall()
    return [dict(row) for row in rows]

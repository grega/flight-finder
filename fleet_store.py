"""SQLite-backed store for the fleet: device heartbeats, queued commands,
on-demand logs, and fleet-wide OTA version pointers.

Devices check in on their own cadence (see flight_service `/device/checkin`),
which records one row per device keyed by its self-reported X-Device-Id and
also carries command acks / uploaded logs.

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


def _ensure_column(conn, table, column, coltype):
    """Idempotently add a column to an existing table (SQLite has no
    ADD COLUMN IF NOT EXISTS), so a deployed fleet.db picks up new fields on
    the next boot without a manual migration."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    """Create/upgrade the schema. Idempotent, so every worker can run it at
    import with no coordination."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id     TEXT PRIMARY KEY,
                label         TEXT,
                version       TEXT,
                last_ip       TEXT,
                lan_ip        TEXT,
                ota_failed    TEXT,
                first_seen    TEXT,
                last_seen     TEXT,
                request_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Bring an existing (pre-fleet-OTA) devices table up to date.
        _ensure_column(conn, "devices", "lan_ip", "TEXT")
        _ensure_column(conn, "devices", "ota_failed", "TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id    TEXT NOT NULL,
                action       TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                delivered_at TEXT,
                acked_at     TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_logs (
                device_id   TEXT PRIMARY KEY,
                logs        TEXT,
                received_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS fleet_meta (key TEXT PRIMARY KEY, value TEXT)"
        )


def record(device_id, label, version, ip, lan_ip=None, ota_failed=None):
    """Upsert a device's heartbeat: create it on first sighting, otherwise
    refresh its details and bump request_count. first_seen is never overwritten.
    lan_ip/ota_failed are optional (older callers omit them)."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO devices (device_id, label, version, last_ip, lan_ip,
                                 ota_failed, first_seen, last_seen, request_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(device_id) DO UPDATE SET
                label         = excluded.label,
                version       = excluded.version,
                last_ip       = excluded.last_ip,
                lan_ip        = excluded.lan_ip,
                ota_failed    = excluded.ota_failed,
                last_seen     = excluded.last_seen,
                request_count = request_count + 1
            """,
            (device_id, label, version, ip, lan_ip, ota_failed, now, now),
        )


def list_devices():
    """All known devices, most-recently-seen first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT device_id, label, version, last_ip, lan_ip, ota_failed, "
            "first_seen, last_seen, request_count FROM devices ORDER BY last_seen DESC"
        ).fetchall()
    return [dict(row) for row in rows]


# ---- Commands: one-shot instructions delivered on a check-in, acked next ----

def enqueue_command(device_id, action):
    """Queue a one-shot command (reboot / enter-setup / clear-crash / send-logs)
    for a device. Returns the new command id."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO commands (device_id, action, created_at) VALUES (?, ?, ?)",
            (device_id, action, _now()),
        )
        return cur.lastrowid


def pending_command(device_id):
    """The oldest un-acked command for a device (marking it delivered), or None.
    It stays pending until the device acks it, so a dropped response redelivers."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, action FROM commands WHERE device_id = ? AND acked_at IS NULL "
            "ORDER BY id LIMIT 1",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE commands SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
            (_now(), row["id"]),
        )
        return {"id": row["id"], "action": row["action"]}


def ack_commands(device_id, ids):
    """Mark the given command ids acked so they stop being delivered."""
    if not ids:
        return
    now = _now()
    with _connect() as conn:
        conn.executemany(
            "UPDATE commands SET acked_at = ? WHERE id = ? AND device_id = ? "
            "AND acked_at IS NULL",
            [(now, cid, device_id) for cid in ids],
        )


# ---- On-demand logs: one (overwritten) set per device -----------------------

def store_logs(device_id, logs):
    """Save the latest uploaded log blob for a device, replacing any previous set."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO device_logs (device_id, logs, received_at) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET logs = excluded.logs, "
            "received_at = excluded.received_at",
            (device_id, logs, _now()),
        )


def get_logs(device_id):
    """The latest stored logs for a device as {logs, received_at}, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT logs, received_at FROM device_logs WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    return dict(row) if row else None


def logs_index():
    """{device_id: received_at} for every device that has stored logs (cheap
    lookup for the fleet page, avoiding fetching each blob just to test existence)."""
    with _connect() as conn:
        rows = conn.execute("SELECT device_id, received_at FROM device_logs").fetchall()
    return {row["device_id"]: row["received_at"] for row in rows}


# ---- Fleet-wide key/value meta (OTA version pointers) -----------------------

def get_meta(key, default=None):
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM fleet_meta WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_meta(key, value):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO fleet_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

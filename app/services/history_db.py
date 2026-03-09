import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       TEXT NOT NULL,
    device_name     TEXT,
    client_type     TEXT,
    room_id         TEXT,
    ip_address      TEXT,
    country         TEXT,
    country_code    TEXT,
    region          TEXT,
    city            TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    total_sessions  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(client_id)
);

CREATE TABLE IF NOT EXISTS connection_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id        TEXT NOT NULL,
    device_name      TEXT,
    room_id          TEXT,
    client_type      TEXT,
    ip_address       TEXT,
    connected_at     TEXT NOT NULL,
    disconnected_at  TEXT,
    duration_seconds INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_connected_at ON connection_events(connected_at);
CREATE INDEX IF NOT EXISTS idx_clients_last_seen   ON clients(last_seen);
"""


def init_db(db_path: str):
    import os
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _lock:
        con = sqlite3.connect(db_path)
        con.executescript(SCHEMA)
        con.commit()
        con.close()


@contextmanager
def _conn(db_path: str):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def upsert_client(db_path, client_id, device_name, client_type, room_id, ip_address) -> None:
    now = _now_iso()
    with _lock, _conn(db_path) as con:
        con.execute("""
            INSERT INTO clients (client_id, device_name, client_type, room_id,
                                 ip_address, first_seen, last_seen, total_sessions)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(client_id) DO UPDATE SET
                device_name    = excluded.device_name,
                client_type    = excluded.client_type,
                room_id        = excluded.room_id,
                ip_address     = excluded.ip_address,
                last_seen      = excluded.last_seen,
                total_sessions = total_sessions + 1
        """, (client_id, device_name, client_type, room_id, ip_address, now, now))


def insert_event(db_path, client_id, device_name, room_id, client_type, ip_address) -> int:
    now = _now_iso()
    with _lock, _conn(db_path) as con:
        cur = con.execute("""
            INSERT INTO connection_events
                (client_id, device_name, room_id, client_type, ip_address, connected_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (client_id, device_name, room_id, client_type, ip_address, now))
        return cur.lastrowid


def close_event(db_path, event_id: int) -> None:
    now = _now_iso()
    with _lock, _conn(db_path) as con:
        con.execute("""
            UPDATE connection_events
            SET disconnected_at  = ?,
                duration_seconds = CAST(
                    (julianday(?) - julianday(connected_at)) * 86400 AS INTEGER)
            WHERE id = ? AND disconnected_at IS NULL
        """, (now, now, event_id))


def update_client_geo(db_path, client_id: str, country: str, country_code: str,
                      region: str, city: str) -> None:
    with _lock, _conn(db_path) as con:
        con.execute("""
            UPDATE clients SET country=?, country_code=?, region=?, city=?
            WHERE client_id=?
        """, (country, country_code, region, city, client_id))


def query_summary(db_path) -> dict:
    with _conn(db_path) as con:
        row = con.execute("""
            SELECT
                (SELECT COUNT(*) FROM clients)                       AS unique_clients,
                (SELECT COUNT(*) FROM connection_events)             AS total_sessions,
                (SELECT COUNT(DISTINCT country_code)
                 FROM clients WHERE country_code IS NOT NULL
                   AND country_code != '')                           AS countries
        """).fetchone()
        return dict(row) if row else {}


def query_clients(db_path, search='', limit=200, offset=0):
    like = f'%{search}%'
    with _conn(db_path) as con:
        rows = con.execute("""
            SELECT client_id, device_name, client_type, room_id, ip_address,
                   country, country_code, region, city,
                   first_seen, last_seen, total_sessions
            FROM clients
            WHERE (? = '' OR device_name LIKE ? OR country LIKE ? OR city LIKE ? OR room_id LIKE ?)
            ORDER BY last_seen DESC
            LIMIT ? OFFSET ?
        """, (search, like, like, like, like, limit, offset)).fetchall()
        total = con.execute("""
            SELECT COUNT(*) FROM clients
            WHERE (? = '' OR device_name LIKE ? OR country LIKE ? OR city LIKE ? OR room_id LIKE ?)
        """, (search, like, like, like, like)).fetchone()[0]
        return [dict(r) for r in rows], total


def query_hourly(db_path) -> list:
    with _conn(db_path) as con:
        rows = con.execute("""
            SELECT CAST(strftime('%H', connected_at) AS INTEGER) AS hour,
                   COUNT(*) AS count
            FROM connection_events
            GROUP BY hour
            ORDER BY hour
        """).fetchall()
        counts = {r['hour']: r['count'] for r in rows}
        return [{'hour': h, 'count': counts.get(h, 0)} for h in range(24)]


def query_daily(db_path, days=30) -> list:
    with _conn(db_path) as con:
        rows = con.execute("""
            SELECT DATE(connected_at) AS date, COUNT(*) AS count
            FROM connection_events
            WHERE connected_at >= DATE('now', ?)
            GROUP BY date
            ORDER BY date
        """, (f'-{days} days',)).fetchall()
        return [dict(r) for r in rows]


def query_countries(db_path, top=15) -> list:
    with _conn(db_path) as con:
        rows = con.execute("""
            SELECT country, country_code, COUNT(*) AS count
            FROM clients
            WHERE country IS NOT NULL AND country != '' AND country != 'Local'
            GROUP BY country_code
            ORDER BY count DESC
            LIMIT ?
        """, (top,)).fetchall()
        return [dict(r) for r in rows]

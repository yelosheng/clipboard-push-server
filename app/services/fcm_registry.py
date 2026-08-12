"""Persistent (SQLite) FCM device-token registry.

Maps room -> client_id -> token, surviving socket disconnects. This is
intentionally separate from signal_core's in-memory, connection-scoped
state: a frozen/killed device disappears from live room membership but must
still be reachable by FCM, so its token lives here and is NOT purged on
disconnect.

Mirrors the SQLite idiom of app/services/history_db.py.
"""

import sqlite3
import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS fcm_tokens (
    room        TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    token       TEXT NOT NULL,
    client_type TEXT,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (room, client_id)
);
CREATE INDEX IF NOT EXISTS idx_fcm_tokens_room ON fcm_tokens(room);
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


def register_token(db_path: str, room: str, client_id: str, token: str, client_type: str = None):
    """Register (room, client_id) -> token.

    Pairing is strictly 1:1 by product design: a room may hold at most one
    registered client_id per client_type (one phone, one PC — multi-device
    sharing of a room is disallowed). Registering a new client_id evicts any
    other client_id of the same client_type previously registered for this
    room, so a stale registration left behind by a reinstall (client_id
    changes whenever the signing key changes) is displaced immediately
    instead of lingering — and receiving duplicate pushes — until Firebase
    eventually reports its token invalid.

    When client_type is falsy, eviction is skipped: an ambiguous type isn't
    grounds to blow away someone else's registration.
    """
    if not (db_path and room and client_id and token):
        return
    with _lock, _conn(db_path) as con:
        if client_type:
            con.execute(
                "DELETE FROM fcm_tokens WHERE room = ? AND client_type = ? AND client_id != ?",
                (room, client_type, client_id),
            )
        con.execute(
            "INSERT INTO fcm_tokens (room, client_id, token, client_type, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(room, client_id) DO UPDATE SET "
            "token=excluded.token, client_type=excluded.client_type, updated_at=excluded.updated_at",
            (room, client_id, token, client_type, int(time.time() * 1000)),
        )


def get_room_tokens(db_path: str, room: str, exclude_client_id: str = None):
    if not (db_path and room):
        return []
    with _lock, _conn(db_path) as con:
        rows = con.execute(
            "SELECT client_id, token FROM fcm_tokens WHERE room = ?", (room,)
        ).fetchall()
    return [r['token'] for r in rows if r['client_id'] != exclude_client_id]


def get_room_client_ids(db_path: str, room: str):
    """Return the client_ids that have a persistent token registered for the
    room (regardless of current socket connectivity). Used to surface
    FCM-reachable but offline peers so HTTP senders don't gate on live peers."""
    if not (db_path and room):
        return []
    with _lock, _conn(db_path) as con:
        rows = con.execute(
            "SELECT client_id FROM fcm_tokens WHERE room = ?", (room,)
        ).fetchall()
    return [r['client_id'] for r in rows]


def remove_token(db_path: str, token: str):
    if not (db_path and token):
        return
    with _lock, _conn(db_path) as con:
        con.execute("DELETE FROM fcm_tokens WHERE token = ?", (token,))


def remove_client(db_path: str, room: str, client_id: str):
    if not (db_path and room and client_id):
        return
    with _lock, _conn(db_path) as con:
        con.execute("DELETE FROM fcm_tokens WHERE room = ? AND client_id = ?", (room, client_id))

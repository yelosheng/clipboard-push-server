# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the server (development):**
```bash
python relay_server.py
```

**Run with Docker:**
```bash
docker-compose up -d
```

**Run tests:**
```bash
python -m pytest tests/
# or a single test file:
python -m unittest tests/test_favicon.py
```

**Install dependencies:**
```bash
pip install -r requirements.txt
```

## Architecture

This is a **Flask + Flask-SocketIO relay server** for the Clipboard Push app. It relays AES-256-GCM-encrypted clipboard content and files between mobile and PC clients without ever decrypting the payload.

### Entry point
`relay_server.py` → imports `app` and `socketio` from `app/__init__.py` and runs on port 5055.

### Module structure

- **`app/__init__.py`** — Application factory. Wires everything together: creates Flask app, SocketIO, login manager, boto3 S3 client (for R2), and registers routes/socket events. Also manages connection history recording and the 60-minute cleanup background task.
- **`app/signal_core.py`** — All in-memory state and core signaling logic. Holds the global dicts (`CLIENT_SESSIONS`, `CLIENT_ROOMS`, `ROOM_CLIENT_ORDER`, etc.) and all functions that operate on them (room capacity enforcement, LAN probe coordination, transfer state machine). Uses `bind_runtime()` to receive the socketio/logger references from `__init__.py` since it is imported before the app is created.
- **`app/socket_events.py`** — Registers all Socket.IO event handlers (`join`, `leave`, `disconnect`, `clipboard_push`, `file_*`, `lan_probe_result`, etc.) via `register_socket_events()`. Dependencies are injected as keyword arguments to avoid circular imports.
- **`app/route.py`** — HTTP routes registered via `register_routes()`: login/logout, dashboard, file upload/download APIs, settings CRUD, `/api/relay` (HTTP→Socket.IO bridge), and history endpoints.
- **`app/settings.py`** — Reads all env vars; raises `RuntimeError` if `FLASK_SECRET_KEY` is missing in production.
- **`app/auth.py`** — Single-user (`admin`) Flask-Login setup and bcrypt password verification. Password hash stored in `data/admin_password.hash`.

### Services (`app/services/`)

- **`history_db.py`** — SQLite-based connection history. Stores `clients` and `connection_events` tables in `data/history.db`. Thread-safe with a module-level lock.
- **`r2_service.py`** — Cloudflare R2 (S3-compatible) bucket operations: usage stats and bulk delete.
- **`local_storage_service.py`** — Local disk file storage alternative. Files stored as `<timestamp>_<filename>` with a sidecar `.meta` file for content-type.
- **`geo_service.py`** — IP geolocation for history records (async, best-effort).
- **`fcm_service.py`** — Optional Firebase Cloud Messaging for push delivery to killed Android apps. No-op if `FIREBASE_CREDENTIALS_PATH` is unset.

### Settings persistence
Settings can be changed live from the dashboard. They are saved to `data/settings.env` (not `.env`) so the file works on read-only mounts. At startup, `.env` is loaded first, then `data/settings.env` overrides it.

### File transfer flow
1. Sender emits `file_available` → server checks LAN state
2. If `PAIR_SAME_LAN`: relays to receiver → starts a decision timeout background task
3. Receiver responds with `file_sync_completed` (LAN success) or `file_need_relay` (fallback)
4. On fallback: server emits `transfer_command {action: upload_relay}` to sender, who uploads to R2 or local storage
5. Protocol version `4.0` is enforced on file transfer events

### Admin dashboard
Accessible at `/dashboard` (login required). Connects via Socket.IO to `dashboard_room` for live client list, room states, and activity log. Charts and history are at `/history`.

### Key env vars
| Variable | Purpose |
|---|---|
| `FLASK_SECRET_KEY` | Required in production |
| `ADMIN_PASSWORD` | Initial admin password |
| `STORAGE_BACKEND` | `r2` (default) or `local` |
| `SIGNAL_DEBUG_ENABLED` | Set to `1` for verbose signal logging |
| `FIREBASE_CREDENTIALS_PATH` | Path to Firebase service account JSON |

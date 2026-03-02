# Clipboard Push Server

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](https://www.python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-lightgrey.svg)](https://flask.palletsprojects.com)
[![Docker](https://img.shields.io/badge/Docker-supported-2496ED.svg)](https://www.docker.com)

A self-hosted relay server for the [Clipboard Push](https://clipboardpush.com) app. It relays encrypted clipboard text and files between mobile and PC clients in real time — over the internet via the relay, or directly over LAN when both devices are on the same network. All clipboard content is AES-256-GCM encrypted on the device before transmission; the server only sees ciphertext.

## Features

- **Real-time clipboard push** via Socket.IO (text and files)
- **LAN-first file transfer** — direct device-to-device when on the same network, with automatic cloud fallback
- **AES-256-GCM end-to-end encryption** — the server never sees plaintext clipboard content
- **Flexible file storage** — Cloudflare R2 (pre-signed URLs) or local disk; switchable from the dashboard
- **Admin dashboard** — view connected devices, room states, transfer activity, live logs, and storage stats
- **In-dashboard settings** — configure storage backend, R2 credentials, and server options without editing files
- **Room-based routing** — up to 2 devices per room; oldest device is evicted when limit exceeded
- **Automatic storage cleanup** — files purged every 60 minutes (R2 bucket or local uploads folder)
- **Docker support** — single `docker-compose up` deployment

## Quick Start (Docker)

```bash
git clone https://github.com/yelosheng/clipboard-push-server.git
cd clipboard-push-server
cp .env.example .env
# Edit .env and fill in your values (see Configuration section)
docker-compose up -d
```

The server starts on port `5055` by default.

## Manual / Other Deployment Options

See [DEPLOY.md](DEPLOY.md) for full guides covering Linux (Debian/Ubuntu/CentOS), macOS local dev, Nginx reverse proxy, SSL, and systemd setup.

## Configuration

Copy `.env.example` to `.env` and fill in the following:

| Variable | Required | Description |
|---|---|---|
| `FLASK_SECRET_KEY` | Yes | Random secret for Flask sessions. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | Yes | Initial admin dashboard password (hashed on first use) |
| `STORAGE_BACKEND` | No | `r2` (default) or `local` — where to store relay files |
| `LOCAL_STORAGE_PATH` | If `local` | Absolute path for uploaded files (default: `data/uploads`) |
| `LOCAL_STORAGE_BASE_URL` | If `local` | Public base URL of this server, used in download links (e.g. `https://your.domain.com`) |
| `R2_ACCOUNT_ID` | If `r2` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | If `r2` | R2 API token key ID |
| `R2_SECRET_ACCESS_KEY` | If `r2` | R2 API token secret |
| `R2_BUCKET_NAME` | If `r2` | R2 bucket name for file storage |
| `DASHBOARD_R2_BUCKET` | If `r2` | R2 bucket name shown in dashboard stats (can be same as above) |
| `FLASK_DEBUG` | No | Set to `1` for debug mode (never use in production) |

**Text-only mode:** If you don't configure any storage backend, the server works fine for clipboard text sync. File transfer will be unavailable.

**Local storage mode:** Set `STORAGE_BACKEND=local` to store relay files on the server's own disk instead of R2. No cloud account needed. The dashboard shows the current file count and lets you clear all files manually.

**Automatic storage cleanup:** Every 60 minutes the server purges all relay files — deletes all R2 objects (when using R2) or all files in `LOCAL_STORAGE_PATH` (when using local). Transferred files are only needed briefly, so this keeps storage usage near zero.

> Settings can also be changed live from the **Settings** button in the dashboard without editing `.env` directly. Changes take effect after a server restart (there is a Restart button in the settings panel).

## Architecture

```
Mobile App  ── Socket.IO (AES-256-GCM encrypted) ──► Relay Server ◄── Socket.IO (AES-256-GCM encrypted) ──  PC Client
                                                            │
                                                            └── R2 or local disk (file storage, optional)
```

- Clients connect to a shared **room** (identified by a room ID you set in the app)
- Text clipboard content is **AES-256-GCM encrypted** on the device — the server relays ciphertext only
- For files, the server orchestrates a **LAN-first pull** flow: the sender serves the encrypted file over HTTP, the receiver pulls it directly over LAN; if that fails, the file is uploaded to R2 and downloaded via pre-signed URL. Both mobile→PC and PC→mobile directions are supported.
- The admin dashboard is accessible at `http://your-server:5055/dashboard` (login with `ADMIN_PASSWORD`)

### Protocol Version

Current protocol version: `4.0`

Clients must include `"protocol_version": "4.0"` in file transfer events. See [RELAY_SERVER_API.md](RELAY_SERVER_API.md) for the full Socket.IO and HTTP API reference.

## Clients

| Client | Link |
|---|---|
| Android | [Google Play](https://play.google.com/store/apps/details?id=com.clipboardpush.plus) — [source on GitHub](https://github.com/yelosheng/clipboard-push-android) |
| PC (Windows) | [GitHub](https://github.com/yelosheng/clipboard-push-win32) · [Releases](https://github.com/yelosheng/clipboard-push-win32/releases) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache 2.0 — see [LICENSE](LICENSE).

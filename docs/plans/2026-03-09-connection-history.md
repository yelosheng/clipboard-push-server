# Connection History Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record every real client connection (excluding dashboard browser), show a `/history` page with unique client list, geo-location, peak analysis, and daily trend charts.

**Architecture:** SQLite `data/history.db` holds two tables: `clients` (one row per unique device) and `connection_events` (one row per session). Geo-lookup uses ip-api.com (free, no key) with in-memory cache. The `on_join` hook in `socket_events.py` records connections; `on_disconnect` closes them. A new `/history` route serves the page and JSON APIs consumed by Chart.js.

**Tech Stack:** Python sqlite3 (stdlib), requests (already installed), Chart.js 4 (CDN), existing Flask/Jinja2/Tailwind dark-theme templates.

---

## Key Design Decisions

- **Record point:** `on_join`, not `on_connect` — we need `client_id`, `device_name`, `room` which only arrive with the join payload.
- **Dashboard exclusion:** skip recording when `room == 'dashboard_room'` or `client_id` is absent.
- **IP extraction:** prefer `X-Forwarded-For` header (nginx proxy), fall back to `request.remote_addr`.
- **Private IPs:** `127.x`, `10.x`, `192.168.x`, `172.16-31.x` → skip ip-api call, store `country='Local'`, `city='Local Network'`.
- **Geo lookup:** background thread so it doesn't block the join response; updates DB row after lookup completes.
- **Active event tracking:** in-memory dict `_ACTIVE_SID_EVENTS: dict[sid, event_id]` maps an open session to its DB row for closure on disconnect.
- **Callbacks pattern:** pass `record_join` and `record_disconnect` as keyword args into `register_socket_events`, same pattern as rest of codebase.

---

## Task 1: Database service

**Files:**
- Create: `app/services/history_db.py`

**Step 1: Create the file with schema + helpers**

```python
# app/services/history_db.py
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
    """Returns the new event row id."""
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


# ── Query helpers ──────────────────────────────────────────────────────────────

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


def query_clients(db_path, search='', limit=200, offset=0) -> list:
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
    """Returns list of {hour: 0-23, count: n} for all time."""
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
    """Returns list of {date: 'YYYY-MM-DD', count: n} for last N days."""
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
    """Returns [{country, country_code, count}] sorted by count desc."""
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
```

**Step 2: Commit**

```bash
git add app/services/history_db.py
git commit -m "feat: add SQLite history_db service (schema + query helpers)"
```

---

## Task 2: Geo-lookup service

**Files:**
- Create: `app/services/geo_service.py`

**Step 1: Create the file**

```python
# app/services/geo_service.py
import ipaddress
import threading
import requests

_cache: dict[str, dict] = {}
_lock = threading.Lock()
_LOCAL_RESULT = {'country': 'Local', 'country_code': '', 'region': 'Local Network', 'city': 'Local Network'}
_UNKNOWN_RESULT = {'country': '', 'country_code': '', 'region': '', 'city': ''}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
]


def _is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def lookup_ip(ip: str) -> dict:
    """Returns geo dict; never raises."""
    if not ip or _is_private(ip):
        return _LOCAL_RESULT.copy()
    with _lock:
        if ip in _cache:
            return _cache[ip].copy()
    try:
        resp = requests.get(
            f'http://ip-api.com/json/{ip}',
            params={'fields': 'status,country,countryCode,regionName,city'},
            timeout=5,
        )
        data = resp.json()
        if data.get('status') == 'success':
            result = {
                'country': data.get('country', ''),
                'country_code': data.get('countryCode', ''),
                'region': data.get('regionName', ''),
                'city': data.get('city', ''),
            }
        else:
            result = _UNKNOWN_RESULT.copy()
    except Exception:
        result = _UNKNOWN_RESULT.copy()
    with _lock:
        _cache[ip] = result
    return result.copy()


def get_client_ip(request) -> str:
    """Extract real IP from request, honouring X-Forwarded-For."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or ''
```

**Step 2: Commit**

```bash
git add app/services/geo_service.py
git commit -m "feat: add ip-api.com geo-lookup service with private-IP guard and cache"
```

---

## Task 3: Wire up recording into socket_events

**Files:**
- Modify: `app/socket_events.py`
- Modify: `app/__init__.py`

**Step 1: Add `record_join` and `record_disconnect` params to `register_socket_events`**

In `app/socket_events.py`, add to the function signature (after `instruct_finish,`):

```python
    record_join=None,
    record_disconnect=None,
```

**Step 2: Call `record_join` in `on_join`**

At the end of `on_join`, after the log line `emit_activity_log('join', ...)`, add:

```python
        if record_join and room and room != 'dashboard_room' and client_id:
            record_join(
                client_id=client_id,
                device_name=CLIENT_DEVICE_NAMES.get(client_id, client_id),
                client_type=client_type,
                room_id=room,
            )
```

Note: `request` is already imported at the top of the file.

**Step 3: Call `record_disconnect` in `on_disconnect`**

In `on_disconnect`, after `detach_sid_from_tracking`, add:

```python
        if record_disconnect:
            record_disconnect(sid=request.sid)
```

**Step 4: Add DB init + callback factories in `app/__init__.py`**

Import the new services at top:
```python
from .services.history_db import (
    init_db as history_init_db,
    upsert_client as history_upsert_client,
    insert_event as history_insert_event,
    close_event as history_close_event,
    update_client_geo as history_update_client_geo,
)
from .services.geo_service import get_client_ip, lookup_ip as geo_lookup_ip
```

Add after `BASE_DIR` definition:
```python
HISTORY_DB_PATH = os.path.join(BASE_DIR, 'data', 'history.db')
history_init_db(HISTORY_DB_PATH)

# sid → event_id for open sessions
_ACTIVE_SID_EVENTS: dict = {}
_ACTIVE_SID_LOCK = threading.Lock()
```

Add `import threading` near the top imports.

Add factory functions before `register_socket_events(...)`:

```python
def _record_join(*, client_id, device_name, client_type, room_id):
    from flask import request as _req
    ip = get_client_ip(_req)
    sid = _req.sid  # Flask-SocketIO adds .sid to request in socket context
    event_id = history_insert_event(
        HISTORY_DB_PATH, client_id, device_name, room_id, client_type, ip
    )
    history_upsert_client(
        HISTORY_DB_PATH, client_id, device_name, client_type, room_id, ip
    )
    with _ACTIVE_SID_LOCK:
        _ACTIVE_SID_EVENTS[sid] = (event_id, client_id)

    def _geo_update():
        geo = geo_lookup_ip(ip)
        if geo.get('country'):
            history_update_client_geo(
                HISTORY_DB_PATH, client_id,
                geo['country'], geo['country_code'], geo['region'], geo['city'],
            )
    socketio.start_background_task(_geo_update)


def _record_disconnect(*, sid):
    with _ACTIVE_SID_LOCK:
        entry = _ACTIVE_SID_EVENTS.pop(sid, None)
    if entry:
        event_id, _ = entry
        history_close_event(HISTORY_DB_PATH, event_id)
```

**Step 5: Pass callbacks into `register_socket_events`**

Add to the `register_socket_events(...)` call:
```python
    record_join=_record_join,
    record_disconnect=_record_disconnect,
```

**Step 6: Commit**

```bash
git add app/socket_events.py app/__init__.py
git commit -m "feat: record client join/disconnect events into history.db"
```

---

## Task 4: History API routes

**Files:**
- Modify: `app/route.py`

**Step 1: Add `HISTORY_DB_PATH` and query imports to `register_routes` signature**

Add to the keyword params (after `DOTENV_PATH`):
```python
    HISTORY_DB_PATH=None,
    history_query_summary=None,
    history_query_clients=None,
    history_query_hourly=None,
    history_query_daily=None,
    history_query_countries=None,
```

**Step 2: Add routes at end of `register_routes` function body**

```python
    @app.route('/history')
    @login_required
    def history_page():
        return render_template('history.html')

    @app.route('/api/history/summary')
    @login_required
    def api_history_summary():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        return jsonify(history_query_summary(HISTORY_DB_PATH))

    @app.route('/api/history/clients')
    @login_required
    def api_history_clients():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        search = request.args.get('search', '').strip()
        limit = min(int(request.args.get('limit', 100)), 500)
        offset = int(request.args.get('offset', 0))
        rows, total = history_query_clients(HISTORY_DB_PATH, search=search, limit=limit, offset=offset)
        return jsonify({'clients': rows, 'total': total})

    @app.route('/api/history/hourly')
    @login_required
    def api_history_hourly():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        return jsonify(history_query_hourly(HISTORY_DB_PATH))

    @app.route('/api/history/daily')
    @login_required
    def api_history_daily():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        days = int(request.args.get('days', 30))
        return jsonify(history_query_daily(HISTORY_DB_PATH, days=days))

    @app.route('/api/history/countries')
    @login_required
    def api_history_countries():
        if not HISTORY_DB_PATH:
            return jsonify({'error': 'history not configured'}), 503
        return jsonify(history_query_countries(HISTORY_DB_PATH))
```

**Step 3: Pass new params from `__init__.py`**

In `register_routes(...)` call, add:
```python
    HISTORY_DB_PATH=HISTORY_DB_PATH,
    history_query_summary=history_query_summary_fn,
    history_query_clients=history_query_clients_fn,
    history_query_hourly=history_query_hourly_fn,
    history_query_daily=history_query_daily_fn,
    history_query_countries=history_query_countries_fn,
```

And in `__init__.py`, import and alias:
```python
from .services.history_db import (
    ...
    query_summary   as history_query_summary_fn,
    query_clients   as history_query_clients_fn,
    query_hourly    as history_query_hourly_fn,
    query_daily     as history_query_daily_fn,
    query_countries as history_query_countries_fn,
)
```

**Step 4: Commit**

```bash
git add app/route.py app/__init__.py
git commit -m "feat: add /history page route and /api/history/* JSON endpoints"
```

---

## Task 5: Header link in base.html + dashboard.html

**Files:**
- Modify: `templates/base.html` (if it has a nav) OR `templates/dashboard.html` header-right div

**Step 1: Add History link to `header-right` in `dashboard.html`**

In the `header-right` div, before the Settings button, add:
```html
<a class="btn btn-outline btn-mini" href="{{ url_for('history_page') }}">History</a>
```

**Step 2: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat: add History nav link to dashboard header"
```

---

## Task 6: history.html template

**Files:**
- Create: `templates/history.html`

**Step 1: Create the template**

Extend `base.html`. Structure:

```
{% extends "base.html" %}
{% block title %}Connection History | Clipboard Man{% endblock %}

Page layout:
  - view-header: "Connection History" title + "← Dashboard" back link
  - stats-grid (3 cards): Unique Clients / Total Sessions / Countries
  - Two charts side by side:
      Left: "Peak Hours" – bar chart (0–23h, session count)
      Right: "Daily Trend" – line chart (last 30 days)
  - Country bar chart: horizontal bars, top 15
  - Client table: search input + table
      Columns: Device | Type | Location | Room | First Seen | Last Seen | Sessions
  - Load Chart.js from CDN
  - Inline <script> calls /api/history/* endpoints and renders
```

Full HTML is long — see static/js/history.js for the JS logic. The template itself is mostly HTML skeleton + `<canvas>` elements.

Key elements to include:
```html
<!-- Stats -->
<p class="stat-number" id="h-unique-clients">-</p>
<p class="stat-number" id="h-total-sessions">-</p>
<p class="stat-number" id="h-countries">-</p>

<!-- Charts -->
<canvas id="chart-hourly"></canvas>
<canvas id="chart-daily"></canvas>
<canvas id="chart-countries"></canvas>

<!-- Table -->
<input type="search" id="h-search" placeholder="Search device, location, room...">
<tbody id="h-client-tbody"></tbody>
<div id="h-pagination"></div>
```

**Step 2: Commit stub template first**

```bash
git add templates/history.html
git commit -m "feat: add history.html template skeleton"
```

---

## Task 7: history.js frontend logic

**Files:**
- Create: `static/js/history.js`

**Step 1: Create the JS**

```javascript
// static/js/history.js
document.addEventListener('DOMContentLoaded', () => {
    // ── Chart.js defaults ──
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';

    const CHART_BAR_COLOR = 'rgba(99,179,237,0.75)';
    const CHART_LINE_COLOR = '#63b3ed';

    // ── Load summary stats ──
    fetch('/api/history/summary').then(r => r.json()).then(d => {
        document.getElementById('h-unique-clients').textContent = (d.unique_clients ?? '-').toLocaleString();
        document.getElementById('h-total-sessions').textContent = (d.total_sessions ?? '-').toLocaleString();
        document.getElementById('h-countries').textContent = (d.countries ?? '-').toLocaleString();
    });

    // ── Hourly peak chart ──
    fetch('/api/history/hourly').then(r => r.json()).then(data => {
        new Chart(document.getElementById('chart-hourly'), {
            type: 'bar',
            data: {
                labels: data.map(d => `${String(d.hour).padStart(2,'0')}:00`),
                datasets: [{
                    label: 'Sessions',
                    data: data.map(d => d.count),
                    backgroundColor: CHART_BAR_COLOR,
                    borderRadius: 4,
                }]
            },
            options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
        });
    });

    // ── Daily trend chart ──
    fetch('/api/history/daily?days=30').then(r => r.json()).then(data => {
        new Chart(document.getElementById('chart-daily'), {
            type: 'line',
            data: {
                labels: data.map(d => d.date),
                datasets: [{
                    label: 'Sessions',
                    data: data.map(d => d.count),
                    borderColor: CHART_LINE_COLOR,
                    backgroundColor: 'rgba(99,179,237,0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                }]
            },
            options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
        });
    });

    // ── Countries chart ──
    fetch('/api/history/countries').then(r => r.json()).then(data => {
        new Chart(document.getElementById('chart-countries'), {
            type: 'bar',
            data: {
                labels: data.map(d => d.country),
                datasets: [{
                    label: 'Clients',
                    data: data.map(d => d.count),
                    backgroundColor: CHART_BAR_COLOR,
                    borderRadius: 4,
                }]
            },
            options: {
                indexAxis: 'y',
                plugins: { legend: { display: false } },
                scales: { x: { beginAtZero: true } }
            }
        });
    });

    // ── Client table ──
    let currentSearch = '';
    let currentOffset = 0;
    const PAGE_SIZE = 50;

    function loadClients() {
        const params = new URLSearchParams({
            search: currentSearch,
            limit: PAGE_SIZE,
            offset: currentOffset,
        });
        fetch(`/api/history/clients?${params}`).then(r => r.json()).then(data => {
            renderTable(data.clients);
            renderPagination(data.total);
        });
    }

    function renderTable(clients) {
        const tbody = document.getElementById('h-client-tbody');
        if (!clients.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">No records found.</td></tr>';
            return;
        }
        tbody.innerHTML = clients.map(c => {
            const location = [c.city, c.region, c.country].filter(Boolean).join(', ') || '—';
            const typeLabel = (c.client_type || 'unknown').toUpperCase();
            return `<tr>
                <td>${escHtml(c.device_name || c.client_id)}</td>
                <td><span class="type-chip"><span class="type-glyph">${escHtml(typeLabel.slice(0,2))}</span> ${escHtml(typeLabel)}</span></td>
                <td>${escHtml(location)}</td>
                <td>${escHtml(c.room_id || '—')}</td>
                <td class="text-meta">${escHtml((c.first_seen || '').replace('T',' ').slice(0,16))}</td>
                <td class="text-meta">${escHtml((c.last_seen  || '').replace('T',' ').slice(0,16))}</td>
                <td>${c.total_sessions}</td>
            </tr>`;
        }).join('');
    }

    function renderPagination(total) {
        const pages = Math.ceil(total / PAGE_SIZE);
        const current = Math.floor(currentOffset / PAGE_SIZE);
        const el = document.getElementById('h-pagination');
        if (pages <= 1) { el.innerHTML = ''; return; }
        let btns = '';
        for (let i = 0; i < pages; i++) {
            btns += `<button class="btn btn-outline btn-mini ${i===current?'active':''}" data-page="${i}">${i+1}</button>`;
        }
        el.innerHTML = btns;
        el.querySelectorAll('button[data-page]').forEach(btn => {
            btn.addEventListener('click', () => {
                currentOffset = parseInt(btn.dataset.page) * PAGE_SIZE;
                loadClients();
            });
        });
    }

    function escHtml(s) {
        return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    document.getElementById('h-search')?.addEventListener('input', e => {
        currentSearch = e.target.value.trim();
        currentOffset = 0;
        loadClients();
    });

    loadClients();
});
```

**Step 2: Include in history.html**

In `{% block scripts %}`:
```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="{{ url_for('static', filename='js/history.js') }}?v=20260309a"></script>
```

**Step 3: Commit**

```bash
git add static/js/history.js templates/history.html
git commit -m "feat: history page frontend with Chart.js peak/trend/country charts and client table"
```

---

## Task 8: Final wiring + smoke test

**Step 1: Restart server, open `/history`**

Verify:
- Page loads without 500 error
- Stats cards show numbers (may be 0 if no connections yet)
- Connect a real Android client → row appears in table after refresh
- Geo updates (country/city) appear within ~5 seconds

**Step 2: Commit any fixes, then push**

```bash
git add -A
git commit -m "fix: history page wiring corrections"
git push origin master
```

---

## Notes

- `data/history.db` is in `.gitignore` via `data/` — confirm this is already ignored.
- `requests` library is already in `requirements.txt` (used by other services).
- Chart.js loaded from CDN; if offline deployment needed, download and serve from `static/`.
- `request.sid` in Flask-SocketIO context: available as `flask.request.sid` inside a socket event handler. Since `_record_join` is called from within `on_join`, `flask.request` is the SocketIO request context.

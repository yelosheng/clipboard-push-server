# Clipboard Push Server — API Reference

Version: 2026-02-14
Corresponds to: `app/socket_events.py`, `app/route.py`

## 1. Scope

This document is intended for client developers. It describes the server's actual implementation:

- HTTP endpoints
- Socket.IO events and fields
- Room capacity and eviction logic
- LAN probe and transfer orchestration
- Error codes and client handling recommendations

Dashboard UI details (login/dashboard frontend) are not covered here.

## 2. Basics

- Base URL: `http://your-server:5055`
- Socket.IO: default namespace `/` (same origin)
- Dashboard login is protected; business Socket/HTTP endpoints require no authentication
- Key constants:
  - `PROTOCOL_VERSION = "4.0"`
  - `ROOM_MAX_PEERS = 2`
  - `DEFAULT_PROBE_TIMEOUT_MS = 1200`
  - `TRANSFER_DECISION_TIMEOUT_MS_DEFAULT = 15000`
  - `TRANSFER_DECISION_TIMEOUT_MS_MAX = 30000`

## 3. Session and Identity Model

The server tracks devices by `client_id` and connections by `sid`:

- One `client_id` may have multiple `sid`s (multiple tabs/windows)
- `CLIENT_ROOMS[client_id]` represents the device's current room
- `ROOM_CLIENT_ORDER[room]` maintains device join order within a room (used for capacity enforcement)

`join` only registers device identity when `client_id` is provided. Without `client_id`, the socket joins the room but is not added to device tracking.

## 4. Room Capacity and Eviction

### 4.1 When It Triggers

During `join`, if the number of unique devices in a room exceeds 2, the server calls `enforce_room_capacity(room)`.

### 4.2 Eviction Candidate Selection

The server's `choose_eviction_candidate` rules:

1. Prefer evicting non-PC client types (`not is_pc_client_type`)
2. If all are PC types, evict the device that joined earliest

PC types include: `pc / windows / macos / linux / cli / web`
App types include: `app / android / ios`

### 4.3 Eviction Actions

For the evicted `client_id`, the server:

1. Sends `peer_evicted` to all of that device's sids
2. Forces those sids to `leave_room(room)`
3. Cleans up device tracking (sessions, type, room, network, probe state, etc.)
4. Records an `activity_log` entry (`peer_evicted`)

### 4.4 What the Evicted Client Must Do

Upon receiving `peer_evicted`, the client must:

- Immediately stop sending business traffic (clipboard/file) for that room
- Clear local room active state
- To resume, explicitly re-send `join` (do not assume auto-recovery)

Example `peer_evicted` payload:

```json
{
  "protocol_version": "4.0",
  "room": "room-1",
  "evicted_client_id": "app_001",
  "reason": "room_capacity_exceeded",
  "evicted_at_ms": 1770000000000
}
```

## 5. HTTP API

### 5.1 `POST /api/file/upload_auth`

Purpose: Obtain a pre-signed R2 upload URL and download URL.

Request:

```json
{
  "filename": "example.png",
  "content_type": "image/png"
}
```

Success `200`:

```json
{
  "upload_url": "https://...",
  "download_url": "https://...",
  "file_key": "1700000000_example.png",
  "expires_in": 300
}
```

Errors:

- `400`: `{"error": "Filename required"}`
- `500`: `{"error": "..."}`

### 5.2 `POST /api/relay`

Purpose: Stateless HTTP relay for Socket.IO events.

Request:

```json
{
  "room": "room-1",
  "event": "clipboard_sync",
  "data": {"room": "room-1", "content": "..."},
  "sender_id": "pc_001"
}
```

Behavior:

- Required fields: `room`, `event`, `data` (must not be null)
- If `sender_id` or `client_id` is online, the server uses `skip_sid` to skip all their sids, preventing loopback

Responses:

- `200`: `{"status": "ok"}`
- `400`: `{"error": "Missing room, event, or data"}`
- `500`: `{"error": "..."}`

## 6. Socket Connection Lifecycle

### 6.1 `connect`

- Server broadcasts `server_stats` to `dashboard_room` (new connection)

### 6.2 `disconnect`

- Server removes the current sid from tracking
- If that `client_id` has no remaining sids:
  - Clean up that client
  - Clear `ROOM_LAST_PROBE` for the associated room
  - Broadcast `room_stats`
  - Broadcast `room_state_changed`
  - Attempt to re-trigger LAN probe if conditions are met
- Broadcast `client_list_update` and `server_stats` to dashboard

### 6.3 `join`

Minimum recommended payload:

```json
{
  "room": "room-1",
  "client_id": "pc_001",
  "client_type": "pc"
}
```

Optional fields:

- `network`: `private_ip / cidr / network_id_hash / network_epoch`
- `probe`: `probe_url / probe_ttl_ms`

Key behavior:

- First calls `join_room(room)` and emits `status`
- If `room == dashboard_room`: sends targeted `client_list_update` and `room_states_snapshot`
- If `client_id` is present but `client_type` is missing: `error(E_BAD_SCHEMA)`
- If the device is migrating from another room: the old room broadcasts state changes
- Finally: enforce capacity, broadcast `room_stats` and `room_state_changed`, trigger `lan_probe_request` if applicable

### 6.4 `leave`

Request: `{"room": "room-1"}`
Behavior: leaves the room, sends `status`, updates room stats and state.

## 7. Room State and LAN Probe

### 7.1 Room State — `room_state_changed`

State enum:

- `EMPTY`
- `SINGLE`
- `PAIR_UNKNOWN`
- `PAIR_SAME_LAN`
- `PAIR_DIFF_LAN`

Example:

```json
{
  "protocol_version": "4.0",
  "room": "room-1",
  "max_peers": 2,
  "state": "PAIR_SAME_LAN",
  "same_lan": true,
  "lan_confidence": "confirmed",
  "peers": [
    {"client_id": "pc_001", "client_type": "pc", "joined_at_ms": 1, "last_seen_ms": 2, "network_epoch": 3},
    {"client_id": "app_001", "client_type": "app", "joined_at_ms": 1, "last_seen_ms": 2, "network_epoch": 4}
  ],
  "last_probe": {
    "probe_id": "pr_...",
    "status": "ok",
    "latency_ms": 32,
    "checked_at_ms": 1770000000000,
    "reason": ""
  }
}
```

### 7.2 `peer_network_update`

Purpose: Update network metadata and trigger recalculation.

Errors:

- `E_ROLE_DENIED`: cannot resolve `client_id`
- `E_TRANSFER_STATE`: `client_id` does not belong to the declared room

### 7.3 `lan_probe_request` / `lan_probe_result`

`lan_probe_request` is triggered when:

- The room has exactly 2 devices
- At least one PC and one App
- The PC provides a valid private-network `probe_url` (host matches `network.private_ip`, must be `http`)

If validation fails, the room records a probe failure and broadcasts `room_state_changed`.

`lan_probe_result`:

- Must include `room` and `probe_id`
- `probe_id` must exist and match the room, otherwise `E_PROBE_STALE`
- `result` values other than `ok / fail / timeout` are normalized to `fail`

## 8. Text and File Events

### 8.1 Simple Relay Events

| Client sends | Server broadcasts |
|---|---|
| `clipboard_push` | `clipboard_sync` |
| `file_push` | `file_sync` |
| `file_announcement` | `file_announcement` |
| `file_ack` | `file_ack` |
| `file_request_relay` | `file_request_relay` |

All use `include_self=false`.

### 8.2 Orchestrated File Transfer Events (Protocol v4.0)

#### 8.2.1 `file_available`

Purpose: Initiates LAN-first transfer.

Validation:

- `room` must be resolvable
- If `protocol_version` is present, it must equal `"4.0"`, otherwise `E_BAD_VERSION`
- Sender must still be in the room and registered, otherwise `E_ROLE_DENIED`

Server behavior:

- Creates or reuses a transfer context
- If room state is `PAIR_DIFF_LAN`: immediately issues `transfer_command(upload_relay)`
- Otherwise: forwards `file_available` and sets transfer state to `waiting_result`
- Starts a decision timeout task; on timeout, automatically issues `upload_relay`

#### 8.2.2 `file_sync_completed`

Same validation as above (version/permissions).

Behavior:

- Forwards `file_sync_completed`
- If a transfer context exists: issues `transfer_command(finish)`

#### 8.2.3 `file_need_relay`

Same validation as above (version/permissions).

Behavior:

- Forwards `file_need_relay`
- If a transfer context exists: issues `transfer_command(upload_relay)`
- Also sends a compatibility `file_need_relay` to the sender's sid

## 9. Transfer State Machine (Server Internal)

Common states:

- `created`
- `waiting_result`
- `fallback_requested`
- `fallback_timeout`
- `lan_success`

Notes:

- Once `upload_relay` is triggered, it will not be triggered again
- Once `finish` is triggered, it will not be triggered again

## 10. Server-Emitted Events

General:

- `status`
- `error`
- `room_stats`
- `room_state_changed`
- `peer_evicted`

Orchestration:

- `lan_probe_request`
- `transfer_command`
- `file_available`
- `file_sync_completed`
- `file_need_relay`

Dashboard:

- `client_list_update`
- `room_states_snapshot`
- `server_stats`
- `activity_log`

## 11. Error Codes

| Code | Meaning |
|---|---|
| `E_BAD_SCHEMA` | Missing or invalid required field |
| `E_BAD_VERSION` | `protocol_version` is not `"4.0"` |
| `E_ROLE_DENIED` | `client_id` not found or not in the room |
| `E_TRANSFER_STATE` | Transfer context conflict or invalid state |
| `E_PROBE_STALE` | `probe_id` not found or room mismatch |

Client recommendations:

- Protocol/field errors: fix and resend
- Permission errors: rebuild session with `join` first
- Stale probe: ignore and wait for the next probe cycle

## 12. JavaScript Integration Example

```js
import { io } from "socket.io-client";

const room = "room-1";
const clientId = "pc_001";

const socket = io("http://your-server:5055", {
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 500,
  reconnectionDelayMax: 5000,
});

socket.on("connect", () => {
  socket.emit("join", {
    protocol_version: "4.0",
    room,
    client_id: clientId,
    client_type: "pc",
  });
});

socket.on("peer_evicted", (msg) => {
  // Critical: stop sending immediately after eviction
  console.warn("evicted", msg);
  // Mark local room as unavailable; wait for user to re-join
});

socket.on("transfer_command", (cmd) => {
  if (cmd.action === "upload_relay") {
    // Call /api/file/upload_auth then PUT to upload URL
  }
  if (cmd.action === "finish") {
    // Mark transfer as complete
  }
});

socket.on("error", (e) => {
  console.error("server error", e);
});
```

## 13. Integration Checklist

- After reconnect, always re-send `join`
- Use a stable, consistent `client_id`
- Implement `peer_evicted` — stop sending immediately
- Implement `transfer_command(upload_relay / finish)`
- Handle `error.code` appropriately
- Include `protocol_version: "4.0"` in all v4 orchestration events

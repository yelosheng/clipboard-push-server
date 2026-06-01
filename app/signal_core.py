import ipaddress
import json
import os
import time
from urllib.parse import urlparse
from uuid import uuid4

from flask import request
from flask_socketio import emit


socketio = None
logger = None


def bind_runtime(runtime_socketio, runtime_logger):
    global socketio, logger
    socketio = runtime_socketio
    logger = runtime_logger


CLIENT_SESSIONS = {}
CLIENT_ROOMS = {}
CLIENT_TYPES = {}
CLIENT_DEVICE_NAMES = {}
ROOM_CLIENT_ORDER = {}
CLIENT_JOINED_AT_MS = {}
CLIENT_LAST_SEEN_MS = {}
CLIENT_NETWORK_META = {}
CLIENT_PROBE_META = {}
ROOM_LAST_PROBE = {}
PENDING_LAN_PROBES = {}

ROOM_MAX_PEERS = 2
PROTOCOL_VERSION = '4.0'
DEFAULT_PROBE_TIMEOUT_MS = 1200
SIGNAL_DEBUG_ENABLED = os.environ.get('SIGNAL_DEBUG_ENABLED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
SIGNAL_DEBUG_MAX_CHARS = int(os.environ.get('SIGNAL_DEBUG_MAX_CHARS', '800') or 800)
TRANSFER_DECISION_TIMEOUT_MS_DEFAULT = int(os.environ.get('TRANSFER_DECISION_TIMEOUT_MS_DEFAULT', '3000') or 3000)
TRANSFER_DECISION_TIMEOUT_MS_MAX = int(os.environ.get('TRANSFER_DECISION_TIMEOUT_MS_MAX', '30000') or 30000)

TRANSFER_CONTEXTS = {}

ALLOWED_ACTIVITY_TYPES = {
    'clipboard',
    'file',
    'api_relay',
    'file_announcement',
    'file_ack',
    'file_request_relay',
    'file_available',
    'file_sync_completed',
    'file_need_relay',
    'room_state_changed',
    'peer_evicted',
    'lan_probe_request',
    'lan_probe_result',
    'peer_network_update',
    'transfer_command',
    'transfer_state',
    'connect',
    'disconnect',
    'join',
    'leave',
    'heartbeat',
}


def current_time_ms():
    return int(time.time() * 1000)


def normalize_client_type(client_type):
    return str(client_type or 'unknown').strip().lower()


def is_app_client_type(client_type):
    return normalize_client_type(client_type) in {'app', 'android', 'ios'}


def is_pc_client_type(client_type):
    return normalize_client_type(client_type) in {'pc', 'windows', 'macos', 'linux', 'cli', 'web'}


def is_valid_private_probe_url(probe_url, expected_private_ip=None):
    if not probe_url:
        return False
    try:
        parsed = urlparse(probe_url)
        if parsed.scheme != 'http':
            return False
        if not parsed.hostname:
            return False
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.version != 4 or not ip.is_private:
            return False
        if expected_private_ip and str(ip) != str(expected_private_ip):
            return False
        return True
    except Exception:
        return False


def emit_activity_log(activity_type, room, sender, content, client_id=None):
    normalized_type = activity_type if activity_type in ALLOWED_ACTIVITY_TYPES else 'api_relay'
    payload = {
        'type': normalized_type,
        'room': room or 'Unknown',
        'sender': sender or 'Unknown',
        'content': content or ''
    }
    if client_id:
        payload['client_id'] = client_id
    socketio.emit('activity_log', payload, room='dashboard_room')


def to_debug_json(payload):
    try:
        rendered = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        rendered = str(payload)

    if len(rendered) > SIGNAL_DEBUG_MAX_CHARS:
        return rendered[:SIGNAL_DEBUG_MAX_CHARS] + '...(truncated)'
    return rendered


def debug_signal_log(tag, payload, room=None, event=None, sender=None, sid=None):
    if not SIGNAL_DEBUG_ENABLED:
        return

    logger.info(
        "[SIGDBG] tag=%s event=%s room=%s sender=%s sid=%s payload=%s",
        tag,
        event or '-',
        room or '-',
        sender or '-',
        sid or request.sid,
        to_debug_json(payload)
    )


def parse_signal_payload(data):
    if isinstance(data, dict):
        payload = data.get('data')
        if isinstance(payload, dict):
            return payload
        return data
    return {}


def resolve_signal_context(data):
    payload = parse_signal_payload(data)
    if not isinstance(payload, dict):
        payload = {}

    if isinstance(data, dict):
        for key in ('room', 'protocol_version', 'transfer_id', 'file_id', 'sender_id', 'filename', 'method', 'reason'):
            if key in data and key not in payload:
                payload[key] = data.get(key)

    room = payload.get('room')
    if not room and isinstance(data, dict):
        room = data.get('room')

    if not room:
        sender = get_client_from_sid(request.sid)
        if sender != 'Unknown':
            room = CLIENT_ROOMS.get(sender)

    if room and 'room' not in payload:
        payload['room'] = room

    return room, payload


def is_sender_authorized_for_room(sender_client_id, room):
    if not room or not sender_client_id or sender_client_id == 'Unknown':
        return False
    return CLIENT_ROOMS.get(sender_client_id) == room and sender_client_id in CLIENT_SESSIONS


def get_serialized_sessions():
    data = {}
    room_state_cache = {}
    for client_id, sids in CLIENT_SESSIONS.items():
        room = CLIENT_ROOMS.get(client_id, 'Unknown')
        if room not in room_state_cache and room != 'Unknown':
            room_state_cache[room] = build_room_state_payload(room)

        room_state = room_state_cache.get(room, {}) if room != 'Unknown' else {}

        data[client_id] = {
            'sids': list(sids),
            'room': room,
            'type': CLIENT_TYPES.get(client_id, 'Unknown'),
            'device_name': CLIENT_DEVICE_NAMES.get(client_id) or client_id,
            'network': CLIENT_NETWORK_META.get(client_id, {}),
            'room_state': room_state.get('state', 'UNKNOWN'),
            'same_lan': room_state.get('same_lan', False),
            'lan_confidence': room_state.get('lan_confidence', 'none')
        }
    return data


def get_client_from_sid(sid):
    for client_id, sids in CLIENT_SESSIONS.items():
        if sid in sids:
            return client_id
    return "Unknown"


def get_room_client_ids(room):
    room_clients = ROOM_CLIENT_ORDER.get(room, [])
    filtered = [client_id for client_id in room_clients if CLIENT_ROOMS.get(client_id) == room and client_id in CLIENT_SESSIONS]
    if filtered != room_clients:
        ROOM_CLIENT_ORDER[room] = filtered
    return filtered


def remove_client_from_room_order(client_id, room):
    if not room:
        return
    room_clients = ROOM_CLIENT_ORDER.get(room, [])
    if client_id in room_clients:
        ROOM_CLIENT_ORDER[room] = [cid for cid in room_clients if cid != client_id]
    if not ROOM_CLIENT_ORDER.get(room):
        ROOM_CLIENT_ORDER.pop(room, None)


def build_room_state_payload(room):
    clients = get_room_client_ids(room)
    peer_summaries = []
    for client_id in clients:
        peer_summaries.append({
            'client_id': client_id,
            'client_type': CLIENT_TYPES.get(client_id, 'unknown'),
            'device_name': CLIENT_DEVICE_NAMES.get(client_id) or client_id,
            'joined_at_ms': CLIENT_JOINED_AT_MS.get(client_id, 0),
            'last_seen_ms': CLIENT_LAST_SEEN_MS.get(client_id, 0),
            'network_epoch': CLIENT_NETWORK_META.get(client_id, {}).get('network_epoch', 0)
        })

    last_probe = ROOM_LAST_PROBE.get(room)

    if len(clients) == 0:
        state = 'EMPTY'
        same_lan = False
        confidence = 'none'
    elif len(clients) == 1:
        state = 'SINGLE'
        same_lan = False
        confidence = 'none'
    else:
        probe_status = (last_probe or {}).get('status')
        if probe_status == 'ok':
            state = 'PAIR_SAME_LAN'
            same_lan = True
            confidence = 'confirmed'
        elif probe_status in {'fail', 'timeout'}:
            state = 'PAIR_DIFF_LAN'
            same_lan = False
            confidence = 'confirmed'
        else:
            state = 'PAIR_UNKNOWN'
            same_lan = False
            confidence = 'none'

    payload = {
        'protocol_version': PROTOCOL_VERSION,
        'room': room,
        'max_peers': ROOM_MAX_PEERS,
        'state': state,
        'same_lan': same_lan,
        'lan_confidence': confidence,
        'peers': peer_summaries
    }
    if last_probe:
        payload['last_probe'] = last_probe
    return payload


def get_room_lan_state(room):
    if not room:
        return 'UNKNOWN'
    return str(build_room_state_payload(room).get('state', 'UNKNOWN')).upper()


def clamp_transfer_timeout_ms(timeout_ms):
    try:
        value = int(timeout_ms)
    except Exception:
        value = TRANSFER_DECISION_TIMEOUT_MS_DEFAULT
    if value < 1000:
        value = 1000
    if value > TRANSFER_DECISION_TIMEOUT_MS_MAX:
        value = TRANSFER_DECISION_TIMEOUT_MS_MAX
    return value


def ensure_protocol_version(payload, event_name):
    version = str(payload.get('protocol_version', '')).strip()
    if version and version != PROTOCOL_VERSION:
        emit('error', {'code': 'E_BAD_VERSION', 'msg': f'{event_name} protocol_version not supported'})
        return False
    return True


def pick_receiver_client_id(room, sender_client_id):
    for client_id in get_room_client_ids(room):
        if client_id != sender_client_id:
            return client_id
    return None


def get_or_create_transfer_context(room, sender_client_id, payload):
    transfer_id = str(payload.get('transfer_id') or '').strip()
    if not transfer_id:
        transfer_id = f"tr_{current_time_ms()}_{uuid4().hex[:6]}"
        payload['transfer_id'] = transfer_id

    file_id = str(payload.get('file_id') or '').strip() or 'Unknown ID'
    receiver_client_id = pick_receiver_client_id(room, sender_client_id)
    timeout_ms = clamp_transfer_timeout_ms(payload.get('decision_timeout_ms', TRANSFER_DECISION_TIMEOUT_MS_DEFAULT))

    existing = TRANSFER_CONTEXTS.get(transfer_id)
    if existing:
        return existing

    context = {
        'transfer_id': transfer_id,
        'room': room,
        'sender_client_id': sender_client_id,
        'receiver_client_id': receiver_client_id,
        'file_id': file_id,
        'filename': payload.get('filename', ''),
        'status': 'created',
        'created_at_ms': current_time_ms(),
        'decision_timeout_ms': timeout_ms,
        'decision_deadline_ms': current_time_ms() + timeout_ms,
        'last_reason': ''
    }
    TRANSFER_CONTEXTS[transfer_id] = context
    return context


def update_transfer_state(context, status, reason=''):
    context['status'] = status
    context['last_reason'] = reason
    context['updated_at_ms'] = current_time_ms()
    emit_activity_log('transfer_state', context.get('room'), 'server', f"{context.get('transfer_id')} -> {status} ({reason})")


def emit_transfer_command(context, action, reason):
    room = context.get('room')
    sender_client_id = context.get('sender_client_id')
    transfer_id = context.get('transfer_id')
    file_id = context.get('file_id')

    command_payload = {
        'protocol_version': PROTOCOL_VERSION,
        'room': room,
        'transfer_id': transfer_id,
        'file_id': file_id,
        'action': action,
        'reason': reason,
        'issued_at_ms': current_time_ms()
    }

    for sid in list(CLIENT_SESSIONS.get(sender_client_id, set())):
        socketio.emit('transfer_command', command_payload, room=sid)

    debug_signal_log('tx', command_payload, room=room, event='transfer_command', sender='server')
    emit_activity_log('transfer_command', room, 'server', f"{transfer_id}: {action} ({reason})")


def emit_compat_file_need_relay_to_sender(context, reason):
    payload = {
        'protocol_version': PROTOCOL_VERSION,
        'room': context.get('room'),
        'file_id': context.get('file_id', 'Unknown ID'),
        'transfer_id': context.get('transfer_id', ''),
        'reason': reason,
        'reported_at_ms': current_time_ms()
    }
    for sid in list(CLIENT_SESSIONS.get(context.get('sender_client_id'), set())):
        socketio.emit('file_need_relay', payload, room=sid)
    debug_signal_log('tx', payload, room=context.get('room'), event='file_need_relay', sender='server')


def instruct_upload_relay(context, reason):
    status = context.get('status')
    if status in {'lan_success', 'completed', 'relay_uploading', 'fallback_requested', 'fallback_timeout'}:
        return
    update_transfer_state(context, 'fallback_requested' if reason != 'decision_timeout' else 'fallback_timeout', reason)
    emit_transfer_command(context, 'upload_relay', reason)
    emit_compat_file_need_relay_to_sender(context, reason)


def instruct_finish(context, reason='lan_ack'):
    status = context.get('status')
    if status in {'lan_success', 'completed'}:
        return
    update_transfer_state(context, 'lan_success', reason)
    emit_transfer_command(context, 'finish', reason)


def transfer_decision_timeout_worker(transfer_id):
    context = TRANSFER_CONTEXTS.get(transfer_id)
    if not context:
        return

    deadline_ms = context.get('decision_deadline_ms', current_time_ms())
    sleep_ms = max(0, deadline_ms - current_time_ms())
    socketio.sleep(sleep_ms / 1000.0)

    context = TRANSFER_CONTEXTS.get(transfer_id)
    if not context:
        return

    if context.get('status') in {'waiting_result', 'offered'}:
        instruct_upload_relay(context, 'decision_timeout')


# Optional hook (wired in app factory) that returns the client_ids reachable
# via a persistent FCM token for a room. Kept as an injected callable so this
# module stays decoupled from the FCM token registry (different lifecycle).
_fcm_offline_peers_provider = None


def set_fcm_offline_peers_provider(fn):
    global _fcm_offline_peers_provider
    _fcm_offline_peers_provider = fn


def _augment_with_fcm_offline_peers(room, payload):
    """Return a copy of *payload* whose peers list also includes devices that
    have a persistent FCM token but no live socket, so HTTP senders (e.g. the
    Win32 client) don't refuse to send with 'no target devices' — the server
    can still deliver to them via FCM. Only the in-memory `state`/`max_peers`
    fields stay based on real socket peers; the dashboard payload is untouched.
    """
    provider = _fcm_offline_peers_provider
    if not provider:
        return payload
    try:
        fcm_client_ids = provider(room) or []
    except Exception:
        return payload
    present = {p.get('client_id') for p in payload.get('peers', [])}
    extra = []
    for cid in fcm_client_ids:
        if cid and cid not in present:
            extra.append({
                'client_id': cid,
                'client_type': CLIENT_TYPES.get(cid, 'unknown'),
                'device_name': CLIENT_DEVICE_NAMES.get(cid) or cid,
                'joined_at_ms': 0,
                'last_seen_ms': CLIENT_LAST_SEEN_MS.get(cid, 0),
                'network_epoch': 0,
                'offline': True,
                'via_fcm': True,
            })
    if not extra:
        return payload
    augmented = dict(payload)
    augmented['peers'] = list(payload.get('peers', [])) + extra
    return augmented


def emit_room_state_changed(room, reason='state_updated'):
    if not room:
        return
    payload = build_room_state_payload(room)
    room_payload = _augment_with_fcm_offline_peers(room, payload)
    socketio.emit('room_state_changed', room_payload, room=room)
    socketio.emit('room_state_changed', payload, room='dashboard_room')
    emit_activity_log('room_state_changed', room, 'server', f"{payload.get('state', 'UNKNOWN')} ({reason})")


def broadcast_room_stats(room):
    if not room:
        return

    client_list = get_room_client_ids(room)
    count = len(client_list)

    socketio.emit('room_stats', {'count': count, 'room': room, 'clients': client_list}, room=room)
    logger.info(f"Broadcast room_stats to {room}: {count} clients ({client_list})")


def purge_client_tracking(client_id):
    room = CLIENT_ROOMS.pop(client_id, None)
    remove_client_from_room_order(client_id, room)
    CLIENT_SESSIONS.pop(client_id, None)
    CLIENT_TYPES.pop(client_id, None)
    CLIENT_DEVICE_NAMES.pop(client_id, None)
    CLIENT_JOINED_AT_MS.pop(client_id, None)
    CLIENT_LAST_SEEN_MS.pop(client_id, None)
    CLIENT_NETWORK_META.pop(client_id, None)
    CLIENT_PROBE_META.pop(client_id, None)
    return room


def evict_client_from_room(room, client_id, reason='room_capacity_exceeded'):
    payload = {
        'protocol_version': PROTOCOL_VERSION,
        'room': room,
        'evicted_client_id': client_id,
        'reason': reason,
        'evicted_at_ms': current_time_ms()
    }
    for sid in list(CLIENT_SESSIONS.get(client_id, set())):
        socketio.emit('peer_evicted', payload, room=sid)
        try:
            socketio.server.leave_room(sid, room)
        except Exception:
            logger.warning(f"Failed to force sid {sid} to leave room {room} during eviction")

    purge_client_tracking(client_id)
    emit_activity_log('peer_evicted', room, 'server', f"{client_id}: {reason}")
    logger.info(f"Evicted client {client_id} from room {room}: {reason}")


def choose_eviction_candidate(room):
    clients = get_room_client_ids(room)
    if not clients:
        return None

    non_pc_clients = [client_id for client_id in clients if not is_pc_client_type(CLIENT_TYPES.get(client_id, 'unknown'))]
    if non_pc_clients:
        return non_pc_clients[0]

    return clients[0]


def enforce_room_capacity(room):
    if not room:
        return
    while len(get_room_client_ids(room)) > ROOM_MAX_PEERS:
        eviction_candidate = choose_eviction_candidate(room)
        if not eviction_candidate:
            break
        evict_client_from_room(room, eviction_candidate, reason='room_capacity_exceeded')


def update_client_network_meta(client_id, network_data):
    if not isinstance(network_data, dict):
        return
    current = CLIENT_NETWORK_META.get(client_id, {})
    updated = {
        'private_ip': network_data.get('private_ip', current.get('private_ip')),
        'cidr': network_data.get('cidr', current.get('cidr')),
        'network_id_hash': network_data.get('network_id_hash', current.get('network_id_hash')),
        'network_epoch': int(network_data.get('network_epoch', current.get('network_epoch', 0) or 0))
    }
    CLIENT_NETWORK_META[client_id] = updated


def update_client_probe_meta(client_id, probe_data):
    if not isinstance(probe_data, dict):
        return
    current = CLIENT_PROBE_META.get(client_id, {})
    updated = {
        'probe_url': probe_data.get('probe_url', current.get('probe_url')),
        'probe_ttl_ms': int(probe_data.get('probe_ttl_ms', current.get('probe_ttl_ms', 30000) or 30000))
    }
    CLIENT_PROBE_META[client_id] = updated


def trigger_lan_probe_if_ready(room, reason='room_updated'):
    if not room:
        return

    clients = get_room_client_ids(room)
    if len(clients) != 2:
        return

    pc_client_id = None
    app_client_id = None
    for client_id in clients:
        ctype = CLIENT_TYPES.get(client_id, 'unknown')
        if is_app_client_type(ctype) and app_client_id is None:
            app_client_id = client_id
        elif is_pc_client_type(ctype) and pc_client_id is None:
            pc_client_id = client_id

    if not pc_client_id or not app_client_id:
        return

    pc_network = CLIENT_NETWORK_META.get(pc_client_id, {})
    pc_probe = CLIENT_PROBE_META.get(pc_client_id, {})
    probe_url = pc_probe.get('probe_url')

    if not is_valid_private_probe_url(probe_url, expected_private_ip=pc_network.get('private_ip')):
        ROOM_LAST_PROBE[room] = {
            'probe_id': '',
            'status': 'fail',
            'latency_ms': None,
            'checked_at_ms': current_time_ms(),
            'reason': 'invalid_probe_url'
        }
        emit_room_state_changed(room, reason='probe_url_invalid')
        return

    probe_id = f"pr_{current_time_ms()}_{uuid4().hex[:8]}"
    timeout_ms = DEFAULT_PROBE_TIMEOUT_MS

    PENDING_LAN_PROBES[probe_id] = {
        'room': room,
        'pc_client_id': pc_client_id,
        'app_client_id': app_client_id,
        'requested_at_ms': current_time_ms(),
        'timeout_ms': timeout_ms,
        'resolved': False
    }

    payload = {
        'protocol_version': PROTOCOL_VERSION,
        'room': room,
        'probe_id': probe_id,
        'provider_client_id': pc_client_id,
        'probe_url': probe_url,
        'timeout_ms': timeout_ms,
        'requested_at_ms': current_time_ms()
    }

    for sid in list(CLIENT_SESSIONS.get(app_client_id, set())):
        socketio.emit('lan_probe_request', payload, room=sid)

    emit_activity_log('lan_probe_request', room, 'server', f"{probe_id} ({reason})")
    emit_room_state_changed(room, reason='probe_requested')


def get_all_room_states():
    states = {}
    for room in sorted(ROOM_CLIENT_ORDER.keys()):
        states[room] = build_room_state_payload(room)
    return states


def detach_sid_from_tracking(sid, reason='peer_disconnected', room_hint=None):
    for client_id, sids in list(CLIENT_SESSIONS.items()):
        if sid in sids:
            sids.discard(sid)
            CLIENT_LAST_SEEN_MS[client_id] = current_time_ms()
            room = CLIENT_ROOMS.get(client_id) or room_hint

            if not sids:
                purge_client_tracking(client_id)
                if room:
                    ROOM_LAST_PROBE.pop(room, None)
                    broadcast_room_stats(room)
                    emit_room_state_changed(room, reason=reason)
                    trigger_lan_probe_if_ready(room, reason=reason)

            return client_id
    return None






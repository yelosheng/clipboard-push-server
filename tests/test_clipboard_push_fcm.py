import os
import tempfile

from app.services import fcm_registry
from app import socket_events as se


def _db():
    p = os.path.join(tempfile.mkdtemp(), 'fcm.db')
    fcm_registry.init_db(p)
    return p


def test_payload_injects_id_and_timestamp():
    data = {'room': 'r1', 'content': 'CIPHER', 'encrypted': True, 'client_id': 'A'}
    out = se.build_clipboard_fcm_payload(data)
    assert out['type'] == 'clipboard_push'
    assert out['content'] == 'CIPHER'
    assert out['encrypted'] == 'true'
    assert out['id'] and out['timestamp']
    # injected back into original data so socket broadcast reuses the same id
    assert data['id'] == out['id']
    assert data['timestamp'] == out['timestamp']


def test_payload_preserves_existing_id():
    data = {'room': 'r1', 'content': 'C', 'encrypted': False, 'id': 'fixed-id', 'timestamp': '123'}
    out = se.build_clipboard_fcm_payload(data)
    assert out['id'] == 'fixed-id'
    assert out['timestamp'] == '123'
    assert out['encrypted'] == 'false'


def test_fanout_sends_to_other_peer_only_and_prunes_invalid():
    db = _db()
    fcm_registry.register_token(db, 'r1', 'A', 'tokA', 'app')
    fcm_registry.register_token(db, 'r1', 'B', 'tokB', 'pc')
    sent = []

    def fake_send(token, payload):
        sent.append(token)
        return 'invalid_token' if token == 'tokB' else 'ok'

    data = {'room': 'r1', 'content': 'C', 'encrypted': True}
    se.fanout_clipboard_fcm(db, room='r1', sender_client_id='A', data=data, send_fn=fake_send)
    assert sent == ['tokB']                                   # only the non-sender peer
    assert fcm_registry.get_room_tokens(db, 'r1', 'A') == []  # invalid token pruned


def test_wake_fanout():
    db = _db()
    fcm_registry.register_token(db, 'r1', 'A', 'tokA', 'app')
    fcm_registry.register_token(db, 'r1', 'B', 'tokB', 'pc')
    sent = []
    se.fanout_wake_fcm(db, 'r1', 'A', send_fn=lambda t, p: sent.append((t, p)) or 'ok')
    assert sent == [('tokB', {'type': 'wake'})]


def test_fanout_noop_when_no_db():
    # Should not raise when FCM is disabled (fcm_db_path None)
    se.fanout_clipboard_fcm(None, room='r1', sender_client_id='A', data={'content': 'x'})
    se.fanout_wake_fcm(None, room='r1', sender_client_id='A')

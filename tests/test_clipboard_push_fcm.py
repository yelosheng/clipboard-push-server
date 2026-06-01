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


def test_relay_fanout_only_for_clipboard_sync():
    db = _db()
    fcm_registry.register_token(db, 'r1', 'A', 'tokA', 'app')
    fcm_registry.register_token(db, 'r1', 'B', 'tokB', 'pc')
    sent = []

    def fake_send(token, payload):
        sent.append((token, payload.get('type')))
        return 'ok'

    data = {'room': 'r1', 'content': 'C', 'encrypted': True}
    # non-clipboard events relayed over HTTP must NOT push text via FCM
    se.fanout_relay_fcm(db, 'file_sync', 'r1', 'A', data, send_fn=fake_send)
    assert sent == []
    # clipboard_sync over HTTP fans out the text to the other peer only
    se.fanout_relay_fcm(db, 'clipboard_sync', 'r1', 'A', data, send_fn=fake_send)
    assert sent == [('tokB', 'clipboard_push')]


def test_relay_fanout_noop_when_no_db_or_bad_data():
    # Disabled FCM or non-dict data must not raise
    se.fanout_relay_fcm(None, 'clipboard_sync', 'r1', 'A', {'content': 'x'})
    se.fanout_relay_fcm('ignored', 'clipboard_sync', 'r1', 'A', 'not-a-dict')


def test_get_room_client_ids():
    db = _db()
    fcm_registry.register_token(db, 'r1', 'A', 'tokA', 'app')
    fcm_registry.register_token(db, 'r1', 'B', 'tokB', 'pc')
    fcm_registry.register_token(db, 'r2', 'C', 'tokC', 'app')
    assert sorted(fcm_registry.get_room_client_ids(db, 'r1')) == ['A', 'B']
    assert fcm_registry.get_room_client_ids(db, 'r2') == ['C']
    assert fcm_registry.get_room_client_ids(db, 'rX') == []


def test_room_state_augments_offline_fcm_peers():
    from app import signal_core as sc
    payload = {'peers': [{'client_id': 'pc1'}], 'state': 'SINGLE'}
    try:
        # an FCM-registered device with no live socket shows up as an offline peer
        sc.set_fcm_offline_peers_provider(lambda room: ['phone1'] if room == 'r1' else [])
        out = sc._augment_with_fcm_offline_peers('r1', payload)
        assert [p['client_id'] for p in out['peers']] == ['pc1', 'phone1']
        phantom = out['peers'][1]
        assert phantom['offline'] is True and phantom['via_fcm'] is True
        assert out['state'] == 'SINGLE'          # real state untouched
        assert len(payload['peers']) == 1        # original payload not mutated

        # a client already present as a live peer is not duplicated
        sc.set_fcm_offline_peers_provider(lambda room: ['pc1'])
        out2 = sc._augment_with_fcm_offline_peers('r1', payload)
        assert [p['client_id'] for p in out2['peers']] == ['pc1']

        # no provider -> payload returned unchanged
        sc.set_fcm_offline_peers_provider(None)
        assert sc._augment_with_fcm_offline_peers('r1', payload) is payload
    finally:
        sc.set_fcm_offline_peers_provider(None)

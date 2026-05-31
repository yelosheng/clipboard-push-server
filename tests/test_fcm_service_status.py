import types

from app.services import fcm_service


def test_send_fcm_data_ok(monkeypatch):
    monkeypatch.setattr(fcm_service, '_ensure_initialized', lambda: True)
    fake_messaging = types.SimpleNamespace(
        Message=lambda **kw: kw,
        AndroidConfig=lambda **kw: kw,
        send=lambda msg: 'projects/x/messages/1',
    )
    monkeypatch.setattr(fcm_service, '_import_messaging', lambda: fake_messaging, raising=False)
    assert fcm_service.send_fcm_data('tokX', {'type': 'wake'}) == 'ok'


def test_send_fcm_data_disabled(monkeypatch):
    monkeypatch.setattr(fcm_service, '_ensure_initialized', lambda: False)
    assert fcm_service.send_fcm_data('tokX', {'type': 'wake'}) == 'disabled'


def test_send_fcm_data_maps_unregistered_to_invalid_token(monkeypatch):
    monkeypatch.setattr(fcm_service, '_ensure_initialized', lambda: True)

    def fake_send(msg):
        raise Exception('Requested entity was not found. (UNREGISTERED)')

    fake_messaging = types.SimpleNamespace(
        Message=lambda **kw: kw,
        AndroidConfig=lambda **kw: kw,
        send=fake_send,
    )
    monkeypatch.setattr(fcm_service, '_import_messaging', lambda: fake_messaging, raising=False)
    assert fcm_service.send_fcm_data('tokX', {'type': 'wake'}) == 'invalid_token'


def test_send_fcm_data_generic_error(monkeypatch):
    monkeypatch.setattr(fcm_service, '_ensure_initialized', lambda: True)

    def fake_send(msg):
        raise Exception('some transient network error')

    fake_messaging = types.SimpleNamespace(
        Message=lambda **kw: kw,
        AndroidConfig=lambda **kw: kw,
        send=fake_send,
    )
    monkeypatch.setattr(fcm_service, '_import_messaging', lambda: fake_messaging, raising=False)
    assert fcm_service.send_fcm_data('tokX', {'type': 'wake'}) == 'error'

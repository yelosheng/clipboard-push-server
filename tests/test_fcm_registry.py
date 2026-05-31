import os
import tempfile

from app.services import fcm_registry


def _db():
    d = tempfile.mkdtemp()
    return os.path.join(d, 'fcm.db')


def test_register_and_get_excludes_sender():
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='A', token='tokA', client_type='app')
    fcm_registry.register_token(db, room='r1', client_id='B', token='tokB', client_type='pc')
    tokens = fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='A')
    assert tokens == ['tokB']


def test_register_is_upsert():
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='A', token='old', client_type='app')
    fcm_registry.register_token(db, room='r1', client_id='A', token='new', client_type='app')
    assert fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='X') == ['new']


def test_remove_token():
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='A', token='tokA', client_type='app')
    fcm_registry.remove_token(db, token='tokA')
    assert fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='X') == []

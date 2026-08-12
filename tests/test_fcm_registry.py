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


def test_registering_new_client_id_evicts_previous_of_same_type():
    # A reinstall changes client_id (it's derived from ANDROID_ID, which
    # changes with the signing key), leaving the old row orphaned unless
    # something displaces it. Pairing is strictly 1:1, so the new phone
    # registering should kick the old one out immediately rather than
    # waiting for Firebase to eventually report its token invalid.
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='old_phone', token='tokOld', client_type='app')
    fcm_registry.register_token(db, room='r1', client_id='new_phone', token='tokNew', client_type='app')
    tokens = fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='X')
    assert tokens == ['tokNew']
    assert fcm_registry.get_room_client_ids(db, room='r1') == ['new_phone']


def test_eviction_is_scoped_to_client_type():
    # Registering a new phone must not evict the PC's registration in the
    # same room, and vice versa — the 1:1 invariant is per client_type, not
    # "one registration per room" overall.
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='phone_A', token='tokA', client_type='app')
    fcm_registry.register_token(db, room='r1', client_id='pc_B', token='tokB', client_type='pc')
    fcm_registry.register_token(db, room='r1', client_id='phone_C', token='tokC', client_type='app')
    tokens = set(fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='X'))
    assert tokens == {'tokB', 'tokC'}


def test_reregistering_same_client_id_does_not_evict_itself():
    # A plain token refresh (client_id unchanged) must behave like the
    # existing upsert, not wipe itself out via the new eviction step.
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='A', token='old', client_type='app')
    fcm_registry.register_token(db, room='r1', client_id='A', token='refreshed', client_type='app')
    assert fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='X') == ['refreshed']


def test_unknown_client_type_does_not_evict():
    # An ambiguous/missing client_type isn't grounds to blow away someone
    # else's registration — skip eviction rather than guess.
    db = _db()
    fcm_registry.init_db(db)
    fcm_registry.register_token(db, room='r1', client_id='A', token='tokA', client_type='app')
    fcm_registry.register_token(db, room='r1', client_id='B', token='tokB', client_type=None)
    tokens = set(fcm_registry.get_room_tokens(db, room='r1', exclude_client_id='X'))
    assert tokens == {'tokA', 'tokB'}

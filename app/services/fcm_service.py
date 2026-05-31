"""
FCM (Firebase Cloud Messaging) service for dual-channel push delivery.

When FIREBASE_CREDENTIALS_PATH is set, the relay server sends an FCM data
message alongside every Socket.IO broadcast so Android devices can receive
content even when the app is killed (no persistent connection required).

FCM is fully optional — if the env var is missing or firebase-admin is not
installed, all FCM calls are silently no-ops and Socket.IO remains the
only delivery channel.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

_fcm_initialized = False
_fcm_available = False
_init_lock = threading.Lock()


def _ensure_initialized():
    global _fcm_initialized, _fcm_available
    if _fcm_initialized:
        return _fcm_available
    with _init_lock:
        if _fcm_initialized:
            return _fcm_available
        creds_path = os.environ.get('FIREBASE_CREDENTIALS_PATH', '').strip()
        if not creds_path:
            logger.info('FCM disabled: FIREBASE_CREDENTIALS_PATH not set')
            _fcm_initialized = True
            return False
        try:
            import firebase_admin
            from firebase_admin import credentials
            cred = credentials.Certificate(creds_path)
            firebase_admin.initialize_app(cred)
            _fcm_available = True
            logger.info('Firebase Admin SDK initialized (FCM enabled)')
        except ImportError:
            logger.warning('FCM disabled: firebase-admin not installed')
        except Exception as e:
            logger.error(f'Firebase Admin init failed: {e}')
        _fcm_initialized = True
        return _fcm_available


# Substrings that indicate the device token is permanently invalid and
# should be removed from the registry (no point retrying it).
_INVALID_TOKEN_MARKERS = (
    'UNREGISTERED',
    'SenderIdMismatch',
    'registration-token-not-registered',
    'Requested entity was not found',
)


def _import_messaging():
    from firebase_admin import messaging
    return messaging


def send_fcm_data(token: str, data: dict) -> str:
    """Send an FCM data message to a single device token.

    All values in *data* are coerced to strings as required by FCM.
    Returns one of: 'ok' | 'invalid_token' | 'error' | 'disabled'.
    """
    if not _ensure_initialized():
        return 'disabled'
    try:
        messaging = _import_messaging()
        str_data = {k: str(v) for k, v in data.items() if v is not None}
        message = messaging.Message(
            data=str_data,
            token=token,
            android=messaging.AndroidConfig(priority='high'),
        )
        messaging.send(message)
        return 'ok'
    except Exception as e:
        text = str(e)
        if any(m in text for m in _INVALID_TOKEN_MARKERS):
            logger.info(f'FCM token invalid (…{token[-6:]}): will be removed')
            return 'invalid_token'
        logger.warning(f'FCM send failed (token=…{token[-6:]}): {e}')
        return 'error'


def send_fcm_to_tokens(tokens: list, data: dict) -> int:
    """Send FCM to a list of device tokens.

    Returns the number of successful deliveries.
    """
    if not tokens or not _ensure_initialized():
        return 0
    success = 0
    for token in tokens:
        if send_fcm_data(token, data) == 'ok':
            success += 1
    logger.debug(f'FCM sent {success}/{len(tokens)} OK')
    return success

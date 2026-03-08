# app/services/geo_service.py
import ipaddress
import threading
import requests

_cache: dict = {}
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

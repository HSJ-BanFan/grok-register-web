import ipaddress
from urllib.parse import urlsplit


def is_loopback_host(host):
    value = str(host or '').strip().lower()
    if value == 'localhost':
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def origin_matches_host(origin, host):
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in ('http', 'https') and parsed.netloc.lower() == str(host or '').lower()

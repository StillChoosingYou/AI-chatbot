"""School-only access for the student assistant.

A student may use the assistant only from the Padre Garcia Polytechnic College campus. Two proofs are accepted:
  - the request comes from a school network (CAMPUS_IPS: addresses or CIDR ranges such as 203.0.113.0/24), or
  - the phone or laptop reports a position inside the campus circle (the browser asks the student first).
The position is checked and thrown away: it is never written to the database or to a log. A passing check is
remembered in the signed session cookie for CAMPUS_PASS_HOURS, so the student is not asked again at every page.

It is a deterrent, not a lock: a person who fakes the GPS of their phone can pass. The coordinator and the
administrator are not affected. GEO_FENCE=1 turns it on; it is off by default so that testing and the defense
can happen anywhere.
"""
import ipaddress
import math
import os

# Padre Garcia Polytechnic College (Google Maps pin).
DEFAULT_LAT = 13.8828094
DEFAULT_LNG = 121.2665155
DEFAULT_RADIUS_M = 200.0
DEFAULT_MAX_ACCURACY_M = 300.0       # a fix less precise than this (a laptop guessing from Wi-Fi) proves nothing
DEFAULT_PASS_HOURS = 3.0
EARTH_RADIUS_M = 6371008.8


def _number(name, default, low, high):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and low <= value <= high else default


def parse_networks(text):
    """'1.2.3.4, 10.0.0.0/8' -> list of networks. Entries that are not an address or range are skipped."""
    networks = []
    for part in str(text or '').replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return networks


def distance_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres (haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((phi2 - phi1) / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class CampusAccess:
    def __init__(self):
        self.enabled = os.environ.get('GEO_FENCE', '0').strip() == '1'
        self.lat = _number('CAMPUS_LAT', DEFAULT_LAT, -90, 90)
        self.lng = _number('CAMPUS_LNG', DEFAULT_LNG, -180, 180)
        self.radius_m = _number('CAMPUS_RADIUS_M', DEFAULT_RADIUS_M, 20, 5000)
        self.max_accuracy_m = _number('CAMPUS_MAX_ACCURACY_M', DEFAULT_MAX_ACCURACY_M, 10, 5000)
        self.pass_seconds = _number('CAMPUS_PASS_HOURS', DEFAULT_PASS_HOURS, 0.1, 24) * 3600
        self.networks = parse_networks(os.environ.get('CAMPUS_IPS', ''))

    def on_campus_network(self, address):
        try:
            ip = ipaddress.ip_address(str(address).strip())
        except ValueError:
            return False
        return any(ip in network for network in self.networks if network.version == ip.version)

    def position_ok(self, lat, lng, accuracy):
        """True when a reported position is inside the campus circle and precise enough to trust.

        Anything that is not a plain finite number (a string, NaN, a boolean) is refused. The position may be off
        by its accuracy, so a fix counts only when its whole error circle could still be on campus."""
        values = []
        for item in (lat, lng, accuracy):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                return False
            values.append(float(item))
        lat, lng, accuracy = values
        if not (-90 <= lat <= 90 and -180 <= lng <= 180) or accuracy < 0 or accuracy > self.max_accuracy_m:
            return False
        return distance_m(lat, lng, self.lat, self.lng) <= self.radius_m

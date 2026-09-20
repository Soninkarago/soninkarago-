from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import re
import time
import hmac
import hashlib
import base64
import binascii
import secrets
import mimetypes
import threading
import math
import uuid
import socket
from http.cookies import SimpleCookie
from collections import defaultdict, deque
import psycopg
from psycopg.rows import dict_row
import phonenumbers


ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "index.html")
PORT = int(os.environ.get("PORT", "10000"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PAYTECH_API_KEY = os.environ.get("PAYTECH_API_KEY", "")
PAYTECH_API_SECRET = os.environ.get("PAYTECH_API_SECRET", "")
PAYTECH_ENV = os.environ.get("PAYTECH_ENV", "prod").lower()
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "https://soninkarago-mzp6.onrender.com"
).rstrip("/")

APP_VERSION = "2026.09.20-v36-service-category-isolation"
MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
DAKAR_BASE_FARE = int(os.environ.get("DAKAR_BASE_FARE", "500"))
DAKAR_PRICE_PER_KM = int(os.environ.get("DAKAR_PRICE_PER_KM", "150"))
DAKAR_PRICE_PER_MINUTE = int(os.environ.get("DAKAR_PRICE_PER_MINUTE", "20"))
DAKAR_MIN_FARE = int(os.environ.get("DAKAR_MIN_FARE", "700"))
RATE_LIMITS = defaultdict(deque)
RATE_LIMIT_LOCK = threading.Lock()
SESSION_COOKIE_NAME = "skg_session"
SESSION_TTL_SECONDS = 12 * 60 * 60

AUDIT_RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))
SECURITY_LOG_RETENTION_DAYS = int(os.environ.get("SECURITY_LOG_RETENTION_DAYS", "365"))
BACKUP_RPO_HOURS = int(os.environ.get("BACKUP_RPO_HOURS", "24"))
BACKUP_RTO_HOURS = int(os.environ.get("BACKUP_RTO_HOURS", "4"))
INSTANCE_ID = os.environ.get("RENDER_INSTANCE_ID", "") or socket.gethostname()


def allow_request(key, limit, window_seconds):
    """Small in-memory abuse guard for login and public write endpoints."""
    now = time.time()
    cutoff = now - window_seconds

    with RATE_LIMIT_LOCK:
        attempts = RATE_LIMITS[key]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()

        if len(attempts) >= limit:
            return False

        attempts.append(now)

        # Prevent unbounded memory use from forged client identifiers.
        if len(RATE_LIMITS) > 10000:
            stale = [
                item_key for item_key, values in RATE_LIMITS.items()
                if not values or values[-1] < now - 86400
            ]
            for item_key in stale[:2000]:
                RATE_LIMITS.pop(item_key, None)

    return True


def allow_request_shared(key, limit, window_seconds):
    """Shared rate limit persisted in PostgreSQL; falls back to memory only if DB is unavailable."""
    now = int(time.time())
    bucket = now // max(1, int(window_seconds))
    try:
        with db() as conn:
            row = conn.execute(
                """
                INSERT INTO request_rate_limits(rate_key, bucket, request_count, updated_at)
                VALUES(%s, %s, 1, %s)
                ON CONFLICT(rate_key, bucket) DO UPDATE
                SET request_count=request_rate_limits.request_count + 1,
                    updated_at=EXCLUDED.updated_at
                RETURNING request_count
                """,
                (key, bucket, now)
            ).fetchone()
            # Opportunistic cleanup: keep roughly two days of buckets.
            if secrets.randbelow(100) == 0:
                conn.execute(
                    "DELETE FROM request_rate_limits WHERE updated_at < %s",
                    (now - 172800,)
                )
        return int(row["request_count"]) <= int(limit)
    except Exception:
        return allow_request(key, limit, window_seconds)




def safe_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"unserializable": True})


def audit_event(conn, actor_role, actor_id, action, entity_type="", entity_id="", details=None, outcome="success"):
    """Append-only audit trail for sensitive business/security actions."""
    try:
        conn.execute(
            """
            INSERT INTO audit_events(
                id, actor_role, actor_id, action, entity_type, entity_id,
                details_json, outcome, created_at, instance_id
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                uuid.uuid4().hex,
                str(actor_role or "")[:50],
                str(actor_id or "")[:120],
                str(action or "")[:120],
                str(entity_type or "")[:80],
                str(entity_id or "")[:160],
                safe_json(details or {}),
                str(outcome or "success")[:30],
                int(time.time()),
                INSTANCE_ID[:120],
            )
        )
    except Exception as exc:
        # Audit logging must never expose sensitive details in HTTP responses.
        print("audit_event error:", repr(exc))


def payment_event_once(conn, provider, event_key, reference, event_type, amount, payload_summary):
    """Idempotency register for payment callbacks. Returns True only on first processing."""
    try:
        row = conn.execute(
            """
            INSERT INTO payment_events(
                id, provider, event_key, reference, event_type, amount,
                payload_summary, created_at
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(provider,event_key) DO NOTHING
            RETURNING id
            """,
            (
                uuid.uuid4().hex,
                provider[:40],
                event_key[:160],
                reference[:160],
                event_type[:80],
                int(amount or 0),
                safe_json(payload_summary or {}),
                int(time.time()),
            )
        ).fetchone()
        return bool(row)
    except Exception as exc:
        print("payment_event_once error:", repr(exc))
        # Existing state checks still protect against duplicate balance changes.
        return True


def purge_old_operational_logs(conn):
    now = int(time.time())
    audit_cutoff = now - max(30, AUDIT_RETENTION_DAYS) * 86400
    security_cutoff = now - max(30, SECURITY_LOG_RETENTION_DAYS) * 86400
    conn.execute("DELETE FROM audit_events WHERE created_at < %s", (audit_cutoff,))
    conn.execute("DELETE FROM payment_events WHERE created_at < %s", (security_cutoff,))


URBAN_CAR_ZONES = {
    "dakar": {
        "label": "Dakar et proche banlieue",
        "center": (14.7167, -17.4677),
        "radius_km": 27,
        "example": "Plateau, Parcelles Assainies, Pikine, Guédiawaye ou Keur Massar",
    },
    "rufisque": {
        "label": "Rufisque et périphérie",
        "center": (14.7167, -17.2667),
        "radius_km": 18,
        "example": "Rufisque, Bargny ou un quartier de la périphérie",
    },
    "thies": {
        "label": "Thiès et périphérie",
        "center": (14.7910, -16.9359),
        "radius_km": 24,
        "example": "Thiès ou un quartier de la périphérie",
    },
    "mbour": {
        "label": "Mbour, Saly et périphérie",
        "center": (14.4200, -16.9638),
        "radius_km": 28,
        "example": "Mbour, Saly ou un quartier de la Petite-Côte",
    },
    "saint_louis": {
        "label": "Saint-Louis et périphérie",
        "center": (16.0326, -16.4818),
        "radius_km": 32,
        "example": "Sor, île de Saint-Louis ou un quartier de la périphérie",
    },
    "ziguinchor": {
        "label": "Ziguinchor et périphérie",
        "center": (12.5833, -16.2667),
        "radius_km": 32,
        "example": "Néma, Lyndiane, Castor ou un quartier de la périphérie",
    },
    "touba": {
        "label": "Touba, Mbacké et périphérie",
        "center": (14.8667, -15.8833),
        "radius_km": 35,
        "example": "Touba, Mbacké ou un quartier de la périphérie",
    },
    "kaolack": {
        "label": "Kaolack et périphérie",
        "center": (14.1514, -16.0726),
        "radius_km": 32,
        "example": "Kaolack, Kahone ou un quartier de la périphérie",
    },
    "louga": {
        "label": "Louga et périphérie",
        "center": (15.6187, -16.2244),
        "radius_km": 28,
        "example": "Louga ou un quartier de la périphérie",
    },
    "matam": {
        "label": "Matam, Ourossogui et périphérie",
        "center": (15.6559, -13.2554),
        "radius_km": 38,
        "example": "Matam, Ourossogui ou un quartier de la périphérie",
    },
    "tambacounda": {
        "label": "Tambacounda et périphérie",
        "center": (13.7707, -13.6673),
        "radius_km": 30,
        "example": "Tambacounda ou un quartier de la périphérie",
    },
    "kolda": {
        "label": "Kolda et périphérie",
        "center": (12.8939, -14.9413),
        "radius_km": 28,
        "example": "Kolda ou un quartier de la périphérie",
    },
    "diourbel": {
        "label": "Diourbel et périphérie",
        "center": (14.6561, -16.2346),
        "radius_km": 24,
        "example": "Diourbel ou un quartier de la périphérie",
    },
    "fatick": {
        "label": "Fatick et périphérie",
        "center": (14.3390, -16.4111),
        "radius_km": 22,
        "example": "Fatick ou un quartier de la périphérie",
    },
    "richard_toll": {
        "label": "Richard-Toll et périphérie",
        "center": (16.4625, -15.7008),
        "radius_km": 22,
        "example": "Richard-Toll ou un quartier de la périphérie",
    },
    "kaffrine": {
        "label": "Kaffrine et périphérie",
        "center": (14.1059, -15.5508),
        "radius_km": 22,
        "example": "Kaffrine ou un quartier de la périphérie",
    },
    "kedougou": {
        "label": "Kédougou et périphérie",
        "center": (12.5556, -12.1808),
        "radius_km": 24,
        "example": "Kédougou ou un quartier de la périphérie",
    },
    "sedhiou": {
        "label": "Sédhiou et périphérie",
        "center": (12.7081, -15.5569),
        "radius_km": 22,
        "example": "Sédhiou ou un quartier de la périphérie",
    },
    "tivaouane": {
        "label": "Tivaouane et périphérie",
        "center": (14.9500, -16.8167),
        "radius_km": 20,
        "example": "Tivaouane ou un quartier de la périphérie",
    },
}


def in_urban_service_zone(zone_code, lat, lng):
    zone = URBAN_CAR_ZONES.get(str(zone_code or "").strip())
    if not zone:
        return False
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    center_lat, center_lng = zone["center"]
    return distance_km(center_lat, center_lng, lat, lng) <= float(zone["radius_km"])


def detect_urban_zone(lat, lng):
    """Retourne la zone desservie la plus logique à partir du point de départ."""
    candidates = []
    for code, zone in URBAN_CAR_ZONES.items():
        d = distance_km(zone["center"][0], zone["center"][1], lat, lng)
        if d <= float(zone["radius_km"]):
            # Choisir la zone dont le centre est le plus proche évite les conflits
            # Dakar/Rufisque ou Touba/Mbacké sans demander à l'utilisateur.
            candidates.append((d, code))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def urban_zone_bounds(zone_code):
    zone = URBAN_CAR_ZONES.get(zone_code)
    if not zone:
        return None
    lat, lng = zone["center"]
    delta_lat = float(zone["radius_km"]) / 111.0
    delta_lng = float(zone["radius_km"]) / max(
        35.0, 111.0 * math.cos(math.radians(lat))
    )
    return f"{lat-delta_lat},{lng-delta_lng}|{lat+delta_lat},{lng+delta_lng}"


def geocode_senegal(query, bias_zone=None):
    """Géocodage Sénégal, éventuellement biaisé vers la zone de départ."""
    from urllib.parse import urlencode
    address = str(query or "").strip()[:180]
    if len(address) < 3:
        raise ValueError("Indiquez une rue, un quartier, un commerce ou un lieu précis.")

    params = {
        "address": f"{address}, Sénégal",
        "components": "country:SN",
        "key": MAPS_API_KEY,
        "language": "fr",
        "region": "sn",
    }
    if bias_zone:
        bounds = urban_zone_bounds(bias_zone)
        if bounds:
            params["bounds"] = bounds

    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urlencode(params)
    try:
        with urlopen(url, timeout=10) as response:
            result = json.load(response)
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Recherche d'adresse momentanément indisponible.") from exc

    if result.get("status") != "OK" or not result.get("results"):
        raise ValueError("Lieu introuvable. Vérifiez le nom de la rue, du quartier ou du lieu.")

    # Si on a un biais de ville, privilégier un résultat dans cette zone.
    if bias_zone:
        for place in result["results"]:
            coords = place.get("geometry", {}).get("location", {})
            lat, lng = coords.get("lat"), coords.get("lng")
            if in_urban_service_zone(bias_zone, lat, lng):
                return {
                    "address": place["formatted_address"][:200],
                    "lat": lat,
                    "lng": lng,
                }

    place = result["results"][0]
    coords = place.get("geometry", {}).get("location", {})
    return {
        "address": place["formatted_address"][:200],
        "lat": coords.get("lat"),
        "lng": coords.get("lng"),
    }



def reverse_geocode_senegal(lat, lng):
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        raise ValueError("Coordonnées GPS invalides.")
    if not (12.0 <= lat <= 17.5 and -18.5 <= lng <= -11.0):
        raise ValueError("Cette position ne semble pas être au Sénégal.")
    key = google_maps_api_key()
    if not key:
        raise RuntimeError("Service de localisation temporairement indisponible.")
    params = {
        "latlng": f"{lat:.7f},{lng:.7f}",
        "language": "fr",
        "region": "sn",
        "key": key,
    }
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urlencode(params)
    data = http_json(url, timeout=8)
    if data.get("status") != "OK" or not data.get("results"):
        raise ValueError("Impossible d’identifier précisément votre position.")
    result = data["results"][0]
    address = str(result.get("formatted_address") or "").strip()[:200]
    city = ""
    for component in result.get("address_components") or []:
        types = set(component.get("types") or [])
        if types.intersection({"locality", "postal_town", "administrative_area_level_2", "administrative_area_level_1"}):
            city = str(component.get("long_name") or "").strip()
            if city:
                break
    zone = detect_urban_zone(lat, lng)
    return {
        "address": address,
        "city": city,
        "lat": lat,
        "lng": lng,
        "zone": zone or "",
        "zone_label": (URBAN_CAR_ZONES.get(zone, {}).get("label") if zone else "") or "",
    }

def urban_quote(zone_code, pickup, destination):
    if not MAPS_API_KEY or not AUTH_SECRET:
        raise RuntimeError("Calcul du trajet momentanément indisponible.")

    # Mode Uber-like : la ville est détectée à partir du départ.
    if str(zone_code or "").strip() in URBAN_CAR_ZONES:
        requested_zone = str(zone_code).strip()
        origin = geocode_senegal(pickup, requested_zone)
        detected_zone = detect_urban_zone(origin["lat"], origin["lng"])
        if detected_zone != requested_zone:
            raise ValueError(
                f"Le départ ne semble pas se trouver dans {URBAN_CAR_ZONES[requested_zone]['label']}."
            )
    else:
        origin = geocode_senegal(pickup)
        detected_zone = detect_urban_zone(origin["lat"], origin["lng"])
        if not detected_zone:
            raise ValueError(
                "SoninkaraGo n'a pas encore activé la voiture à la demande à ce point de départ."
            )

    zone = URBAN_CAR_ZONES[detected_zone]
    # La destination est biaisée vers la ville de départ pour les noms ambigus,
    # mais peut être dans une autre zone SoninkaraGo.
    arrival = geocode_senegal(destination, detected_zone)
    destination_zone = detect_urban_zone(arrival["lat"], arrival["lng"])
    if not destination_zone:
        raise ValueError(
            "La destination n'est pas encore dans une zone voiture SoninkaraGo."
        )

    payload = json.dumps({
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin["lat"],
                    "longitude": origin["lng"]
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": arrival["lat"],
                    "longitude": arrival["lng"]
                }
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 30)
        ),
    }).encode()

    req = Request(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        payload,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": MAPS_API_KEY,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=12) as response:
            route = json.load(response)["routes"][0]
    except (URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(
            "Impossible de calculer le trajet avec la circulation actuelle."
        ) from exc

    km = int(route["distanceMeters"]) / 1000
    minutes = math.ceil(float(route["duration"].rstrip("s")) / 60)
    if km < .4 or km > 500 or minutes < 1:
        raise ValueError("Vérifiez le départ et la destination.")

    fare = max(
        DAKAR_MIN_FARE,
        DAKAR_BASE_FARE
        + km * DAKAR_PRICE_PER_KM
        + minutes * DAKAR_PRICE_PER_MINUTE,
    )
    fare = int(math.ceil(fare / 100) * 100)

    quote = {
        "zone": detected_zone,
        "zone_label": zone["label"],
        "destination_zone": destination_zone,
        "destination_zone_label": URBAN_CAR_ZONES[destination_zone]["label"],
        "pickup": origin["address"],
        "destination": arrival["address"],
        "lat": origin["lat"],
        "lng": origin["lng"],
        "distance_km": round(km, 1),
        "duration_min": minutes,
        "fare": fare,
        "exp": int(time.time()) + 300,
    }
    body = b64(json.dumps(quote, separators=(",", ":")).encode())
    signature = hmac.new(
        AUTH_SECRET.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return quote, body + "." + signature


# Compatibilité avec les anciennes courses / liens Dakar.
def in_dakar_thies_service_zone(lat, lng):
    return in_urban_service_zone("dakar", lat, lng)


def dakar_address(query):
    return geocode_senegal(query, "dakar")


def dakar_quote(pickup, destination):
    return urban_quote("dakar", pickup, destination)


def verify_dakar_quote(token):
    try:
        body, signature = token.split(".")
        expected = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        quote = json.loads(b64decode(body))
        quote.setdefault("zone", "dakar")
        if quote.get("zone") not in URBAN_CAR_ZONES:
            raise ValueError()
        if quote["exp"] < time.time():
            raise ValueError()
        return quote
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        raise ValueError("Le devis a expiré. Recalculez le prix avant de commander.")




LOCAL_SERVICE_CONFIG = {
    "local_moto": {
        "service": "Moto-taxi",
        "label": "Moto-taxi — villages et petites localités",
        "max_km": 35,
    },
    "local_tricycle": {
        "service": "3 roues",
        "label": "3 roues — villages et petites localités",
        "max_km": 8,
    },
    "local_taxi": {
        "service": "Voiture taxi",
        "label": "Taxi local — villages et petites localités",
        "max_km": 25,
    },
}


def local_fare(service_code, km):
    """Grille SoninkaraGo pour villages et petites localités.

    Le prix progresse par paliers pour éviter les sauts incohérents et conserver
    un minimum viable pour le chauffeur. Cette grille est commerciale/interne :
    elle ne doit pas être présentée comme un tarif officiel ou homologué.
    """
    km = max(0.0, float(km))

    if service_code == "local_moto":
        # Référence historique SoninkaraGo : trajet interne au village à petit prix,
        # liaison entre localités proches à 2 000 F, puis 3 000 F pour une liaison plus longue.
        # Exemple de référence : Moudéry → Diawara (~6–7 km) = 2 000 F.
        if km <= 2:
            return 200
        if km <= 15:
            return 2000
        return 3000  # jusqu'à 35 km (limite du service)

    if service_code == "local_tricycle":
        # 3 roues : service de proximité, limité à 8 km.
        if km <= 2:
            return 500
        if km <= 4:
            return 800
        if km <= 6:
            return 1200
        return 1500

    if service_code == "local_taxi":
        # Taxi local : couvre les trajets courts et les liaisons entre localités proches.
        if km <= 2:
            return 1000
        if km <= 5:
            return 1500
        if km <= 10:
            return 2500
        if km <= 15:
            return 3500
        return 5000  # jusqu'à 25 km (limite du service)

    raise ValueError("Service local invalide.")


def local_quote(service_code, pickup, destination):
    if not MAPS_API_KEY or not AUTH_SECRET:
        raise RuntimeError("Calcul du trajet momentanément indisponible.")
    config = LOCAL_SERVICE_CONFIG.get(str(service_code or "").strip())
    if not config:
        raise ValueError("Choisissez un service local valide.")

    origin = geocode_senegal(pickup)
    arrival = geocode_senegal(destination)
    for point in (origin, arrival):
        lat, lng = point.get("lat"), point.get("lng")
        if lat is None or lng is None or not (12.0 <= float(lat) <= 17.5 and -18.5 <= float(lng) <= -11.0):
            raise ValueError("Le départ et la destination doivent se trouver au Sénégal.")

    travel_mode = "TWO_WHEELER" if service_code == "local_moto" else "DRIVE"
    payload = {
        "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
        "destination": {"location": {"latLng": {"latitude": arrival["lat"], "longitude": arrival["lng"]}}},
        "travelMode": travel_mode,
        "departureTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 30)),
    }
    if travel_mode == "DRIVE":
        payload["routingPreference"] = "TRAFFIC_AWARE"
    req = Request(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": MAPS_API_KEY,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=12) as response:
            route = json.load(response)["routes"][0]
    except (URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        raise RuntimeError("Impossible de calculer ce trajet pour le moment.") from exc

    km = int(route["distanceMeters"]) / 1000
    minutes = math.ceil(float(str(route["duration"]).rstrip("s")) / 60)
    if km < .1:
        raise ValueError("Vérifiez le départ et la destination.")
    if km > float(config["max_km"]):
        raise ValueError(
            "Ce trajet dépasse la distance prévue pour ce service local. Choisissez un autre service SoninkaraGo."
        )

    pickup_place = reverse_geocode_senegal(origin["lat"], origin["lng"])
    fare = local_fare(service_code, km)
    quote = {
        "service_code": service_code,
        "service": config["service"],
        "service_label": config["label"],
        "zone": "local",
        "zone_label": pickup_place.get("city") or "Village ou petite localité",
        "pickup": origin["address"],
        "destination": arrival["address"],
        "lat": origin["lat"],
        "lng": origin["lng"],
        "distance_km": round(km, 1),
        "duration_min": minutes,
        "fare": fare,
        "exp": int(time.time()) + 300,
    }
    body = b64(json.dumps(quote, separators=(",", ":")).encode())
    signature = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return quote, body + "." + signature


def verify_local_quote(token, expected_service):
    quote = verify_dakar_quote(token)
    if quote.get("service_code") != expected_service:
        raise ValueError("Le devis ne correspond pas au service local choisi.")
    return quote

def compute_live_eta(driver_lat, driver_lng, client_lat, client_lng, vehicle="Voiture taxi"):
    """ETA routier basé sur la position GPS réelle du chauffeur et le trafic Google actuel.

    Aucun temps inventé : si Google Routes ne répond pas, on renvoie None.
    """
    if not MAPS_API_KEY:
        return None
    coords = [driver_lat, driver_lng, client_lat, client_lng]
    try:
        dlat, dlng, clat, clng = [float(v) for v in coords]
    except (TypeError, ValueError):
        return None

    travel_mode = "TWO_WHEELER" if vehicle == "Moto-taxi" else "DRIVE"
    payload = {
        "origin": {"location": {"latLng": {"latitude": dlat, "longitude": dlng}}},
        "destination": {"location": {"latLng": {"latitude": clat, "longitude": clng}}},
        "travelMode": travel_mode,
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 15))
    }
    req = Request(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": MAPS_API_KEY,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration"
        },
        method="POST"
    )
    try:
        with urlopen(req, timeout=8) as response:
            result = json.load(response)
        route = result["routes"][0]
        seconds = max(0, int(round(float(str(route["duration"]).rstrip("s")))))
        meters = max(0, int(route.get("distanceMeters", 0)))
        if seconds <= 0:
            return None
        return {"eta_seconds": seconds, "eta_distance_meters": meters}
    except Exception as exc:
        print(f"ETA Google Routes indisponible: {exc}", flush=True)
        return None

def request_paytech_payment(ride_id, route, amount, payment, client_name):
    if not PAYTECH_API_KEY or not PAYTECH_API_SECRET:
        raise RuntimeError("Paiement PayTech non configuré")

    target_payment = {
        "Wave": "Wave",
        "Orange Money": "Orange Money"
    }.get(payment)

    if not target_payment:
        raise ValueError("Mode de paiement non pris en charge")

    payload = json.dumps({
        "item_name": f"Course {route['pickup']} - {route['destination']}",
        "item_price": amount,
        "currency": "XOF",
        "ref_command": ride_id,
        "command_name": f"Réservation SoninkaraGo {ride_id}",
        "env": PAYTECH_ENV if PAYTECH_ENV in ("test", "prod") else "prod",
        "target_payment": target_payment,
        "ipn_url": f"{PUBLIC_BASE_URL}/api/paytech/ipn",
        "success_url": f"{PUBLIC_BASE_URL}/paiement/succes",
        "cancel_url": f"{PUBLIC_BASE_URL}/paiement/annule",
        "custom_field": json.dumps({
            "ride_id": ride_id,
            "client_name": client_name
        }, ensure_ascii=False)
    }, ensure_ascii=False).encode()

    request = Request(
        "https://paytech.sn/api/payment/request-payment",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "API_KEY": PAYTECH_API_KEY,
            "API_SECRET": PAYTECH_API_SECRET
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError(detail or "PayTech a refusé le paiement") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("PayTech est temporairement indisponible") from exc

    payment_url = result.get("redirect_url") or result.get("redirectUrl")
    if result.get("success") not in (1, True) or not payment_url:
        raise RuntimeError(result.get("message") or "Impossible de créer le paiement")

    return payment_url


def request_paytech_recharge(recharge_id, amount, payment, driver_id):
    if not PAYTECH_API_KEY or not PAYTECH_API_SECRET:
        raise RuntimeError("Paiement PayTech non configuré")

    target_payment = {
        "wave": "Wave",
        "orange_money": "Orange Money"
    }.get(payment)

    if not target_payment:
        raise ValueError("Mode de paiement non pris en charge")

    payload = json.dumps({
        "item_name": "Recharge compte chauffeur SoninkaraGo",
        "item_price": amount,
        "currency": "XOF",
        "ref_command": recharge_id,
        "command_name": f"Recharge chauffeur {recharge_id}",
        "env": PAYTECH_ENV if PAYTECH_ENV in ("test", "prod") else "prod",
        "target_payment": target_payment,
        "ipn_url": f"{PUBLIC_BASE_URL}/api/paytech/ipn",
        "success_url": f"{PUBLIC_BASE_URL}/paiement/succes",
        "cancel_url": f"{PUBLIC_BASE_URL}/paiement/annule",
        "custom_field": json.dumps({
            "recharge_id": recharge_id,
            "driver_id": driver_id
        })
    }).encode()

    request = Request(
        "https://paytech.sn/api/payment/request-payment",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "API_KEY": PAYTECH_API_KEY,
            "API_SECRET": PAYTECH_API_SECRET
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError(detail or "PayTech a refusé la recharge") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("PayTech est temporairement indisponible") from exc

    payment_url = result.get("redirect_url") or result.get("redirectUrl")
    if result.get("success") not in (1, True) or not payment_url:
        raise RuntimeError(result.get("message") or "Impossible de créer la recharge")

    return payment_url


ROUTES = {
    # MINICAR 14 PLACES
    "minicar_dakar_touba": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Touba",
        "fare": 70000
    },
    "minicar_touba_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Touba",
        "destination": "Dakar",
        "fare": 70000
    },
    "minicar_dakar_matam": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Matam",
        "fare": 180000
    },
    "minicar_matam_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Matam",
        "destination": "Dakar",
        "fare": 180000
    },
    "minicar_dakar_bakel": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Bakel",
        "fare": 190000
    },
    "minicar_bakel_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Bakel",
        "destination": "Dakar",
        "fare": 190000
    },
    "minicar_dakar_khadebere": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Khadé Béré",
        "fare": 190000
    },
    "minicar_khadebere_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Khadé Béré",
        "destination": "Dakar",
        "fare": 190000
    },
    "minicar_moudery_ourossogui": {
        "service": "Minicar 14 places",
        "pickup": "Moudéry",
        "destination": "Ourossogui",
        "fare": 70000
    },
    "minicar_ourossogui_moudery": {
        "service": "Minicar 14 places",
        "pickup": "Ourossogui",
        "destination": "Moudéry",
        "fare": 70000
    },
    "minicar_dakar_ourossogui": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Ourossogui",
        "fare": 180000
    },
    "minicar_ourossogui_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Ourossogui",
        "destination": "Dakar",
        "fare": 180000
    },
    "minicar_dakar_waounde": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Waoundé",
        "fare": 200000
    },
    "minicar_waounde_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Waoundé",
        "destination": "Dakar",
        "fare": 200000
    },
    "minicar_dakar_diawara": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Diawara",
        "fare": 190000
    },
    "minicar_diawara_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Diawara",
        "destination": "Dakar",
        "fare": 190000
    },
    "minicar_dakar_moudery": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Moudéry",
        "fare": 190000
    },
    "minicar_moudery_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Moudéry",
        "destination": "Dakar",
        "fare": 190000
    },
    "minicar_dakar_tambacounda": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Tambacounda",
        "fare": 160000
    },
    "minicar_tambacounda_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Tambacounda",
        "destination": "Dakar",
        "fare": 160000
    },
        "minicar_dakar_louga": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Louga",
        "fare": 60000
    },
    "minicar_louga_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Louga",
        "destination": "Dakar",
        "fare": 60000
    },

    "minicar_dakar_kaolack": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Kaolack",
        "fare": 60000
    },
    "minicar_kaolack_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Kaolack",
        "destination": "Dakar",
        "fare": 60000
    },

    "minicar_dakar_saint_louis": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Saint-Louis",
        "fare": 70000
    },
    "minicar_saint_louis_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Saint-Louis",
        "destination": "Dakar",
        "fare": 70000
    },

    "minicar_dakar_ziguinchor": {
        "service": "Minicar 14 places",
        "pickup": "Dakar",
        "destination": "Ziguinchor",
        "fare": 150000
    },
    "minicar_ziguinchor_dakar": {
        "service": "Minicar 14 places",
        "pickup": "Ziguinchor",
        "destination": "Dakar",
        "fare": 150000
    }
}


ALLOWED_VILLAGES = None  # Toutes les localités du Sénégal sont acceptées.

ALLOWED_VEHICLES = [
    "Moto-taxi",
    "3 roues",
    "Voiture taxi",
    "Minicar 14 places"
]


def db():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def init():
    with db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rides(
                id TEXT PRIMARY KEY,
                client_name TEXT,
                phone TEXT,
                pickup TEXT,
                destination TEXT,
                vehicle TEXT,
                payment TEXT,
                fare INTEGER,
                fee INTEGER,
                status TEXT,
                driver_name TEXT,
                created_at BIGINT
            )
        """) 
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS driver_id TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS client_lat DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS client_lng DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS driver_lat DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS driver_lng DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS client_location_at BIGINT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS driver_location_at BIGINT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS eta_seconds INTEGER
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS eta_distance_meters INTEGER
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS eta_calculated_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS eta_driver_location_at BIGINT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS tracking_token TEXT
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS route_code TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS departure_date TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS departure_time TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS meeting_point TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS passenger_count INTEGER DEFAULT 1
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS luggage TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS booking_note TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS payment_status TEXT
            NOT NULL DEFAULT 'unpaid'
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS deposit_amount INTEGER
            NOT NULL DEFAULT 0
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS balance_due INTEGER
            NOT NULL DEFAULT 0
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS commission_charged BOOLEAN
            NOT NULL DEFAULT FALSE
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS offered_driver_id TEXT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS offer_expires_at BIGINT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS offer_attempts TEXT
            NOT NULL DEFAULT ''
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS deposit_paid_at BIGINT
        """)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS balance_paid_at BIGINT
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS drivers(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                village TEXT NOT NULL,
                vehicle TEXT NOT NULL,
                pin_hash TEXT NOT NULL,
                pin_salt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at BIGINT NOT NULL
            )
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS online BOOLEAN NOT NULL DEFAULT FALSE
        """)

        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS last_location_at BIGINT
        """)
        conn.execute("""
    ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0
""")
        # Conformité Sénégal — consentements et dossier chauffeur
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS terms_accepted_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS privacy_accepted_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS location_consent_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS compliance_version TEXT
        """)

        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS legal_documents_declared BOOLEAN NOT NULL DEFAULT FALSE
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS terms_accepted_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS privacy_accepted_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS compliance_version TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS driving_licence_number TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS driving_licence_expiry TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS insurance_policy_number TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS insurance_expiry TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS vehicle_plate TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS registration_card_number TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS technical_inspection_expiry TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS transport_authorisation_reference TEXT
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS compliance_verified BOOLEAN NOT NULL DEFAULT FALSE
        """)
        conn.execute("""
            ALTER TABLE drivers
            ADD COLUMN IF NOT EXISTS compliance_verified_at BIGINT
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS driver_recharges(
                id TEXT PRIMARY KEY,
                driver_id TEXT NOT NULL REFERENCES drivers(id),
                amount INTEGER NOT NULL,
                payment TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at BIGINT NOT NULL,
                paid_at BIGINT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions(
                endpoint TEXT PRIMARY KEY,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                user_agent TEXT NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                revoked BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS request_rate_limits(
                rate_key TEXT NOT NULL,
                bucket BIGINT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                updated_at BIGINT NOT NULL,
                PRIMARY KEY(rate_key, bucket)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_rate_limits_updated
            ON request_rate_limits(updated_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events(
                id TEXT PRIMARY KEY,
                actor_role TEXT NOT NULL,
                actor_id TEXT,
                action TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                outcome TEXT NOT NULL DEFAULT 'success',
                created_at BIGINT NOT NULL,
                instance_id TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_events_created
            ON audit_events(created_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_events_entity
            ON audit_events(entity_type, entity_id, created_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_events(
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                event_key TEXT NOT NULL,
                reference TEXT,
                event_type TEXT,
                amount INTEGER NOT NULL DEFAULT 0,
                payload_summary TEXT NOT NULL DEFAULT '{}',
                created_at BIGINT NOT NULL,
                UNIQUE(provider,event_key)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_payment_events_reference
            ON payment_events(reference, created_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS driver_compliance_checks(
                id TEXT PRIMARY KEY,
                driver_id TEXT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                reviewer_role TEXT NOT NULL,
                checklist_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                decision TEXT NOT NULL,
                created_at BIGINT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_driver_compliance_checks_driver
            ON driver_compliance_checks(driver_id, created_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operational_incidents(
                id TEXT PRIMARY KEY,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                opened_at BIGINT NOT NULL,
                closed_at BIGINT,
                resolution TEXT NOT NULL DEFAULT ''
            )
        """)
        purge_old_operational_logs(conn)

        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS cancelled_at BIGINT
        """)
        conn.execute("""
            ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS cancel_reason TEXT
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS support_requests(
                id TEXT PRIMARY KEY,
                ride_id TEXT,
                phone TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at BIGINT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_support_requests_ride
            ON support_requests(ride_id, created_at DESC)
        """)


def hash_pin(pin, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode(),
        salt.encode(),
        310000
    ).hex()

    return digest, salt


def verify_pin(pin, stored_hash, salt):
    digest, _ = hash_pin(pin, salt)

    return hmac.compare_digest(
        digest,
        stored_hash
    )


def b64(data):
    return base64.urlsafe_b64encode(
        data
    ).decode().rstrip("=")


def b64decode(data):
    data += "=" * (-len(data) % 4)

    return base64.urlsafe_b64decode(
        data.encode()
    )


def make_token(role, name="", driver_id=""):
    payload = {
        "role": role,
        "name": name,
        "driver_id": driver_id,
        "exp": int(time.time()) + (12 * 60 * 60)
    }

    encoded = b64(
        json.dumps(
            payload,
            separators=(",", ":")
        ).encode()
    )

    signature = hmac.new(
        AUTH_SECRET.encode(),
        encoded.encode(),
        hashlib.sha256
    ).hexdigest()

    return encoded + "." + signature


def read_token_value(token):
    if not token:
        return None
    try:
        encoded, signature = str(token).split(".", 1)
        expected = hmac.new(
            AUTH_SECRET.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(b64decode(encoded).decode())
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def read_token(header):
    if not header or not header.startswith("Bearer "):
        return None
    return read_token_value(header[7:].strip())


def session_cookie(token=None, clear=False):
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = "" if clear else str(token or "")
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/api"
    morsel["secure"] = True
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    morsel["max-age"] = 0 if clear else SESSION_TTL_SECONDS
    return morsel.OutputString()


def valid_coords(lat, lng):
    try:
        lat = float(lat)
        lng = float(lng)

        if not (-90 <= lat <= 90):
            return None

        if not (-180 <= lng <= 180):
            return None

        return lat, lng

    except (TypeError, ValueError):
        return None


def in_dakar_zone(lat, lng):
    """Compatibilité historique : zone voiture Dakar → AIBD → Thiès."""
    return in_dakar_thies_service_zone(lat, lng)


def normalize_phone(value, region="SN"):
    """Convertit un numéro national ou international en E.164."""
    raw = str(value or "").strip()
    region = str(region or "SN").upper()
    if len(raw) > 50 or region not in phonenumbers.SUPPORTED_REGIONS:
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if parsed.extension or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def distance_km(lat1, lng1, lat2, lng2):
    """Distance à vol d'oiseau entre deux positions GPS."""
    radius = 6371.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lng2) - float(lng1))
    value = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def assign_next_driver(conn, ride_id, now=None):
    """Réserve une course pendant 15 secondes au chauffeur éligible le plus proche."""
    now = int(now or time.time())
    ride = conn.execute(
        """
        SELECT id, vehicle, fee, status, client_lat, client_lng, route_code,
               offered_driver_id, offer_expires_at, offer_attempts
        FROM rides
        WHERE id=%s
        FOR UPDATE
        """,
        (ride_id,)
    ).fetchone()

    if not ride or ride["status"] != "searching":
        return None

    # Les minicars planifiés conservent le fonctionnement de réservation.
    if ride["vehicle"] == "Minicar 14 places":
        return None

    if ride["client_lat"] is None or ride["client_lng"] is None:
        return None

    if (
        ride.get("offered_driver_id")
        and int(ride.get("offer_expires_at") or 0) > now
    ):
        return ride["offered_driver_id"]

    attempted = {
        item for item in str(ride.get("offer_attempts") or "").split(",")
        if item
    }

    drivers = conn.execute(
        """
        SELECT d.id, d.latitude, d.longitude
        FROM drivers d
        WHERE d.status='approved'
          AND d.online=TRUE
          AND d.vehicle=%s
          AND d.latitude IS NOT NULL
          AND d.longitude IS NOT NULL
          AND COALESCE(d.last_location_at, 0) >= %s
          AND d.balance >= %s
          AND NOT EXISTS (
              SELECT 1 FROM rides active
              WHERE active.driver_id=d.id
                AND active.status='accepted'
          )
        """,
        (ride["vehicle"], now - 300, int(ride["fee"] or 0))
    ).fetchall()

    urban_zone_code = None
    local_route = str(ride.get("route_code") or "") in LOCAL_SERVICE_CONFIG
    if ride["route_code"] == "dakar_car":
        urban_zone_code = "dakar"
    elif str(ride["route_code"] or "").startswith("urban_car_"):
        urban_zone_code = str(ride["route_code"]).removeprefix("urban_car_")

    eligible = [
        driver for driver in drivers
        if driver["id"] not in attempted
        and (
            (not urban_zone_code and not local_route)
            or (local_route and distance_km(
                ride["client_lat"], ride["client_lng"],
                driver["latitude"], driver["longitude"]
            ) <= 20)
            or (
                in_urban_service_zone(
                    urban_zone_code, driver["latitude"], driver["longitude"]
                )
                and distance_km(
                    ride["client_lat"], ride["client_lng"],
                    driver["latitude"], driver["longitude"]
                ) <= 20
            )
        )
    ]
    if not eligible:
        conn.execute(
            """
            UPDATE rides
            SET offered_driver_id=NULL, offer_expires_at=NULL
            WHERE id=%s AND status='searching'
            """,
            (ride_id,)
        )
        return None

    nearest = min(
        eligible,
        key=lambda driver: distance_km(
            ride["client_lat"], ride["client_lng"],
            driver["latitude"], driver["longitude"]
        )
    )
    attempted.add(nearest["id"])
    conn.execute(
        """
        UPDATE rides
        SET offered_driver_id=%s,
            offer_expires_at=%s,
            offer_attempts=%s
        WHERE id=%s AND status='searching'
        """,
        (nearest["id"], now + 15, ",".join(sorted(attempted)), ride_id)
    )
    return nearest["id"]


def dispatch_pending_rides(conn):
    """Fait avancer les offres expirées vers le chauffeur suivant."""
    now = int(time.time())
    rides = conn.execute(
        """
        SELECT id
        FROM rides
        WHERE status='searching'
          AND vehicle<>'Minicar 14 places'
          AND client_lat IS NOT NULL
          AND client_lng IS NOT NULL
          AND (offered_driver_id IS NULL OR COALESCE(offer_expires_at, 0) <= %s)
        ORDER BY created_at ASC
        LIMIT 100
        """,
        (now,)
    ).fetchall()
    for ride in rides:
        assign_next_driver(conn, ride["id"], now)

class App(SimpleHTTPRequestHandler):
    server_version = "SoninkaraGo"
    sys_version = ""

    def version_string(self):
        return self.server_version

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(self), payment=(self), usb=(), serial=(), bluetooth=()"
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "img-src 'self' data: https://*.tile.openstreetmap.org; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; "
            "worker-src 'self'; manifest-src 'self'; media-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests"
        )
        self.send_header(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains"
        )

    def client_ip(self):
        # Render transmet l'adresse d'origine via X-Forwarded-For.
        # On borne et nettoie la valeur avant de l'utiliser comme clé anti-abus.
        candidate = (
            self.headers.get("CF-Connecting-IP")
            or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or self.client_address[0]
        )
        candidate = str(candidate or "unknown").strip()[:64]
        return candidate if candidate else "unknown"

    def check_rate(self, action, limit, window_seconds, identity=""):
        key = f"{action}:{self.client_ip()}:{identity[:80]}"
        if allow_request_shared(key, limit, window_seconds):
            return True
        self.sendj(
            {"error": "Trop de tentatives. Réessayez plus tard."},
            429
        )
        return False

    def same_origin_request(self):
        """Reject browser cross-origin writes while allowing trusted server callbacks without Origin."""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        allowed = {PUBLIC_BASE_URL, "https://soninkarago.sn", "https://www.soninkarago.sn"}
        if origin.rstrip("/") in {item.rstrip("/") for item in allowed if item}:
            return True
        self.sendj({"error": "Origine de requête non autorisée."}, 403)
        return False

    def serve_static(self, filename, cache_seconds=3600):
        safe_name = os.path.basename(filename)
        file_path = os.path.join(ROOT, safe_name)
        if not os.path.isfile(file_path):
            return self.sendj({"error": "Introuvable"}, 404)

        content_type, _ = mimetypes.guess_type(file_path)
        with open(file_path, "rb") as stream:
            data = stream.read()

        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        self.security_headers()
        self.end_headers()
        self.wfile.write(data)

    def sendj(self, obj, status=200, extra_headers=None):
        body = json.dumps(
            obj,
            ensure_ascii=False
        ).encode()

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.security_headers()

        for header_name, header_value in (extra_headers or []):
            self.send_header(header_name, header_value)

        self.end_headers()

        self.wfile.write(body)


    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                self.sendj({"error": "Requête trop volumineuse."}, 413)
                return None
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="strict")
            content_type = (self.headers.get("Content-Type", "") or "").lower()
            if "application/x-www-form-urlencoded" in content_type:
                return {key: values[-1] if values else "" for key, values in parse_qs(raw, keep_blank_values=True, max_num_fields=80).items()}
            if "application/json" not in content_type:
                self.sendj({"error": "Type de contenu non accepté."}, 415)
                return None
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                self.sendj({"error": "Format de requête invalide."}, 400)
                return None
            return parsed
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.sendj({"error": "Requête invalide."}, 400)
            return None
        except Exception:
            self.sendj({"error": "Requête invalide."}, 400)
            return None


    def send_html(self, title, message, status=200):
        body = (
            "<!doctype html><html lang='fr'><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title} - SoninkaraGo</title>"
            "<style>body{margin:0;background:#f5f5f2;font-family:Arial,sans-serif;"
            "display:grid;place-items:center;min-height:100vh;color:#121412}"
            ".card{background:#fff;max-width:520px;margin:20px;padding:32px;"
            "border-radius:20px;border:1px solid #e2e3df;box-shadow:0 14px 36px #00000012;text-align:center}"
            "h1{margin-top:0}a{display:inline-block;margin-top:18px;padding:12px 20px;"
            "border-radius:10px;background:#0b7a55;color:#fff;text-decoration:none}</style>"
            f"</head><body><main class='card'><h1>{title}</h1><p>{message}</p>"
            "<a href='/'>Retour à SoninkaraGo</a></main></body></html>"
        ).encode()

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)


    def auth(self):
        user = read_token(self.headers.get("Authorization"))
        if user:
            return user
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            morsel = cookies.get(SESSION_COOKIE_NAME)
            return read_token_value(morsel.value if morsel else "")
        except Exception:
            return None


    def serve_index(self):
        try:
            with open(INDEX, "rb") as f:
                body = f.read()

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header("Cache-Control", "no-cache")
            self.security_headers()

            self.end_headers()

            self.wfile.write(body)

        except Exception:
            self.send_error(500)


    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/push/public-key":
            return self.sendj({"public_key": VAPID_PUBLIC_KEY, "configured": bool(VAPID_PUBLIC_KEY)})
        if path == "/api/session":
            user = self.auth()
            if not user:
                return self.sendj({"authenticated": False})
            return self.sendj({
                "authenticated": True,
                "role": user.get("role", ""),
                "name": user.get("name", "")
            })
        if path in ("/", "/index.html"):
            return self.serve_index()
        if path == "/paiement/succes":
            return self.send_html(
                "Paiement réussi",
                "Votre paiement a bien été reçu. La mise à jour sera effectuée automatiquement."
            )
        if path == "/paiement/annule":
            return self.send_html(
                "Paiement annulé",
                "Le paiement n'a pas été effectué. Vous pouvez revenir à l'accueil et réessayer."
            )
        static_pages = {
            "/confidentialite": "confidentialite.html",
            "/conditions": "conditions.html",
            "/mentions-legales": "mentions-legales.html",
            "/suppression-compte": "suppression-compte.html",
            "/manifest.webmanifest": "manifest.webmanifest",
            "/service-worker.js": "service-worker.js",
        }
        if path in static_pages:
            cache_seconds = 0 if path in ("/confidentialite", "/conditions", "/mentions-legales", "/suppression-compte") else 3600
            return self.serve_static(static_pages[path], cache_seconds)
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")):
            return self.serve_static(path.lstrip("/"), 86400)
        if path == "/api/location/reverse":
            try:
                params = parse_qs(parsed.query)
                lat = (params.get("lat") or [""])[0]
                lng = (params.get("lng") or [""])[0]
                self.json_response(200, reverse_geocode_senegal(lat, lng))
            except ValueError as exc:
                self.json_response(400, {"error": str(exc)})
            except Exception as exc:
                print("reverse geocode error:", repr(exc))
                self.json_response(503, {"error": "Service de localisation temporairement indisponible."})
            return

        if path in ("/api/health", "/api/ready"):
            checks = {
                "database": "unknown",
                "paytech_configured": bool(PAYTECH_API_KEY and PAYTECH_API_SECRET),
                "maps_configured": bool(MAPS_API_KEY),
                "auth_secret_configured": bool(AUTH_SECRET),
            }
            try:
                started = time.time()
                with db() as conn:
                    conn.execute("SELECT 1").fetchone()
                checks["database"] = "ok"
                checks["database_latency_ms"] = int((time.time() - started) * 1000)
            except psycopg.Error:
                checks["database"] = "unavailable"
                return self.sendj({
                    "ok": False,
                    "service": "SoninkaraGo",
                    "version": APP_VERSION,
                    "checks": checks
                }, 503)
            return self.sendj({
                "ok": True,
                "service": "SoninkaraGo",
                "version": APP_VERSION,
                "instance": INSTANCE_ID,
                "checks": checks,
                "recovery_objectives": {
                    "rpo_hours": BACKUP_RPO_HOURS,
                    "rto_hours": BACKUP_RTO_HOURS
                }
            })

        # Client : suivi de sa course
        if (
            path.startswith("/api/rides/")
            and path.count("/") == 3
        ):
            ride_id = path.split("/")[3]

            try:
                with db() as conn:
                    row = conn.execute(
                        """
                        SELECT r.*, d.phone AS driver_phone
                        FROM rides r
                        LEFT JOIN drivers d ON d.id = r.driver_id
                        WHERE r.id=%s
                        """,
                        (ride_id,)
                    ).fetchone()
            except psycopg.Error as exc:
                print(
                    f"Erreur PostgreSQL pendant le suivi de {ride_id}: {exc}",
                    flush=True
                )
                return self.sendj(
                    {"error": "Suivi temporairement indisponible"},
                    503
                )

            if not row:
                return self.sendj(
                    {"error": "Course introuvable"},
                    404
                )

            query = parse_qs(urlparse(self.path).query)
            token = str(query.get("token", [""])[-1])
            if not token or not hmac.compare_digest(
                str(row.get("tracking_token") or ""),
                token
            ):
                return self.sendj({"error": "Non autorisé"}, 401)

            # ETA réel : calculé à partir de la dernière position GPS du chauffeur
            # et de la circulation routière actuelle. On ne fabrique jamais une
            # estimation locale si Google Routes n'est pas disponible.
            eta_seconds = None
            eta_distance_meters = None
            eta_calculated_at = None
            eta_source = "unavailable"
            now = int(time.time())
            driver_loc_at = int(row.get("driver_location_at") or 0)
            driver_gps_fresh = driver_loc_at > 0 and (now - driver_loc_at) <= 60
            eta_status = row.get("status") in ("accepted", "arriving")
            have_coords = all(row.get(k) is not None for k in (
                "driver_lat", "driver_lng", "client_lat", "client_lng"
            ))

            if eta_status and driver_gps_fresh and have_coords:
                cached_at = int(row.get("eta_calculated_at") or 0)
                cached_driver_loc_at = int(row.get("eta_driver_location_at") or 0)
                cache_valid = (
                    row.get("eta_seconds") is not None
                    and cached_at > 0
                    and (now - cached_at) < 15
                    and cached_driver_loc_at == driver_loc_at
                )
                if cache_valid:
                    elapsed = max(0, now - cached_at)
                    eta_seconds = max(0, int(row.get("eta_seconds") or 0) - elapsed)
                    eta_distance_meters = int(row.get("eta_distance_meters") or 0)
                    eta_calculated_at = cached_at
                    eta_source = "google_routes"
                else:
                    eta = compute_live_eta(
                        row.get("driver_lat"), row.get("driver_lng"),
                        row.get("client_lat"), row.get("client_lng"),
                        row.get("vehicle", "Voiture taxi")
                    )
                    if eta:
                        eta_seconds = eta["eta_seconds"]
                        eta_distance_meters = eta["eta_distance_meters"]
                        eta_calculated_at = now
                        eta_source = "google_routes"
                        try:
                            with db() as conn:
                                conn.execute(
                                    """
                                    UPDATE rides
                                    SET eta_seconds=%s, eta_distance_meters=%s,
                                        eta_calculated_at=%s, eta_driver_location_at=%s
                                    WHERE id=%s
                                    """,
                                    (eta_seconds, eta_distance_meters, now, driver_loc_at, ride_id)
                                )
                        except psycopg.Error as exc:
                            print(f"Cache ETA non enregistré pour {ride_id}: {exc}", flush=True)

            return self.sendj({
                "id": row.get("id", ride_id),
                "pickup": row.get("pickup", ""),
                "destination": row.get("destination", ""),
                "vehicle": row.get("vehicle", ""),
                "payment": row.get("payment", ""),
                "fare": row.get("fare", 0),
                "status": row.get("status", "searching"),
                "driver_name": row.get("driver_name", ""),
                "driver_phone": (
                    row.get("driver_phone", "")
                    if row.get("status") in ("accepted", "arriving", "in_progress")
                    else ""
                ),
                "driver_location_at": row.get("driver_location_at"),
                "eta_seconds": eta_seconds,
                "eta_distance_meters": eta_distance_meters,
                "eta_calculated_at": eta_calculated_at,
                "eta_source": eta_source,
                "eta_gps_fresh": bool(driver_gps_fresh),
                "payment_status": row.get("payment_status", "unpaid"),
                "can_cancel": (
                    row.get("status") in ("searching", "offered", "payment_failed", "payment_canceled")
                    and row.get("payment_status", "unpaid") in ("unpaid", "failed")
                )
            })


        # Liste des courses
        if path == "/api/rides":
            user = self.auth()

            if (
                not user
                or user.get("role")
                not in ("driver", "admin")
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            with db() as conn:

                if user["role"] == "admin":
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM rides
                        ORDER BY created_at DESC
                        """
                    ).fetchall()

                else:
                    dispatch_pending_rides(conn)
                    now = int(time.time())
                    rows = conn.execute(
                        """
                        SELECT
                            id,
                            client_name,
                            pickup,
                            destination,
                            vehicle,
                            payment,
                            fare,
                            fee,
                            status,
                            driver_name,
                            created_at,
                            offer_expires_at
                        FROM rides
                        WHERE
                            (
                                status='searching'
                                AND (
                                    (
                                        vehicle='Minicar 14 places'
                                        AND vehicle=(
                                            SELECT vehicle FROM drivers WHERE id=%s
                                        )
                                    )
                                    OR (
                                        offered_driver_id=%s
                                        AND COALESCE(offer_expires_at, 0) > %s
                                    )
                                )
                            )
                            OR (
                                status='accepted'
                                AND driver_id=%s
                            )
                        ORDER BY created_at DESC
                        """,
                        (
                            user.get("driver_id"),
                            user.get("driver_id"),
                            now,
                            user.get("driver_id"),
                        )
                    ).fetchall()

            return self.sendj(rows)
        # SOLDE CHAUFFEUR
        if path == "/api/driver/me":

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            with db() as conn:
                driver = conn.execute(
                    """
                    SELECT balance
                    FROM drivers
                    WHERE id=%s
                    """,
                    (user.get("driver_id"),)
                ).fetchone()

            if not driver:
                return self.sendj(
                    {"error": "Chauffeur introuvable"},
                    404
                )

            return self.sendj({
                "balance": int(driver["balance"] or 0)
            })

        # Statistiques Admin
        if path == "/api/stats":
            user = self.auth()

            if (
                not user
                or user.get("role") != "admin"
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            with db() as conn:
                row = conn.execute("""
                    SELECT
                        COUNT(*) AS n,
                        COALESCE(SUM(fare),0) AS volume,
                        COALESCE(SUM(fee) FILTER (WHERE commission_charged=TRUE),0) AS fees
                    FROM rides
                """).fetchone()

            return self.sendj(row)


        # Liste des chauffeurs pour Admin
        if path == "/api/admin/audit":
            user = self.auth()
            if not user or user.get("role") != "admin":
                return self.sendj({"error": "Non autorisé"}, 401)
            try:
                params = parse_qs(parsed.query)
                limit = min(200, max(1, int((params.get("limit") or ["100"])[0])))
            except Exception:
                limit = 100
            with db() as conn:
                rows = conn.execute(
                    """
                    SELECT actor_role,actor_id,action,entity_type,entity_id,
                           details_json,outcome,created_at
                    FROM audit_events
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,)
                ).fetchall()
            return self.sendj(rows)


        if path == "/api/admin/drivers":
            user = self.auth()

            if (
                not user
                or user.get("role") != "admin"
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            with db() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        phone,
                        village,
                        vehicle,
                        status,
                        created_at,
                        driving_licence_number,
                        driving_licence_expiry,
                        insurance_policy_number,
                        insurance_expiry,
                        vehicle_plate,
                        registration_card_number,
                        technical_inspection_expiry,
                        transport_authorisation_reference,
                        compliance_verified,
                        compliance_verified_at
                    FROM drivers
                    ORDER BY created_at DESC
                    """
                ).fetchall()

            return self.sendj(rows)
                    # SUIVI GPS CÔTÉ CLIENT
        if (
            path.startswith("/api/rides/")
            and path.endswith("/tracking")
        ):

            ride_id = path.split("/")[3]

            query = urlparse(self.path).query
            params = {}

            for item in query.split("&"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    params[k] = v

            token = params.get("token", "")

            try:
                with db() as conn:
                    ride = conn.execute(
                        """
                        SELECT r.*, d.phone AS driver_phone
                        FROM rides r
                        LEFT JOIN drivers d ON d.id = r.driver_id
                        WHERE r.id=%s
                        """,
                        (ride_id,)
                    ).fetchone()
            except psycopg.Error as exc:
                print(
                    f"Erreur PostgreSQL pendant le suivi GPS de {ride_id}: {exc}",
                    flush=True
                )
                return self.sendj(
                    {"error": "Suivi GPS temporairement indisponible"},
                    503
                )

            if not ride:
                return self.sendj(
                    {"error": "Course introuvable"},
                    404
                )

            if not hmac.compare_digest(
                str(ride.get("tracking_token") or ""),
                token
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            return self.sendj({
                "status": ride.get("status", "searching"),
                "driver_name": ride.get("driver_name", ""),
                "driver_phone": (
                    ride.get("driver_phone", "")
                    if ride.get("status") in ("accepted", "arriving", "in_progress")
                    else ""
                ),
                "driver_lat": ride.get("driver_lat"),
                "driver_lng": ride.get("driver_lng"),
                "updated_at": ride.get("driver_location_at")
            })
                    # POSITION CLIENT VISIBLE PAR SON CHAUFFEUR
        if (
            path.startswith("/api/rides/")
            and path.endswith("/client-location")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            ride_id = path.split("/")[3]

            with db() as conn:
                ride = conn.execute(
                    """
                    SELECT
                        status,
                        driver_id,
                        client_lat,
                        client_lng,
                        client_location_at
                    FROM rides
                    WHERE id=%s
                    """,
                    (ride_id,)
                ).fetchone()

            if not ride:
                return self.sendj(
                    {"error": "Course introuvable"},
                    404
                )

            if ride["driver_id"] != user.get("driver_id"):
                return self.sendj(
                    {"error": "Non autorisé"},
                    403
                )

            return self.sendj({
                "status": ride["status"],
                "client_lat": ride["client_lat"],
                "client_lng": ride["client_lng"],
                "updated_at": ride["client_location_at"]
            })


        return self.sendj(
            {"error": "Introuvable"},
            404
        )


    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()
        if data is None:
            return
        if path != "/api/paytech/ipn" and not self.same_origin_request():
            return
        if path == "/api/push/subscribe":
            if not self.check_rate("push-subscribe", 10, 3600):
                return
            subscription = data.get("subscription") or {}
            endpoint = str(subscription.get("endpoint") or "").strip()
            keys = subscription.get("keys") or {}
            p256dh = str(keys.get("p256dh") or "").strip()
            auth = str(keys.get("auth") or "").strip()
            if not endpoint.startswith("https://") or len(endpoint) > 2000 or not p256dh or not auth:
                return self.sendj({"error": "Abonnement push invalide."}, 400)
            now = int(time.time())
            ua = str(self.headers.get("User-Agent", ""))[:300]
            with db() as conn:
                conn.execute("""
                    INSERT INTO push_subscriptions(endpoint,p256dh,auth,user_agent,created_at,updated_at,revoked)
                    VALUES(%s,%s,%s,%s,%s,%s,FALSE)
                    ON CONFLICT(endpoint) DO UPDATE SET
                      p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, user_agent=EXCLUDED.user_agent,
                      updated_at=EXCLUDED.updated_at, revoked=FALSE
                """, (endpoint,p256dh,auth,ua,now,now))
            return self.sendj({"ok": True})
        if path in ("/api/urban/quote", "/api/dakar/quote"):
            if not self.check_rate("urban-quote", 30, 3600):
                return
            try:
                zone_code = (
                    "dakar" if path == "/api/dakar/quote"
                    else str(data.get("zone") or "").strip()
                )
                quote, token = urban_quote(
                    zone_code or None,
                    data.get("pickup"),
                    data.get("destination")
                )
                zone_code = quote["zone"]
                with db() as conn:
                    available = conn.execute("""
                        SELECT latitude, longitude FROM drivers
                        WHERE status='approved' AND online=TRUE AND vehicle='Voiture taxi'
                          AND latitude IS NOT NULL AND longitude IS NOT NULL
                          AND last_location_at >= %s
                          AND balance >= %s
                          AND NOT EXISTS (
                              SELECT 1 FROM rides r
                              WHERE r.driver_id=drivers.id AND r.status='accepted'
                          )
                    """, (int(time.time()) - 300, (quote["fare"] + 9) // 10)).fetchall()
                available = sum(
                    in_urban_service_zone(
                        zone_code, d["latitude"], d["longitude"]
                    )
                    and distance_km(
                        quote["lat"], quote["lng"],
                        d["latitude"], d["longitude"]
                    ) <= 20
                    for d in available
                )
                return self.sendj({
                    **quote,
                    "quote_token": token,
                    "drivers_online": available,
                })
            except ValueError as exc:
                return self.sendj({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self.sendj({"error": str(exc)}, 503)
        if path == "/api/local/quote":
            if not self.check_rate("local-quote", 30, 3600):
                return
            try:
                service_code = str(data.get("service") or "").strip()
                quote, token = local_quote(
                    service_code,
                    data.get("pickup"),
                    data.get("destination")
                )
                vehicle = LOCAL_SERVICE_CONFIG[service_code]["service"]
                with db() as conn:
                    available = conn.execute("""
                        SELECT latitude, longitude FROM drivers
                        WHERE status='approved' AND online=TRUE AND vehicle=%s
                          AND latitude IS NOT NULL AND longitude IS NOT NULL
                          AND last_location_at >= %s
                          AND balance >= %s
                          AND NOT EXISTS (
                              SELECT 1 FROM rides r
                              WHERE r.driver_id=drivers.id AND r.status='accepted'
                          )
                    """, (vehicle, int(time.time()) - 300, (quote["fare"] + 9) // 10)).fetchall()
                available = sum(
                    distance_km(
                        quote["lat"], quote["lng"],
                        d["latitude"], d["longitude"]
                    ) <= 20
                    for d in available
                )
                return self.sendj({**quote, "quote_token": token, "drivers_online": available})
            except ValueError as exc:
                return self.sendj({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self.sendj({"error": str(exc)}, 503)

        if path == "/api/paytech/ipn":
            if not PAYTECH_API_KEY or not PAYTECH_API_SECRET:
                return self.sendj({"error": "PayTech non configuré"}, 503)

            ref_command = str(data.get("ref_command", "")).strip()[:120]
            item_price = str(data.get("item_price", "")).strip()
            final_item_price = str(data.get("final_item_price", "")).strip()
            effective_price = final_item_price or item_price
            received_hmac = str(data.get("hmac_compute", "")).strip().lower()
            message = f"{effective_price}|{ref_command}|{PAYTECH_API_KEY}"
            expected_hmac = hmac.new(
                PAYTECH_API_SECRET.encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()

            if not received_hmac or not hmac.compare_digest(received_hmac, expected_hmac):
                return self.sendj({"error": "Signature IPN invalide"}, 403)

            callback_currency = str(data.get("currency", "XOF")).upper().strip()
            callback_env = str(data.get("env", PAYTECH_ENV)).lower().strip()
            if callback_currency and callback_currency != "XOF":
                return self.sendj({"error": "Devise IPN invalide"}, 409)
            if callback_env and callback_env != PAYTECH_ENV:
                return self.sendj({"error": "Environnement IPN invalide"}, 409)

            event = str(data.get("type_event", "")).strip()

            with db() as conn:
                if ref_command.startswith("RECH-"):
                    recharge = conn.execute(
                        """
                        SELECT id, driver_id, amount, status
                        FROM driver_recharges
                        WHERE id=%s
                        FOR UPDATE
                        """,
                        (ref_command,)
                    ).fetchone()

                    if not recharge:
                        return self.sendj({"error": "Recharge introuvable"}, 404)

                    try:
                        paid_amount = int(float(effective_price))
                    except (TypeError, ValueError):
                        return self.sendj({"error": "Montant invalide"}, 400)

                    if paid_amount != int(recharge["amount"]):
                        return self.sendj({"error": "Montant incorrect"}, 409)

                    if event == "sale_complete":
                        if recharge["status"] == "pending":
                            conn.execute(
                                """
                                UPDATE driver_recharges
                                SET status='paid', paid_at=%s
                                WHERE id=%s AND status='pending'
                                """,
                                (int(time.time()), ref_command)
                            )
                            conn.execute(
                                """
                                UPDATE drivers
                                SET balance=balance+%s
                                WHERE id=%s
                                """,
                                (paid_amount, recharge["driver_id"])
                            )
                    elif event == "sale_canceled":
                        conn.execute(
                            """
                            UPDATE driver_recharges
                            SET status='canceled'
                            WHERE id=%s AND status='pending'
                            """,
                            (ref_command,)
                        )
                    else:
                        return self.sendj({"error": "Événement IPN inconnu"}, 400)

                    return self.sendj({"ok": True})

                ride = conn.execute(
                    "SELECT id, fare, deposit_amount, status FROM rides WHERE id=%s FOR UPDATE",
                    (ref_command,)
                ).fetchone()

                if not ride:
                    return self.sendj({"error": "Réservation introuvable"}, 404)

                try:
                    paid_amount = int(float(effective_price))
                except (TypeError, ValueError):
                    return self.sendj({"error": "Montant invalide"}, 400)

                expected_amount = int(ride["deposit_amount"] or ride["fare"])
                if paid_amount != expected_amount:
                    return self.sendj({"error": "Montant incorrect"}, 409)

                if event == "sale_complete":
                    if ride.get("status") in ("cancelled", "canceled"):
                        return self.sendj({
                            "error": "Réservation annulée : paiement à vérifier manuellement."
                        }, 409)
                    conn.execute(
                        """
                        UPDATE rides
                        SET payment_status=%s, deposit_paid_at=%s, status='searching'
                        WHERE id=%s AND payment_status='unpaid'
                        """,
                        (
                            "deposit_paid" if int(ride["deposit_amount"] or 0) else "fully_paid",
                            int(time.time()),
                            ref_command
                        )
                    )
                    assign_next_driver(conn, ref_command)
                elif event == "sale_canceled":
                    conn.execute(
                        """
                        UPDATE rides
                        SET status='payment_canceled'
                        WHERE id=%s AND payment_status='unpaid'
                        """,
                        (ref_command,)
                    )
                else:
                    return self.sendj({"error": "Événement IPN inconnu"}, 400)

            return self.sendj({"ok": True})


        # CLIENT : annulation avant acceptation / avant paiement validé.
        if (
            path.startswith("/api/rides/")
            and path.endswith("/cancel")
        ):
            if not self.check_rate("ride-cancel", 10, 3600):
                return
            ride_id = path.split("/")[3]
            tracking_token = str(data.get("tracking_token") or "").strip()
            reason = str(data.get("reason") or "Annulation client").strip()[:180]

            with db() as conn:
                ride = conn.execute(
                    """
                    SELECT id, status, payment_status, tracking_token
                    FROM rides
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (ride_id,)
                ).fetchone()

                if not ride:
                    return self.sendj({"error": "Course introuvable"}, 404)

                if not tracking_token or not hmac.compare_digest(
                    str(ride.get("tracking_token") or ""),
                    tracking_token
                ):
                    return self.sendj({"error": "Non autorisé"}, 401)

                if ride["status"] in ("cancelled", "canceled"):
                    return self.sendj({"ok": True, "status": "cancelled"})

                if ride["status"] in ("accepted", "arriving", "in_progress", "completed"):
                    return self.sendj({
                        "error": "La course ne peut plus être annulée automatiquement à ce stade."
                    }, 409)

                if ride.get("payment_status") not in ("unpaid", "failed"):
                    return self.sendj({
                        "error": "Un paiement a déjà été enregistré. L'annulation automatique est bloquée pour protéger votre paiement."
                    }, 409)

                if ride["status"] == "awaiting_payment":
                    return self.sendj({
                        "error": "Terminez ou annulez d'abord le paiement sécurisé en cours."
                    }, 409)

                conn.execute(
                    """
                    UPDATE rides
                    SET status='cancelled',
                        cancelled_at=%s,
                        cancel_reason=%s,
                        offered_driver_id=NULL,
                        offer_expires_at=NULL
                    WHERE id=%s
                    """,
                    (int(time.time()), reason, ride_id)
                )

            return self.sendj({"ok": True, "status": "cancelled"})

        # CLIENT : signaler un problème sans appeler un numéro personnel.
        if path == "/api/support":
            if not self.check_rate("support", 8, 3600):
                return

            ride_id = str(data.get("ride_id") or "").strip()[:80]
            tracking_token = str(data.get("tracking_token") or "").strip()
            category = str(data.get("category") or "other").strip()[:60]
            message = str(data.get("message") or "").strip()[:1000]

            if len(message) < 8:
                return self.sendj({"error": "Décrivez le problème en quelques mots."}, 400)

            allowed_categories = {
                "driver": "Chauffeur",
                "payment": "Paiement",
                "pickup": "Départ / destination",
                "app": "Application / site",
                "other": "Autre",
            }
            if category not in allowed_categories:
                category = "other"

            phone = ""
            if ride_id:
                with db() as conn:
                    ride = conn.execute(
                        """
                        SELECT phone, tracking_token
                        FROM rides
                        WHERE id=%s
                        """,
                        (ride_id,)
                    ).fetchone()
                if not ride:
                    return self.sendj({"error": "Course introuvable"}, 404)
                if not tracking_token or not hmac.compare_digest(
                    str(ride.get("tracking_token") or ""),
                    tracking_token
                ):
                    return self.sendj({"error": "Non autorisé"}, 401)
                phone = str(ride.get("phone") or "")[:40]

            request_id = "SUP-" + secrets.token_hex(6).upper()
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO support_requests(
                        id, ride_id, phone, category, message, status, created_at
                    )
                    VALUES(%s,%s,%s,%s,%s,'open',%s)
                    """,
                    (
                        request_id,
                        ride_id or None,
                        phone,
                        allowed_categories[category],
                        message,
                        int(time.time()),
                    )
                )

            return self.sendj({
                "ok": True,
                "request_id": request_id,
                "message": "Votre signalement a bien été enregistré."
            }, 201)

        if path == "/api/logout":
            return self.sendj(
                {"ok": True},
                extra_headers=[("Set-Cookie", session_cookie(clear=True))]
            )

        # RECHARGE COMPTE CHAUFFEUR
        if path == "/api/driver/recharge":

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            if not self.check_rate(
                "driver-recharge", 10, 3600, str(user.get("driver_id", ""))
            ):
                return

            try:
                amount = int(data.get("amount", 0))
            except:
                amount = 0

            payment = str(
                data.get("payment", "")
            ).strip()

            if amount < 500:
                return self.sendj(
                    {"error": "Montant minimum : 500 F"},
                    400
                )

            if payment not in (
                "wave",
                "orange_money"
            ):
                return self.sendj(
                    {"error": "Mode de paiement invalide"},
                    400
                )

            recharge_id = "RECH-" + secrets.token_hex(6).upper()

            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO driver_recharges(
                        id, driver_id, amount, payment, status, created_at
                    ) VALUES(%s, %s, %s, %s, 'pending', %s)
                    """,
                    (
                        recharge_id,
                        user.get("driver_id"),
                        amount,
                        payment,
                        int(time.time())
                    )
                )

            try:
                payment_url = request_paytech_recharge(
                    recharge_id,
                    amount,
                    payment,
                    user.get("driver_id")
                )
            except (RuntimeError, ValueError) as exc:
                with db() as conn:
                    conn.execute(
                        """
                        UPDATE driver_recharges
                        SET status='failed'
                        WHERE id=%s AND status='pending'
                        """,
                        (recharge_id,)
                    )
                return self.sendj({"error": str(exc)}, 502)

            return self.sendj({
                "ok": True,
                "payment_url": payment_url,
                "recharge_id": recharge_id
            })

        # INSCRIPTION CHAUFFEUR
        if path == "/api/register/driver":

            if not self.check_rate(
                "driver-register", 5, 3600, str(data.get("phone", ""))
            ):
                return

            name = str(
                data.get("name", "")
            ).strip()

            phone = normalize_phone(
                data.get("phone"), data.get("phone_region", "SN")
            )

            village = str(
                data.get("village", "")
            ).strip()

            vehicle = str(
                data.get("vehicle", "")
            ).strip()

            pin = str(
                data.get("pin", "")
            ).strip()

            legal_documents_declared = data.get("legal_documents_declared") is True
            terms_accepted = data.get("terms_accepted") is True
            privacy_accepted = data.get("privacy_accepted") is True
            compliance_version = str(data.get("compliance_version", "SN-2026-09-v3"))[:50]

            driving_licence_number = str(data.get("driving_licence_number", "")).strip()
            driving_licence_expiry = str(data.get("driving_licence_expiry", "")).strip()
            insurance_policy_number = str(data.get("insurance_policy_number", "")).strip()
            insurance_expiry = str(data.get("insurance_expiry", "")).strip()
            vehicle_plate = str(data.get("vehicle_plate", "")).strip().upper()
            registration_card_number = str(data.get("registration_card_number", "")).strip()
            technical_inspection_expiry = str(data.get("technical_inspection_expiry", "")).strip()
            transport_authorisation_reference = str(data.get("transport_authorisation_reference", "")).strip()

            if not legal_documents_declared:
                return self.sendj({"error": "La déclaration des documents et autorisations du chauffeur est obligatoire."}, 400)
            if not terms_accepted or not privacy_accepted:
                return self.sendj({"error": "Acceptation des conditions et de la politique de confidentialité requise."}, 400)

            # Les exigences documentaires dépendent de la catégorie.
            # Une voiture taxi doit fournir le dossier réglementaire complet.
            # Pour Moto-taxi / 3 roues, seules les références réellement applicables
            # à la catégorie et à la zone sont exigées ; l'admin effectue ensuite
            # la vérification manuelle avant toute activation.
            strict_legal_profile = vehicle == "Voiture taxi"

            if strict_legal_profile:
                required_legal = {
                    "numéro de permis": driving_licence_number,
                    "date d’expiration du permis": driving_licence_expiry,
                    "numéro de police d’assurance": insurance_policy_number,
                    "date d’expiration de l’assurance": insurance_expiry,
                    "immatriculation du véhicule": vehicle_plate,
                    "numéro de carte grise": registration_card_number,
                    "date d’expiration de la visite technique": technical_inspection_expiry,
                    "référence de l’autorisation/licence de transport": transport_authorisation_reference,
                }
                missing = [label for label, value in required_legal.items() if not value]
                if missing:
                    return self.sendj({"error": "Dossier voiture taxi incomplet : " + ", ".join(missing)}, 400)

            today = time.strftime("%Y-%m-%d")
            for label, value in (
                ("permis", driving_licence_expiry),
                ("assurance", insurance_expiry),
                ("visite technique", technical_inspection_expiry),
            ):
                if not value:
                    continue
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    return self.sendj({"error": f"Date invalide pour {label} (AAAA-MM-JJ)."}, 400)
                if value < today:
                    return self.sendj({"error": f"Le document {label} est expiré."}, 400)


            if len(name) < 2:
                return self.sendj(
                    {
                        "error":
                        "Nom et prénom requis"
                    },
                    400
                )


            if not phone:
                return self.sendj(
                    {
                        "error":
                        "Numéro de téléphone invalide"
                    },
                    400
                )


            if len(village) < 2 or len(village) > 100:
                return self.sendj(
                    {"error": "Indiquez votre localité ou votre zone d’activité."},
                    400
                )


            if vehicle not in ALLOWED_VEHICLES:
                return self.sendj(
                    {
                        "error":
                        "Type de véhicule invalide"
                    },
                    400
                )


            if (
                not pin.isdigit()
                or len(pin) < 4
                or len(pin) > 6
            ):
                return self.sendj(
                    {
                        "error":
                        "Le PIN doit contenir 4 à 6 chiffres"
                    },
                    400
                )


            pin_hash, pin_salt = hash_pin(pin)

            driver_id = (
                "DRV-"
                + secrets.token_hex(5).upper()
            )


            try:
                with db() as conn:
                    # Évite de réinscrire un numéro sénégalais déjà conservé
                    # au format national dans une ancienne version du site.
                    legacy_phone = phone[4:] if phone.startswith("+221") else phone
                    existing = conn.execute(
                        "SELECT id FROM drivers WHERE phone IN (%s, %s)",
                        (phone, legacy_phone)
                    ).fetchone()
                    if existing:
                        return self.sendj(
                            {"error": "Ce numéro est déjà inscrit"}, 409
                        )
                    conn.execute(
                        """
                        INSERT INTO drivers(
                            id,
                            name,
                            phone,
                            village,
                            vehicle,
                            pin_hash,
                            pin_salt,
                            status,
                            created_at,
                            legal_documents_declared,
                            terms_accepted_at,
                            privacy_accepted_at,
                            compliance_version,
                            driving_licence_number,
                            driving_licence_expiry,
                            insurance_policy_number,
                            insurance_expiry,
                            vehicle_plate,
                            registration_card_number,
                            technical_inspection_expiry,
                            transport_authorisation_reference,
                            compliance_verified
                        )
                        VALUES(
                            %s,%s,%s,%s,%s,
                            %s,%s,%s,%s,
                            %s,%s,%s,%s,
                            %s,%s,%s,%s,%s,
                            %s,%s,%s,%s
                        )
                        """,
                        (
                            driver_id,
                            name[:100],
                            phone,
                            village,
                            vehicle,
                            pin_hash,
                            pin_salt,
                            "pending",
                            int(time.time()),
                            True,
                            int(time.time()),
                            int(time.time()),
                            compliance_version,
                            driving_licence_number[:80],
                            driving_licence_expiry,
                            insurance_policy_number[:100],
                            insurance_expiry,
                            vehicle_plate[:30],
                            registration_card_number[:100],
                            technical_inspection_expiry,
                            transport_authorisation_reference[:150],
                            False
                        )
                    )

            except psycopg.errors.UniqueViolation:
                return self.sendj(
                    {
                        "error":
                        "Ce numéro est déjà inscrit"
                    },
                    409
                )


            return self.sendj(
                {
                    "ok": True,
                    "status": "pending",
                    "message":
                    "Inscription envoyée. Votre compte doit être validé par SoninkaraGo."
                },
                201
            )


        # CONNEXION CHAUFFEUR
        if path == "/api/login/driver":

            if not self.check_rate(
                "driver-login", 8, 600, str(data.get("phone", ""))
            ):
                return

            raw_phone = str(data.get("phone", "")).strip()
            phone = normalize_phone(
                raw_phone, data.get("phone_region", "SN")
            )

            if not phone:
                return self.sendj(
                    {"error": "Numéro de téléphone invalide"}, 400
                )

            pin = str(
                data.get("pin", "")
            ).strip()


            with db() as conn:
                driver = conn.execute(
                    """
                    SELECT *
                    FROM drivers
                    WHERE phone IN (%s, %s, %s)
                    ORDER BY CASE WHEN phone=%s THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (
                        phone,
                        phone[4:] if phone.startswith("+221") else phone,
                        raw_phone,
                        phone
                    )
                ).fetchone()


            if not driver:
                return self.sendj(
                    {
                        "error":
                        "Téléphone ou PIN incorrect"
                    },
                    401
                )


            if driver["status"] == "pending":
                return self.sendj(
                    {
                        "error":
                        "Votre inscription est encore en attente de validation"
                    },
                    403
                )


            if driver["status"] == "rejected":
                return self.sendj(
                    {
                        "error":
                        "Votre inscription n'a pas été acceptée"
                    },
                    403
                )


            if driver["status"] != "approved":
                return self.sendj(
                    {
                        "error":
                        "Compte chauffeur inactif"
                    },
                    403
                )


            if not verify_pin(
                pin,
                driver["pin_hash"],
                driver["pin_salt"]
            ):
                return self.sendj(
                    {
                        "error":
                        "Téléphone ou PIN incorrect"
                    },
                    401
                )


            token = make_token("driver", driver["name"], driver["id"])
            response = {
                "authenticated": True,
                "name": driver["name"],
                "vehicle": driver["vehicle"],
                "village": driver["village"]
            }
            if str(self.headers.get("X-SoninkaraGo-App", "")).lower() in ("ios", "android", "mobile"):
                response["access_token"] = token
            return self.sendj(
                response,
                extra_headers=[("Set-Cookie", session_cookie(token))]
            )


        # EXERCICE DES DROITS SUR LES DONNÉES PERSONNELLES
        if path == "/api/privacy/request":
            request_type = str(data.get("request_type", "")).strip().lower()
            if request_type not in ("access", "rectification", "deletion", "opposition"):
                return self.sendj({"error": "Type de demande invalide"}, 400)
            name = str(data.get("name", "")).strip()[:120]
            phone = str(data.get("phone", "")).strip()[:40]
            email = str(data.get("email", "")).strip()[:160]
            details = str(data.get("details", "")).strip()[:1200]
            if not phone and not email:
                return self.sendj({"error": "Indiquez un téléphone ou un e-mail pour être recontacté."}, 400)
            req_id = "PRV-" + secrets.token_hex(6).upper()
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO privacy_requests(id,request_type,name,phone,email,details,status,created_at)
                    VALUES(%s,%s,%s,%s,%s,%s,'received',%s)
                    """,
                    (req_id, request_type, name, phone, email, details, int(time.time()))
                )
            return self.sendj({"ok": True, "request_id": req_id, "message": "Votre demande a été enregistrée."})

        # SUPPRESSION DU COMPTE CHAUFFEUR ET ANONYMISATION
        if path == "/api/driver/account/delete":
            user = self.auth()
            if not user or user.get("role") != "driver":
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            driver_id = str(user.get("driver_id", ""))
            if not self.check_rate("driver-delete", 3, 3600, driver_id):
                return

            pin = str(data.get("pin", "")).strip()
            with db() as conn:
                driver = conn.execute(
                    "SELECT * FROM drivers WHERE id=%s FOR UPDATE",
                    (driver_id,)
                ).fetchone()

                if not driver or not verify_pin(
                    pin,
                    driver["pin_hash"],
                    driver["pin_salt"]
                ):
                    return self.sendj({"error": "PIN incorrect"}, 401)

                active = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM rides
                    WHERE driver_id=%s AND status='accepted'
                    """,
                    (driver_id,)
                ).fetchone()
                if int(active["n"] or 0) > 0:
                    return self.sendj({
                        "error": "Terminez votre course active avant de supprimer le compte."
                    }, 409)

                conn.execute(
                    "DELETE FROM driver_recharges WHERE driver_id=%s",
                    (driver_id,)
                )
                conn.execute(
                    """
                    UPDATE rides
                    SET driver_id=NULL, driver_name='Compte supprimé'
                    WHERE driver_id=%s
                    """,
                    (driver_id,)
                )
                audit_event(conn, "driver", driver_id, "driver.account.delete", "driver", driver_id,
                            {"phone_hash": hashlib.sha256(str(driver.get("phone","")).encode()).hexdigest()[:16]})
                conn.execute(
                    "DELETE FROM drivers WHERE id=%s",
                    (driver_id,)
                )

            return self.sendj({
                "ok": True,
                "message": "Votre compte et vos données chauffeur ont été supprimés."
            })


        # CONNEXION ADMIN
        if path == "/api/login/admin":

            if not self.check_rate("admin-login", 5, 900):
                return

            password = str(
                data.get(
                    "password",
                    ""
                )
            )

            if (
                not ADMIN_PASSWORD
                or not hmac.compare_digest(
                    password,
                    ADMIN_PASSWORD
                )
            ):
                try:
                    with db() as conn:
                        audit_event(conn, "anonymous", "", "admin.login", "admin", "Admin",
                                    {"user_agent": str(self.headers.get("User-Agent",""))[:240]},
                                    outcome="denied")
                except Exception:
                    pass
                return self.sendj(
                    {
                        "error":
                        "Mot de passe incorrect"
                    },
                    401
                )

            try:
                with db() as conn:
                    audit_event(conn, "admin", "Admin", "admin.login", "admin", "Admin",
                                {"user_agent": str(self.headers.get("User-Agent",""))[:240]})
            except Exception:
                pass

            token = make_token("admin", "Admin")
            response = {"authenticated": True, "name": "Admin"}
            if str(self.headers.get("X-SoninkaraGo-App", "")).lower() in ("ios", "android", "mobile"):
                response["access_token"] = token
            return self.sendj(
                response,
                extra_headers=[("Set-Cookie", session_cookie(token))]
            )


        if path == "/api/admin/payments/reconciliation":
            user = self.auth()
            if not user or user.get("role") != "admin":
                return self.sendj({"error": "Non autorisé"}, 401)
            with db() as conn:
                rides = conn.execute(
                    """
                    SELECT id,fare,deposit_amount,balance_due,payment_status,status,
                           deposit_paid_at,balance_paid_at,created_at
                    FROM rides
                    WHERE created_at >= %s
                    ORDER BY created_at DESC
                    LIMIT 500
                    """,
                    (int(time.time()) - 7*86400,)
                ).fetchall()
                recharges = conn.execute(
                    """
                    SELECT id,driver_id,amount,status,created_at,paid_at
                    FROM driver_recharges
                    WHERE created_at >= %s
                    ORDER BY created_at DESC
                    LIMIT 500
                    """,
                    (int(time.time()) - 7*86400,)
                ).fetchall()
            anomalies = []
            for r in rides:
                if r.get("payment_status") in ("deposit_paid","fully_paid") and not r.get("deposit_paid_at"):
                    anomalies.append({"type":"ride_missing_paid_timestamp","id":r["id"]})
                if r.get("status") == "searching" and r.get("payment_status") == "unpaid":
                    anomalies.append({"type":"ride_searching_unpaid","id":r["id"]})
            for x in recharges:
                if x.get("status") == "paid" and not x.get("paid_at"):
                    anomalies.append({"type":"recharge_missing_paid_timestamp","id":x["id"]})
            return self.sendj({
                "window_days": 7,
                "rides_count": len(rides),
                "recharges_count": len(recharges),
                "anomalies": anomalies
            })


        # ADMIN VERIFIE LE DOSSIER REGLEMENTAIRE DU CHAUFFEUR
        if (
            path.startswith("/api/admin/drivers/")
            and path.endswith("/verify-docs")
        ):

            user = self.auth()
            if not user or user.get("role") != "admin":
                return self.sendj({"error": "Non autorisé"}, 401)

            driver_id = path.split("/")[4]
            with db() as conn:
                row = conn.execute(
                    """
                    SELECT
                        vehicle,
                        driving_licence_number, driving_licence_expiry,
                        insurance_policy_number, insurance_expiry,
                        vehicle_plate, registration_card_number,
                        technical_inspection_expiry,
                        transport_authorisation_reference
                    FROM drivers WHERE id=%s
                    """,
                    (driver_id,)
                ).fetchone()
                if not row:
                    return self.sendj({"error": "Chauffeur introuvable"}, 404)

                # Pour une voiture taxi, toutes les références prévues par le
                # profil strict doivent être présentes avant vérification.
                # Pour Moto-taxi / 3 roues, l'admin vérifie les pièces réellement
                # applicables à la catégorie et peut valider le dossier sans forcer
                # des références qui n'existent pas légalement pour cette activité.
                if row.get("vehicle") == "Voiture taxi":
                    if not all(row.get(k) for k in (
                        "driving_licence_number","driving_licence_expiry",
                        "insurance_policy_number","insurance_expiry",
                        "vehicle_plate","registration_card_number",
                        "technical_inspection_expiry","transport_authorisation_reference"
                    )):
                        return self.sendj({"error": "Le dossier voiture taxi est incomplet."}, 400)

                notes = str(data.get("notes", ""))[:1500]
                checklist = data.get("checklist") or {
                    "identity_checked": True,
                    "documents_applicable_checked": True,
                    "expiry_dates_checked": True,
                    "vehicle_category_checked": True,
                }
                now = int(time.time())
                conn.execute(
                    """
                    UPDATE drivers
                    SET compliance_verified=TRUE, compliance_verified_at=%s
                    WHERE id=%s
                    """,
                    (now, driver_id)
                )
                conn.execute(
                    """
                    INSERT INTO driver_compliance_checks(
                        id,driver_id,reviewer_role,checklist_json,notes,decision,created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (uuid.uuid4().hex, driver_id, "admin", safe_json(checklist), notes, "verified", now)
                )
                audit_event(conn, "admin", "Admin", "driver.compliance.verify",
                            "driver", driver_id, {"vehicle": row.get("vehicle"), "notes": notes})

            return self.sendj({"ok": True, "compliance_verified": True})


        # ADMIN ACCEPTE CHAUFFEUR
        if (
            path.startswith("/api/admin/drivers/")
            and path.endswith("/approve")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "admin"
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            driver_id = path.split("/")[4]

            with db() as conn:
                row = conn.execute(
                    "SELECT compliance_verified FROM drivers WHERE id=%s",
                    (driver_id,)
                ).fetchone()
                if not row:
                    return self.sendj({"error": "Chauffeur introuvable"}, 404)
                if not bool(row.get("compliance_verified")):
                    return self.sendj(
                        {"error": "Vérifiez d’abord le dossier réglementaire du chauffeur avant de l’accepter."},
                        400
                    )
                cur = conn.execute(
                    """
                    UPDATE drivers
                    SET status='approved'
                    WHERE id=%s AND compliance_verified=TRUE
                    """,
                    (driver_id,)
                )

            if not cur.rowcount:
                return self.sendj(
                    {
                        "error":
                        "Chauffeur introuvable"
                    },
                    404
                )

            with db() as conn:
                audit_event(conn, "admin", "Admin", "driver.approve", "driver", driver_id, {})
            return self.sendj({
                "ok": True,
                "status": "approved"
            })


        # ADMIN REFUSE CHAUFFEUR
        if (
            path.startswith("/api/admin/drivers/")
            and path.endswith("/reject")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "admin"
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            driver_id = path.split("/")[4]

            reason = str(data.get("reason", "")).strip()[:1000]
            with db() as conn:
                cur = conn.execute(
                    """
                    UPDATE drivers
                    SET status='rejected'
                    WHERE id=%s
                    """,
                    (driver_id,)
                )
                if cur.rowcount:
                    conn.execute(
                        """
                        INSERT INTO driver_compliance_checks(
                            id,driver_id,reviewer_role,checklist_json,notes,decision,created_at
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (uuid.uuid4().hex, driver_id, "admin", "{}", reason, "rejected", int(time.time()))
                    )
                    audit_event(conn, "admin", "Admin", "driver.reject", "driver", driver_id,
                                {"reason": reason})

            if not cur.rowcount:
                return self.sendj(
                    {
                        "error":
                        "Chauffeur introuvable"
                    },
                    404
                )

            return self.sendj({
                "ok": True,
                "status": "rejected"
            })


              # CRÉATION COURSE CLIENT
        if path == "/api/rides":

            if not self.check_rate(
                "ride-create", 30, 3600, str(data.get("phone", ""))
            ):
                return

            route_code = str(
                data.get("route_code", "")
            ).strip()

            route = ROUTES.get(route_code)
            local_ride = route_code in LOCAL_SERVICE_CONFIG
            urban_ride = route_code == "dakar_car" or route_code.startswith("urban_car_")
            if local_ride:
                try:
                    quote = verify_local_quote(str(data.get("quote_token", "")), route_code)
                except ValueError as exc:
                    return self.sendj({"error": str(exc)}, 400)
                route = {
                    "service": LOCAL_SERVICE_CONFIG[route_code]["service"],
                    "pickup": quote["pickup"],
                    "destination": quote["destination"],
                    "fare": quote["fare"],
                }
            if urban_ride:
                try:
                    quote = verify_dakar_quote(str(data.get("quote_token", "")))
                except ValueError as exc:
                    return self.sendj({"error": str(exc)}, 400)
                expected_zone = "dakar" if route_code == "dakar_car" else route_code.removeprefix("urban_car_")
                if quote.get("zone", "dakar") != expected_zone:
                    return self.sendj({"error": "Le devis ne correspond pas à la ville choisie."}, 400)
                route = {"service": "Voiture taxi", "pickup": quote["pickup"],
                         "destination": quote["destination"], "fare": quote["fare"]}

            if not route:
                return self.sendj(
                    {"error": "Ce trajet n'est pas disponible"},
                    400
                )

            is_minicar = (
                route["service"] == "Minicar 14 places"
            )

            departure_date = str(
                data.get("departure_date", "")
            ).strip()

            departure_time = str(
                data.get("departure_time", "")
            ).strip()

            meeting_point = str(
                data.get("meeting_point", "")
            ).strip()[:200]

            try:
                passenger_count = int(
                    data.get("passenger_count", 1)
                )
            except (TypeError, ValueError):
                passenger_count = 1

            if is_minicar:
                if not departure_date:
                    return self.sendj(
                        {"error": "Date de départ obligatoire"},
                        400
                    )

                if not departure_time:
                    return self.sendj(
                        {"error": "Heure de départ obligatoire"},
                        400
                    )

                if not meeting_point:
                    return self.sendj(
                        {"error": "Lieu de rendez-vous obligatoire"},
                        400
                    )

                if passenger_count < 1 or passenger_count > 14:
                    return self.sendj(
                        {"error": "Maximum 14 passagers"},
                        400
                    )

            fare = int(route["fare"])
            fee = (fare + 9) // 10

            deposit_amount = (
                fare // 2 if is_minicar else 0
            )

            balance_due = (
                fare - deposit_amount if is_minicar else 0
            )

            ride_id = (
                "SG-" + secrets.token_hex(6).upper()
            )

            tracking_token = secrets.token_urlsafe(32)

            client_name = str(
                data.get("client_name", "Client")
            ).strip()[:80]

            phone = normalize_phone(
                data.get("phone"), data.get("phone_region", "SN")
            )
            if not phone:
                return self.sendj(
                    {"error": "Numéro de téléphone invalide. Vérifiez le pays et le numéro."},
                    400
                )

            payment = str(
                data.get("payment", "Espèces")
            ).strip()[:30]

            if payment not in ("Espèces", "Wave", "Orange Money"):
                return self.sendj(
                    {"error": "Mode de paiement invalide"},
                    400
                )

            mobile_payment = payment in ("Wave", "Orange Money")

            luggage = str(
                data.get("luggage", "")
            ).strip()[:30]

            booking_note = str(
                data.get("booking_note", "")
            ).strip()[:300]

            client_coords = valid_coords(
                data.get("client_lat"),
                data.get("client_lng")
            )
            if urban_ride:
                # Le départ géocodé du trajet détermine l'attribution.
                client_coords = (quote["lat"], quote["lng"])
            if not is_minicar and not client_coords:
                return self.sendj(
                    {"error": "Activez votre position GPS pour trouver le chauffeur le plus proche"},
                    400
                )
            client_lat, client_lng = client_coords or (None, None)

            driver_available = True
            if urban_ride:
                urban_zone_code = quote.get("zone", "dakar")
                with db() as conn:
                    nearby = conn.execute("""
                        SELECT latitude, longitude FROM drivers
                        WHERE status='approved' AND online=TRUE AND vehicle='Voiture taxi'
                          AND latitude IS NOT NULL AND longitude IS NOT NULL
                          AND last_location_at >= %s AND balance >= %s
                          AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.driver_id=drivers.id AND r.status='accepted')
                    """, (int(time.time()) - 300, fee)).fetchall()
                driver_available = any(
                    in_urban_service_zone(urban_zone_code, d["latitude"], d["longitude"])
                    and distance_km(client_lat, client_lng, d["latitude"], d["longitude"]) <= 20
                    for d in nearby
                )
            # Fonctionnement production : une commande reste active même si aucun chauffeur
            # n'est disponible au moment précis de la demande. Elle est conservée en recherche
            # et pourra être proposée dès qu'un chauffeur éligible se connecte.
            initial_status = "awaiting_payment" if mobile_payment else "searching"
            initial_payment_status = "unpaid"

            assigned_driver = None
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO rides(
                        id,
                        client_name,
                        phone,
                        pickup,
                        destination,
                        vehicle,
                        payment,
                        fare,
                        fee,
                        status,
                        driver_name,
                        client_lat,
                        client_lng,
                        created_at,
                        tracking_token,
                        route_code,
                        departure_date,
                        departure_time,
                        meeting_point,
                        passenger_count,
                        luggage,
                        booking_note,
                        payment_status,
                        deposit_amount,
                        balance_due,
                        commission_charged,
                        terms_accepted_at,
                        privacy_accepted_at,
                        location_consent_at,
                        compliance_version
                    )
                    VALUES(
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s
                    )
                    """,
                    (
                        ride_id,
                        client_name or "Client",
                        phone,
                        route["pickup"],
                        route["destination"],
                        route["service"],
                        payment,
                        fare,
                        fee,
                        initial_status,
                        "",
                        client_lat,
                        client_lng,
                        int(time.time()),
                        tracking_token,
                        route_code,
                        departure_date,
                        departure_time,
                        meeting_point,
                        passenger_count,
                        luggage,
                        booking_note,
                        initial_payment_status,
                        deposit_amount,
                        balance_due,
                        False,
                        int(time.time()),
                        int(time.time()),
                        int(time.time()) if data.get("location_consent") is True else None,
                        str(data.get("compliance_version", "SN-2026-09-v3"))[:50]
                    )
                )
                if not mobile_payment:
                    assigned_driver = assign_next_driver(conn, ride_id)

            response = {
                    "id": ride_id,
                    "pickup": route["pickup"],
                    "destination": route["destination"],
                    "vehicle": route["service"],
                    "fare": fare,
                    "fee": fee,
                    "status": initial_status,
                    "payment_status": initial_payment_status,
                    "deposit_amount": deposit_amount,
                    "balance_due": balance_due,
                    "tracking_token": tracking_token
                }

            if not mobile_payment and not is_minicar:
                response["dispatch_status"] = (
                    "offered" if assigned_driver else "waiting_for_driver"
                )

            if mobile_payment:
                amount = deposit_amount if deposit_amount else fare
                try:
                    response["payment_url"] = request_paytech_payment(
                        ride_id,
                        route,
                        amount,
                        payment,
                        client_name or "Client"
                    )
                except (RuntimeError, ValueError) as exc:
                    with db() as conn:
                        conn.execute(
                            "UPDATE rides SET status='payment_failed', payment_status='failed' WHERE id=%s",
                            (ride_id,)
                        )
                    return self.sendj({
                        "error": str(exc),
                        "ride_id": ride_id
                    }, 502)

            return self.sendj(response, 201)


                       # CHAUFFEUR REFUSE UNE PROPOSITION
        if (
            path.startswith("/api/rides/")
            and path.endswith("/decline")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            ride_id = path.split("/")[3]
            driver_id = user.get("driver_id")
            with db() as conn:
                ride = conn.execute(
                    """
                    SELECT status, vehicle, offered_driver_id
                    FROM rides
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (ride_id,)
                ).fetchone()

                if (
                    not ride
                    or ride["status"] != "searching"
                    or ride["vehicle"] == "Minicar 14 places"
                    or ride.get("offered_driver_id") != driver_id
                ):
                    return self.sendj(
                        {"error": "Cette proposition n'est plus disponible"},
                        409
                    )

                conn.execute(
                    """
                    UPDATE rides
                    SET offered_driver_id=NULL, offer_expires_at=NULL
                    WHERE id=%s AND status='searching'
                    """,
                    (ride_id,)
                )
                assign_next_driver(conn, ride_id)

            return self.sendj({"ok": True, "message": "Course proposée au chauffeur suivant."})

                       # CHAUFFEUR ACCEPTE COURSE
        if (
            path.startswith("/api/rides/")
            and path.endswith("/accept")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            ride_id = path.split("/")[3]
            driver_id = user.get("driver_id")

            with db() as conn:

                ride = conn.execute(
                    """
                    SELECT fare, fee, vehicle, offered_driver_id, offer_expires_at
                    FROM rides
                    WHERE id=%s
                      AND status='searching'
                    FOR UPDATE
                    """,
                    (ride_id,)
                ).fetchone()

                if not ride:
                    return self.sendj(
                        {"error": "Course déjà prise"},
                        409
                    )

                commission = int(ride["fee"] or 0)
                is_minicar = (
                    ride["vehicle"] == "Minicar 14 places"
                )

                if not is_minicar and (
                    ride.get("offered_driver_id") != driver_id
                    or int(ride.get("offer_expires_at") or 0) <= int(time.time())
                ):
                    return self.sendj(
                        {"error": "Le délai de 15 secondes est terminé. La course a été proposée au chauffeur suivant."},
                        409
                    )

                driver = conn.execute(
                    """
                    SELECT balance, vehicle
                    FROM drivers
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (driver_id,)
                ).fetchone()

                balance = int(
                    driver["balance"] or 0
                ) if driver else 0

                if not driver or driver["vehicle"] != ride["vehicle"]:
                    return self.sendj(
                        {"error": "Cette course ne correspond pas à votre véhicule."},
                        409
                    )

                if balance < commission:
                    return self.sendj(
                        {
                            "error":
                            f"Solde insuffisant. Vous devez avoir au moins {commission} F.",
                            "required": commission,
                            "balance": balance
                        },
                        402
                    )

                # Pour les courses ordinaires :
                # commission retirée immédiatement.
                if not is_minicar:
                    conn.execute(
                        """
                        UPDATE drivers
                        SET balance=balance-%s
                        WHERE id=%s
                        """,
                        (commission, driver_id)
                    )

                cur = conn.execute(
                    """
                    UPDATE rides
                    SET
                        status='accepted',
                        driver_name=%s,
                        driver_id=%s,
                        commission_charged=%s,
                        offered_driver_id=NULL,
                        offer_expires_at=NULL
                    WHERE id=%s
                      AND status='searching'
                    """,
                    (
                        user.get("name", "Chauffeur"),
                        driver_id,
                        not is_minicar,
                        ride_id
                    )
                )

                if not cur.rowcount:
                    return self.sendj(
                        {"error": "Course déjà prise"},
                        409
                    )

            return self.sendj({
                "ok": True,
                "commission": commission,
                "commission_charged": not is_minicar,
                "message": (
                    "Réservation acceptée. Confirmez les premiers 50 % après réception."
                    if is_minicar
                    else "Course acceptée."
                )
            })
        # CONFIRMATION DES PREMIERS 50 %
        if (
            path.startswith("/api/rides/")
            and path.endswith("/confirm-deposit")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            ride_id = path.split("/")[3]
            driver_id = user.get("driver_id")

            with db() as conn:

                ride = conn.execute(
                    """
                    SELECT *
                    FROM rides
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (ride_id,)
                ).fetchone()

                if not ride:
                    return self.sendj(
                        {"error": "Réservation introuvable"},
                        404
                    )

                if ride["driver_id"] != driver_id:
                    return self.sendj(
                        {"error": "Cette réservation ne vous appartient pas"},
                        403
                    )

                if ride["vehicle"] != "Minicar 14 places":
                    return self.sendj(
                        {"error": "Cette course n'est pas un minicar"},
                        400
                    )

                if ride["commission_charged"]:
                    return self.sendj(
                        {"error": "Premier paiement déjà confirmé"},
                        409
                    )

                if ride["status"] != "accepted":
                    return self.sendj(
                        {"error": "Réservation non acceptée"},
                        409
                    )

                commission = int(ride["fee"] or 0)

                debit = conn.execute(
                    """
                    UPDATE drivers
                    SET balance=balance-%s
                    WHERE id=%s
                      AND balance >= %s
                    """,
                    (
                        commission,
                        driver_id,
                        commission
                    )
                )

                if not debit.rowcount:
                    return self.sendj(
                        {
                            "error":
                            f"Solde insuffisant. Il faut {commission} F."
                        },
                        402
                    )

                conn.execute(
                    """
                    UPDATE rides
                    SET
                        status='deposit_paid',
                        payment_status='deposit_paid',
                        commission_charged=TRUE,
                        deposit_paid_at=%s
                    WHERE id=%s
                    """,
                    (
                        int(time.time()),
                        ride_id
                    )
                )

            return self.sendj({
                "ok": True,
                "deposit_amount": int(
                    ride["deposit_amount"] or 0
                ),
                "commission": commission,
                "message":
                "Premier paiement confirmé. Commission SoninkaraGo retirée."
            })


        # CONFIRMATION DES 50 % RESTANTS
        if (
            path.startswith("/api/rides/")
            and path.endswith("/confirm-balance")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            ride_id = path.split("/")[3]
            driver_id = user.get("driver_id")

            with db() as conn:

                ride = conn.execute(
                    """
                    SELECT *
                    FROM rides
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (ride_id,)
                ).fetchone()

                if not ride:
                    return self.sendj(
                        {"error": "Réservation introuvable"},
                        404
                    )

                if ride["driver_id"] != driver_id:
                    return self.sendj(
                        {"error": "Cette réservation ne vous appartient pas"},
                        403
                    )

                if ride["payment_status"] != "deposit_paid":
                    return self.sendj(
                        {
                            "error":
                            "Confirmez d'abord les premiers 50 %."
                        },
                        409
                    )

                conn.execute(
                    """
                    UPDATE rides
                    SET
                        status='fully_paid',
                        payment_status='fully_paid',
                        balance_paid_at=%s
                    WHERE id=%s
                    """,
                    (
                        int(time.time()),
                        ride_id
                    )
                )

            return self.sendj({
                "ok": True,
                "balance_paid": int(
                    ride["balance_due"] or 0
                ),
                "message": "Paiement total confirmé."
            })

        


        # CHAUFFEUR TERMINE COURSE
        if (
            path.startswith("/api/rides/")
            and path.endswith("/complete")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {
                        "error":
                        "Connexion chauffeur requise"
                    },
                    401
                )

            ride_id = path.split("/")[3]

            with db() as conn:
                                
                cur = conn.execute(
                    """
                    UPDATE rides
                    SET status='completed'
                    WHERE id=%s
                      AND driver_id=%s
                      AND (
                          (
                              vehicle='Minicar 14 places'
                              AND payment_status='fully_paid'
                          )
                          OR
                          (
                              vehicle<>'Minicar 14 places'
                              AND status='accepted'
                          )
                      )
                    """,
                    (ride_id, user.get("driver_id"))
                )
            if not cur.rowcount:
                return self.sendj(
                    {
                        "error":
                        "Pour un minicar, confirmez les deux paiements avant de terminer."
                    },
                    409
                )

            return self.sendj({
                "ok": True
            })
                    # POSITION GPS CLIENT
        if (
            path.startswith("/api/rides/")
            and path.endswith("/location/client")
        ):

            ride_id = path.split("/")[3]

            tracking_token = str(
                data.get("tracking_token", "")
            )

            coords = valid_coords(
                data.get("lat"),
                data.get("lng")
            )

            if not coords:
                return self.sendj(
                    {"error": "Position GPS invalide"},
                    400
                )

            lat, lng = coords

            with db() as conn:
                ride = conn.execute(
                    """
                    SELECT status, tracking_token, route_code
                    FROM rides
                    WHERE id=%s
                    """,
                    (ride_id,)
                ).fetchone()

                if not ride:
                    return self.sendj(
                        {"error": "Course introuvable"},
                        404
                    )

                if ride["route_code"] == "dakar_car":
                    return self.sendj({"error": "Le lieu de départ de cette course est fixe."}, 409)

                if not hmac.compare_digest(
                    str(ride.get("tracking_token") or ""),
                    tracking_token
                ):
                    return self.sendj(
                        {"error": "Non autorisé"},
                        401
                    )

                if ride["status"] == "completed":
                    return self.sendj(
                        {"error": "Course terminée"},
                        409
                    )

                conn.execute(
                    """
                    UPDATE rides
                    SET
                        client_lat=%s,
                        client_lng=%s,
                        client_location_at=%s
                    WHERE id=%s
                    """,
                    (
                        lat,
                        lng,
                        int(time.time()),
                        ride_id
                    )
                )

            return self.sendj({"ok": True})
                    # CHAUFFEUR HORS LIGNE

        if path == "/api/driver/offline":

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            now = int(time.time())
            with db() as conn:
                conn.execute(
                    "UPDATE drivers SET online=FALSE WHERE id=%s",
                    (user.get("driver_id"),)
                )
                conn.execute(
                    """
                    UPDATE rides
                    SET offer_expires_at=%s
                    WHERE status='searching'
                      AND offered_driver_id=%s
                    """,
                    (now, user.get("driver_id"))
                )
                dispatch_pending_rides(conn)

            return self.sendj({"ok": True, "online": False})

                    # CHAUFFEUR EN LIGNE + POSITION GPS

        if path == "/api/driver/location":

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            coords = valid_coords(
                data.get("lat"),
                data.get("lng")
            )

            if not coords:
                return self.sendj(
                    {"error": "Position GPS invalide"},
                    400
                )

            lat, lng = coords

            with db() as conn:
                conn.execute(
                    """
                    UPDATE drivers
                    SET
                        online=TRUE,
                        latitude=%s,
                        longitude=%s,
                        last_location_at=%s
                    WHERE id=%s
                    """,
                    (
                        lat,
                        lng,
                        int(time.time()),
                        user.get("driver_id")
                    )
                )
                dispatch_pending_rides(conn)

            return self.sendj({"ok": True})
        # POSITION GPS CHAUFFEUR
        if (
        path.startswith("/api/rides/")
        and path.endswith("/location/driver")
        ):

            user = self.auth()

            if (
                not user
                or user.get("role") != "driver"
            ):
                return self.sendj(
                    {"error": "Connexion chauffeur requise"},
                    401
                )

            ride_id = path.split("/")[3]

            coords = valid_coords(
                data.get("lat"),
                data.get("lng")
            )

            if not coords:
                return self.sendj(
                    {"error": "Position GPS invalide"},
                    400
                )

            lat, lng = coords

            with db() as conn:
                ride = conn.execute(
                    """
                    SELECT status, driver_id
                    FROM rides
                    WHERE id=%s
                    """,
                    (ride_id,)
                ).fetchone()

                if not ride:
                    return self.sendj(
                        {"error": "Course introuvable"},
                        404
                    )

                if ride["driver_id"] != user.get("driver_id"):
                    return self.sendj(
                        {"error": "Cette course ne vous appartient pas"},
                        403
                    )

                if ride["status"] != "accepted":
                    return self.sendj(
                        {"error": "Course non active"},
                        409
                    )

                conn.execute(
                    """
                    UPDATE rides
                    SET
                        driver_lat=%s,
                        driver_lng=%s,
                        driver_location_at=%s
                    WHERE id=%s
                    """,
                    (
                        lat,
                        lng,
                        int(time.time()),
                        ride_id
                    )
                )

            return self.sendj({"ok": True})


        return self.sendj(
            {"error": "Introuvable"},
            404
        )


if __name__ == "__main__":

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL doit être configuré dans Render"
        )

    if not AUTH_SECRET:
        raise RuntimeError(
            "AUTH_SECRET doit être configuré dans Render"
        )

    if PAYTECH_ENV not in ("test", "prod"):
        raise RuntimeError("PAYTECH_ENV doit être 'test' ou 'prod'")

    init()

    ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        App
    ).serve_forever()

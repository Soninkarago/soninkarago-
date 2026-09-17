from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import time
import hmac
import hashlib
import base64
import binascii
import secrets
import mimetypes
import threading
import math
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

APP_VERSION = "2026.09.17-route-check"
MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
DAKAR_BASE_FARE = int(os.environ.get("DAKAR_BASE_FARE", "1000"))
DAKAR_PRICE_PER_KM = int(os.environ.get("DAKAR_PRICE_PER_KM", "220"))
DAKAR_PRICE_PER_MINUTE = int(os.environ.get("DAKAR_PRICE_PER_MINUTE", "20"))
DAKAR_MIN_FARE = int(os.environ.get("DAKAR_MIN_FARE", "1500"))
RATE_LIMITS = defaultdict(deque)
RATE_LIMIT_LOCK = threading.Lock()


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


def dakar_address(query):
    """Resolve a precise point on the Dakar–Thiès corridor."""
    from urllib.parse import urlencode
    address = str(query or "").strip()[:180]
    if len(address) < 4:
        raise ValueError("Indiquez une adresse ou un lieu précis à Dakar ou Thiès.")
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urlencode({
        "address": address + ", Sénégal", "components": "country:SN",
        "key": MAPS_API_KEY, "language": "fr", "region": "sn"
    })
    try:
        with urlopen(url, timeout=10) as response:
            result = json.load(response)
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Recherche d'adresse momentanément indisponible.") from exc
    if result.get("status") not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError("La recherche d'adresse est indisponible. Vérifiez la clé Google Geocoding.")
    if not result.get("results"):
        raise ValueError("Adresse introuvable. Ajoutez le quartier et la ville.")
    for place in result["results"]:
        components = place.get("address_components", [])
        in_zone = any(
            "administrative_area_level_1" in part.get("types", [])
            and part.get("long_name", "").lower() in ("dakar", "thiès", "thies")
            for part in components
        )
        coords = place.get("geometry", {}).get("location", {})
        lat, lng = coords.get("lat", 0), coords.get("lng", 0)
        types = set(place.get("types", []))
        coarse = types.intersection({"locality", "political", "administrative_area_level_1",
                                     "administrative_area_level_2", "country", "postal_code"})
        precise = types.intersection({"street_address", "route", "intersection", "premise",
                                      "subpremise", "establishment", "point_of_interest"})
        if (in_zone and 14.5 <= lat <= 15.1 and -17.6 <= lng <= -16.6
                and not place.get("partial_match")
                and place.get("geometry", {}).get("location_type") != "APPROXIMATE"
                and (precise or not coarse)):
            return {"address": place["formatted_address"][:200], "lat": lat, "lng": lng}
    raise ValueError("Lieu imprécis ou hors de Dakar–Thiès. Ajoutez la rue, le quartier et la ville.")


def dakar_quote(pickup, destination):
    if not MAPS_API_KEY or not AUTH_SECRET:
        raise RuntimeError("Devis Dakar indisponible : configuration des itinéraires nécessaire.")
    origin = dakar_address(pickup)
    arrival = dakar_address(destination)
    payload = json.dumps({
        "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
        "destination": {"location": {"latLng": {"latitude": arrival["lat"], "longitude": arrival["lng"]}}},
        "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE",
        "departureTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 30))
    }).encode()
    req = Request("https://routes.googleapis.com/directions/v2:computeRoutes", payload,
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": MAPS_API_KEY,
                 "X-Goog-FieldMask": "routes.distanceMeters,routes.duration"}, method="POST")
    try:
        with urlopen(req, timeout=12) as response:
            route = json.load(response)["routes"][0]
    except (URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        raise RuntimeError("Impossible de calculer le trajet avec la circulation actuelle.") from exc
    km = int(route["distanceMeters"]) / 1000
    minutes = math.ceil(float(route["duration"].rstrip("s")) / 60)
    if km < .4 or km > 140 or minutes < 1:
        raise ValueError("Vérifiez les lieux de départ et d'arrivée.")
    fare = max(DAKAR_MIN_FARE, DAKAR_BASE_FARE + km * DAKAR_PRICE_PER_KM
               + minutes * DAKAR_PRICE_PER_MINUTE)
    fare = int(math.ceil(fare / 100) * 100)
    quote = {"pickup": origin["address"], "destination": arrival["address"],
             "lat": origin["lat"], "lng": origin["lng"],
             "destination_lat": arrival["lat"], "destination_lng": arrival["lng"],
             "distance_km": round(km, 1), "duration_min": minutes,
             "fare": fare, "exp": int(time.time()) + 300}
    body = b64(json.dumps(quote, separators=(",", ":")).encode())
    signature = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return quote, body + "." + signature


def verify_dakar_quote(token):
    try:
        body, signature = token.split(".")
        expected = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        quote = json.loads(b64decode(body))
        if quote["exp"] < time.time():
            raise ValueError()
        return quote
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        raise ValueError("Le devis a expiré. Recalculez le prix avant de commander.")


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
    # MOTO-TAXI
    "moto_moudery_bondji": {
        "service": "Moto-taxi",
        "pickup": "Moudéry",
        "destination": "Bondji",
        "fare": 2000
    },
    "moto_bondji_moudery": {
        "service": "Moto-taxi",
        "pickup": "Bondji",
        "destination": "Moudéry",
        "fare": 2000
    },
    "moto_moudery_diawara": {
        "service": "Moto-taxi",
        "pickup": "Moudéry",
        "destination": "Diawara",
        "fare": 2000
    },
    "moto_diawara_moudery": {
        "service": "Moto-taxi",
        "pickup": "Diawara",
        "destination": "Moudéry",
        "fare": 2000
    },
    "moto_moudery_bakel": {
        "service": "Moto-taxi",
        "pickup": "Moudéry",
        "destination": "Bakel",
        "fare": 3000
    },
    "moto_bakel_moudery": {
        "service": "Moto-taxi",
        "pickup": "Bakel",
        "destination": "Moudéry",
        "fare": 3000
    },
    "moto_moudery_bakel_rt": {
        "service": "Moto-taxi",
        "pickup": "Moudéry",
        "destination": "Bakel aller-retour",
        "fare": 6000
    },

    # MOTO LOCAL
    "moto_moudery_local": {
        "service": "Moto-taxi",
        "pickup": "Moudéry",
        "destination": "Moudéry - trajet local",
        "fare": 200
    },
    "moto_bondji_local": {
        "service": "Moto-taxi",
        "pickup": "Bondji",
        "destination": "Bondji - trajet local",
        "fare": 200
    },
    "moto_diawara_local": {
        "service": "Moto-taxi",
        "pickup": "Diawara",
        "destination": "Diawara - trajet local",
        "fare": 200
    },
    "moto_bakel_local": {
        "service": "Moto-taxi",
        "pickup": "Bakel",
        "destination": "Bakel - trajet local",
        "fare": 200
    },

    # 3 ROUES
    "tricycle_moudery_local": {
        "service": "3 roues",
        "pickup": "Moudéry",
        "destination": "Moudéry - trajet local",
        "fare": 500
    },

    # VOITURE TAXI
    "car_moudery_bondji": {
        "service": "Voiture taxi",
        "pickup": "Moudéry",
        "destination": "Bondji",
        "fare": 2500
    },
    "car_bondji_moudery": {
        "service": "Voiture taxi",
        "pickup": "Bondji",
        "destination": "Moudéry",
        "fare": 2500
    },
    "car_moudery_diawara": {
        "service": "Voiture taxi",
        "pickup": "Moudéry",
        "destination": "Diawara",
        "fare": 2500
    },
    "car_diawara_moudery": {
        "service": "Voiture taxi",
        "pickup": "Diawara",
        "destination": "Moudéry",
        "fare": 2500
    },
    "car_moudery_bakel_rt": {
        "service": "Voiture taxi",
        "pickup": "Moudéry",
        "destination": "Bakel aller-retour",
        "fare": 20000
    },
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
    },
    # LIVRAISON
    "delivery_moudery_local": {
        "service": "Livraison de matériel",
        "pickup": "Moudéry",
        "destination": "Moudéry - livraison locale",
        "fare": 1000
    }
}


ALLOWED_VILLAGES = [
    "Moudéry",
    "Bondji",
    "Diawara",
    "Bakel",
    "Dakar", "Pikine", "Guédiawaye", "Keur Massar", "Rufisque", "Thiès"
]

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


def read_token(header):
    if not header or not header.startswith("Bearer "):
        return None

    try:
        token = header[7:].strip()

        encoded, signature = token.split(".", 1)

        expected = hmac.new(
            AUTH_SECRET.encode(),
            encoded.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            signature,
            expected
        ):
            return None

        payload = json.loads(
            b64decode(encoded).decode()
        )

        if int(payload.get("exp", 0)) < int(time.time()):
            return None

        return payload

    except Exception:
        return None
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
    """Dakar–Thiès service corridor; the address resolver also checks regions."""
    return 14.5 <= float(lat) <= 15.1 and -17.6 <= float(lng) <= -16.6


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

    eligible = [driver for driver in drivers if driver["id"] not in attempted
                and (ride["route_code"] != "dakar_car" or
                     (in_dakar_zone(driver["latitude"], driver["longitude"])
                      and distance_km(ride["client_lat"], ride["client_lng"],
                                      driver["latitude"], driver["longitude"]) <= 20))]
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
        return (
            self.headers.get("CF-Connecting-IP")
            or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or self.client_address[0]
        )

    def check_rate(self, action, limit, window_seconds, identity=""):
        key = f"{action}:{self.client_ip()}:{identity[:80]}"
        if allow_request(key, limit, window_seconds):
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

    def sendj(self, obj, status=200):
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
        return read_token(
            self.headers.get("Authorization")
        )


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
        if path == "/api/health":
            try:
                with db() as conn:
                    conn.execute("SELECT 1").fetchone()
            except psycopg.Error:
                return self.sendj({
                    "ok": False,
                    "database": "unavailable",
                    "version": APP_VERSION
                }, 503)
            return self.sendj({
                "ok": True,
                "database": "postgresql",
                "paytech": bool(PAYTECH_API_KEY and PAYTECH_API_SECRET),
                "payment_environment": PAYTECH_ENV,
                "version": APP_VERSION
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
                        "SELECT * FROM rides WHERE id=%s",
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

            # Ne renvoyer au client que les informations utiles au suivi.
            # L'utilisation de get() garde cette route compatible avec les
            # anciennes versions de la table PostgreSQL.
            return self.sendj({
                "id": row.get("id", ride_id),
                "pickup": row.get("pickup", ""),
                "destination": row.get("destination", ""),
                "vehicle": row.get("vehicle", ""),
                "payment": row.get("payment", ""),
                "fare": row.get("fare", 0),
                "status": row.get("status", "searching"),
                "driver_name": row.get("driver_name", "")
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
                        created_at
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
                        "SELECT * FROM rides WHERE id=%s",
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
        if path == "/api/dakar/quote":
            if not self.check_rate("dakar-quote", 20, 3600):
                return
            try:
                quote, token = dakar_quote(data.get("pickup"), data.get("destination"))
                with db() as conn:
                    available = conn.execute("""
                        SELECT latitude, longitude FROM drivers
                        WHERE status='approved' AND online=TRUE AND vehicle='Voiture taxi'
                          AND latitude IS NOT NULL AND longitude IS NOT NULL
                          AND last_location_at >= %s
                          AND balance >= %s
                          AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.driver_id=drivers.id AND r.status='accepted')
                    """, (int(time.time()) - 300, (quote["fare"] + 9) // 10)).fetchall()
                available = sum(in_dakar_zone(d["latitude"], d["longitude"])
                                and distance_km(quote["lat"], quote["lng"],
                                                d["latitude"], d["longitude"]) <= 20
                                for d in available)
                return self.sendj({**quote, "quote_token": token,
                                   "drivers_online": available})
            except ValueError as exc:
                return self.sendj({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self.sendj({"error": str(exc)}, 503)
        if path == "/api/paytech/ipn":
            if not PAYTECH_API_KEY or not PAYTECH_API_SECRET:
                return self.sendj({"error": "PayTech non configuré"}, 503)

            ref_command = str(data.get("ref_command", "")).strip()
            item_price = str(data.get("item_price", "")).strip()
            received_hmac = str(data.get("hmac_compute", "")).strip().lower()
            message = f"{item_price}|{ref_command}|{PAYTECH_API_KEY}"
            expected_hmac = hmac.new(
                PAYTECH_API_SECRET.encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()

            if not received_hmac or not hmac.compare_digest(received_hmac, expected_hmac):
                return self.sendj({"error": "Signature IPN invalide"}, 403)

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
                        paid_amount = int(float(item_price))
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
                    "SELECT id, fare, deposit_amount FROM rides WHERE id=%s FOR UPDATE",
                    (ref_command,)
                ).fetchone()

                if not ride:
                    return self.sendj({"error": "Réservation introuvable"}, 404)

                try:
                    paid_amount = int(float(item_price))
                except (TypeError, ValueError):
                    return self.sendj({"error": "Montant invalide"}, 400)

                expected_amount = int(ride["deposit_amount"] or ride["fare"])
                if paid_amount != expected_amount:
                    return self.sendj({"error": "Montant incorrect"}, 409)

                if event == "sale_complete":
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


            if village not in ALLOWED_VILLAGES:
                return self.sendj(
                    {
                        "error":
                        "Village invalide"
                    },
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
                            created_at
                        )
                        VALUES(
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
                            int(time.time())
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


            return self.sendj({
                "token":
                    make_token(
                        "driver",
                        driver["name"],
                        driver["id"]
                    ),

                "name":
                    driver["name"],

                "vehicle":
                    driver["vehicle"],

                "village":
                    driver["village"]
            })


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
                return self.sendj(
                    {
                        "error":
                        "Mot de passe incorrect"
                    },
                    401
                )

            return self.sendj({
                "token":
                    make_token(
                        "admin",
                        "Admin"
                    )
            })


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
                cur = conn.execute(
                    """
                    UPDATE drivers
                    SET status='approved'
                    WHERE id=%s
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

            with db() as conn:
                cur = conn.execute(
                    """
                    UPDATE drivers
                    SET status='rejected'
                    WHERE id=%s
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
            dakar_ride = route_code == "dakar_car"
            if dakar_ride:
                try:
                    quote = verify_dakar_quote(str(data.get("quote_token", "")))
                except ValueError as exc:
                    return self.sendj({"error": str(exc)}, 400)
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
            if dakar_ride:
                # Le départ du trajet, pas le téléphone du réservant, détermine l'attribution.
                client_coords = (quote["lat"], quote["lng"])
            if not is_minicar and not client_coords:
                return self.sendj(
                    {"error": "Activez votre position GPS pour trouver le chauffeur le plus proche"},
                    400
                )
            client_lat, client_lng = client_coords or (None, None)

            if dakar_ride:
                with db() as conn:
                    nearby = conn.execute("""
                        SELECT latitude, longitude FROM drivers
                        WHERE status='approved' AND online=TRUE AND vehicle='Voiture taxi'
                          AND latitude IS NOT NULL AND longitude IS NOT NULL
                          AND last_location_at >= %s AND balance >= %s
                          AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.driver_id=drivers.id AND r.status='accepted')
                    """, (int(time.time()) - 300, fee)).fetchall()
                if not any(in_dakar_zone(d["latitude"], d["longitude"])
                           and distance_km(client_lat, client_lng, d["latitude"], d["longitude"]) <= 20
                           for d in nearby):
                    return self.sendj({"error": "Aucun chauffeur voiture disponible près du départ. Aucun paiement demandé."}, 409)

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
                        commission_charged
                    )
                    VALUES(
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s
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
                        "awaiting_payment" if mobile_payment else "searching",
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
                        "unpaid",
                        deposit_amount,
                        balance_due,
                        False
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
                    "status": "awaiting_payment" if mobile_payment else "searching",
                    "payment_status": "unpaid",
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

    init()

    ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        App
    ).serve_forever()

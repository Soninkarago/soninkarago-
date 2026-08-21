from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
import json
import os
import time
import hmac
import hashlib
import base64
import secrets
import mimetypes
import psycopg
from psycopg.rows import dict_row


ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "index.html")
PORT = int(os.environ.get("PORT", "10000"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


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
    "Bakel"
]

ALLOWED_VEHICLES = [
    "Moto-taxi",
    "3 roues",
    "Voiture taxi"
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


def hash_pin(pin, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode(),
        salt.encode(),
        150000
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

class App(SimpleHTTPRequestHandler):

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

        self.send_header(
            "X-Content-Type-Options",
            "nosniff"
        )

        self.send_header(
            "X-Frame-Options",
            "DENY"
        )

        self.end_headers()

        self.wfile.write(body)


    def body(self):
        try:
            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

            if length > 20000:
                return {}

            if not length:
                return {}

            return json.loads(
                self.rfile.read(length).decode()
            )

        except Exception:
            return {}


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

            self.send_header(
                "X-Content-Type-Options",
                "nosniff"
            )

            self.send_header(
                "X-Frame-Options",
                "DENY"
            )

            self.end_headers()

            self.wfile.write(body)

        except Exception:
            self.send_error(500)


    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self.serve_index()
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")):
            file_path = os.path.join(ROOT, path.lstrip("/"))

            if not os.path.isfile(file_path):
                return self.sendj({"error": "Introuvable"}, 404)

            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = "application/octet-stream"

            with open(file_path, "rb") as f:
                data = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/health":
            return self.sendj({
                "ok": True,
                "database": "postgresql"
            })

        # Client : suivi de sa course
        if (
            path.startswith("/api/rides/")
            and path.count("/") == 3
        ):
            ride_id = path.split("/")[3]

            with db() as conn:
                row = conn.execute(
                    """
                    SELECT
                        id,
                        pickup,
                        destination,
                        vehicle,
                        payment,
                        fare,
                        status,
                        driver_name
                    FROM rides
                    WHERE id=%s
                    """,
                    (ride_id,)
                ).fetchone()

            if not row:
                return self.sendj(
                    {"error": "Course introuvable"},
                    404
                )

            return self.sendj(row)


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
                            created_at
                        FROM rides
                        WHERE
                            status='searching'
                            OR (
                                status='accepted'
                                AND driver_name=%s
                            )
                        ORDER BY created_at DESC
                        """,
                        (
                            user.get(
                                "name",
                                ""
                            ),
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
                        COALESCE(SUM(fee),0) AS fees
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

            with db() as conn:
                ride = conn.execute(
                    """
                    SELECT
                        status,
                        driver_name,
                        driver_lat,
                        driver_lng,
                        driver_location_at,
                        tracking_token
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

            if not hmac.compare_digest(
                str(ride.get("tracking_token") or ""),
                token
            ):
                return self.sendj(
                    {"error": "Non autorisé"},
                    401
                )

            return self.sendj({
                "status": ride["status"],
                "driver_name": ride["driver_name"],
                "driver_lat": ride["driver_lat"],
                "driver_lng": ride["driver_lng"],
                "updated_at": ride["driver_location_at"]
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

            return self.sendj({
                "ok": True,
                "message": "Demande de paiement créée. Connexion Wave / Orange Money à terminer.",
                "amount": amount,
                "payment": payment
            })

        # INSCRIPTION CHAUFFEUR
        if path == "/api/register/driver":

            name = str(
                data.get("name", "")
            ).strip()

            phone = str(
                data.get("phone", "")
            ).strip()

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


            if len(phone) < 8:
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
                            phone[:30],
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

            phone = str(
                data.get("phone", "")
            ).strip()

            pin = str(
                data.get("pin", "")
            ).strip()


            with db() as conn:
                driver = conn.execute(
                    """
                    SELECT *
                    FROM drivers
                    WHERE phone=%s
                    """,
                    (phone,)
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


        # CONNEXION ADMIN
        if path == "/api/login/admin":

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

            route_code = str(
                data.get("route_code", "")
            ).strip()

            route = ROUTES.get(route_code)

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

            phone = str(
                data.get("phone", "")
            ).strip()[:30]

            payment = str(
                data.get("payment", "Espèces")
            ).strip()[:30]

            luggage = str(
                data.get("luggage", "")
            ).strip()[:30]

            booking_note = str(
                data.get("booking_note", "")
            ).strip()[:300]

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
                        "searching",
                        "",
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

            return self.sendj(
                {
                    "id": ride_id,
                    "pickup": route["pickup"],
                    "destination": route["destination"],
                    "vehicle": route["service"],
                    "fare": fare,
                    "fee": fee,
                    "status": "searching",
                    "payment_status": "unpaid",
                    "deposit_amount": deposit_amount,
                    "balance_due": balance_due,
                    "tracking_token": tracking_token
                },
                201
            )  


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
                    SELECT fare, fee, vehicle
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

                driver = conn.execute(
                    """
                    SELECT balance
                    FROM drivers
                    WHERE id=%s
                    FOR UPDATE
                    """,
                    (driver_id,)
                ).fetchone()

                balance = int(
                    driver["balance"] or 0
                ) if driver else 0

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
                        commission_charged=%s
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
                    SELECT status, tracking_token
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

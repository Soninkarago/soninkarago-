from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
import json
import os
import time
import hmac
import hashlib
import base64
import secrets

import psycopg
from psycopg.rows import dict_row


ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "index.html")
PORT = int(os.environ.get("PORT", "10000"))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

try:
    DRIVERS = json.loads(os.environ.get("DRIVERS_JSON", "[]"))
except Exception:
    DRIVERS = []


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

    # TRAJETS LOCAUX MOTO
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

    # LIVRAISON
    "delivery_moudery_local": {
        "service": "Livraison de matériel",
        "pickup": "Moudéry",
        "destination": "Moudéry - livraison locale",
        "fare": 1000
    }
}


def db():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def init():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
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


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64decode(data):
    data += "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data.encode())


def make_token(role, name=""):
    payload = {
        "role": role,
        "name": name,
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

        if not hmac.compare_digest(signature, expected):
            return None

        payload = json.loads(
            b64decode(encoded).decode()
        )

        if int(payload.get("exp", 0)) < int(time.time()):
            return None

        return payload

    except Exception:
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

            if length > 10000:
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

        if path == "/api/health":
            return self.sendj({
                "ok": True,
                "database": "postgresql"
            })

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
                        COALESCE(
                            SUM(fare),
                            0
                        ) AS volume,
                        COALESCE(
                            SUM(fee),
                            0
                        ) AS fees
                    FROM rides
                """).fetchone()

            return self.sendj(row)

        return self.sendj(
            {"error": "Introuvable"},
            404
        )


    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()

        if path == "/api/login/driver":

            phone = str(
                data.get("phone", "")
            ).strip()

            pin = str(
                data.get("pin", "")
            ).strip()

            driver = None

            for d in DRIVERS:

                if (
                    hmac.compare_digest(
                        str(d.get("phone", "")),
                        phone
                    )
                    and hmac.compare_digest(
                        str(d.get("pin", "")),
                        pin
                    )
                ):
                    driver = d
                    break

            if not driver:

                return self.sendj(
                    {
                        "error":
                        "Téléphone ou code PIN incorrect"
                    },
                    401
                )

            name = str(
                driver.get(
                    "name",
                    "Chauffeur"
                )
            )

            return self.sendj({
                "token":
                    make_token(
                        "driver",
                        name
                    ),
                "name":
                    name
            })

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

        if path == "/api/rides":

            route_code = str(
                data.get(
                    "route_code",
                    ""
                )
            ).strip()

            route = ROUTES.get(route_code)

            if not route:

                return self.sendj(
                    {
                        "error":
                        "Ce trajet n'est pas disponible"
                    },
                    400
                )

            fare = int(route["fare"])
            fee = round(fare * 0.10)

            ride_id = (
                "SG-"
                + secrets.token_hex(6).upper()
            )

            client_name = str(
                data.get(
                    "client_name",
                    "Client"
                )
            ).strip()[:80]

            phone = str(
                data.get(
                    "phone",
                    ""
                )
            ).strip()[:30]

            payment = str(
                data.get(
                    "payment",
                    "Espèces"
                )
            ).strip()[:30]

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
                        created_at
                    )
                    VALUES(
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
                        int(time.time())
                    )
                )

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

            return self.sendj(
                row,
                201
            )

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
                    SET
                        status='accepted',
                        driver_name=%s
                    WHERE
                        id=%s
                        AND status='searching'
                    """,
                    (
                        user.get(
                            "name",
                            "Chauffeur"
                        ),
                        ride_id
                    )
                )

                changed = cur.rowcount

            if not changed:

                return self.sendj(
                    {
                        "error":
                        "Course déjà prise ou introuvable"
                    },
                    409
                )

            return self.sendj({"ok": True})

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
                    WHERE
                        id=%s
                        AND status='accepted'
                        AND driver_name=%s
                    """,
                    (
                        ride_id,
                        user.get(
                            "name",
                            ""
                        )
                    )
                )

                changed = cur.rowcount

            if not changed:

                return self.sendj(
                    {
                        "error":
                        "Cette course ne vous appartient pas"
                    },
                    403
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

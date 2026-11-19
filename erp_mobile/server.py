"""HTTP-Server mit JSON-API für die Smartphone-Oberfläche.

Bewusst nur mit der Python-Standardbibliothek umgesetzt (kein Web-Framework,
kein Qt): ``ThreadingHTTPServer`` bedient mehrere Prüfer parallel, die
Geschäftslogik kommt unverändert aus ``erp.services.inspection``.

Anmeldung: ERP-Benutzer mit Rolle *Prüfer (DGUV V3)* oder *Administrator*.
Das Anmelde-Token ist mit einem in der Datenbank abgelegten Schlüssel signiert,
übersteht also Neustarts des Servers und funktioniert auch, wenn mehrere
Server-Instanzen dieselbe Datenbank nutzen.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import ssl
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from erp import db, models as m
from erp.services import BusinessError, ConflictError, auth, context, get_setting, set_setting
from erp.services import inspection as ins

from . import __version__

TOKEN_DAYS = 14
LOGIN_DELAY = 0.3          # Sekunden; bremst automatisiertes Passwortraten
MAX_BODY = 64 * 1024
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/app.css": "app.css",
    "/sw.js": "sw.js",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/icon.svg": "icon.svg",
}
CONTENT_TYPES = {".webmanifest": "application/manifest+json", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
                 ".svg": "image/svg+xml"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Anmelde-Token
# --------------------------------------------------------------------------

_secret_lock = threading.Lock()
_secret: bytes | None = None


def _server_secret() -> bytes:
    global _secret
    with _secret_lock:
        if _secret is None:
            with db.session_scope() as s:
                value = get_setting(s, "mobile_token_secret")
                if not value:
                    value = secrets.token_hex(32)
                    set_setting(s, "mobile_token_secret", value)
            _secret = bytes.fromhex(value)
        return _secret


def make_token(user: m.User) -> str:
    expires = int(time.time()) + TOKEN_DAYS * 86400
    # Passwort-Hash fließt ein: Passwortänderung macht alte Tokens ungültig
    payload = f"{user.id}.{expires}"
    sig = hmac.new(_server_secret(), f"{payload}.{user.password_hash}".encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def check_token(s, token: str) -> m.User:
    try:
        uid, expires, sig = token.split(".")
        uid_i, exp_i = int(uid), int(expires)
    except ValueError:
        raise ApiError(401, "Bitte neu anmelden") from None
    user = s.get(m.User, uid_i)
    if user is None or not user.active or exp_i < time.time():
        raise ApiError(401, "Anmeldung abgelaufen – bitte neu anmelden")
    expected = hmac.new(_server_secret(), f"{uid}.{expires}.{user.password_hash}".encode(),
                        hashlib.sha256).digest()
    if not hmac.compare_digest(base64.urlsafe_b64encode(expected).decode().rstrip("="), sig):
        raise ApiError(401, "Bitte neu anmelden")
    if not user.may("inspection"):
        raise ApiError(403, "Keine Berechtigung für die Prüfplanung")
    return user


# --------------------------------------------------------------------------
# Datenaufbereitung
# --------------------------------------------------------------------------

def _iso(value):
    return value.isoformat() if value else None


def user_json(u: m.User) -> dict:
    return {"id": u.id, "username": u.username, "name": u.full_name or u.username, "role": u.role}


def area_json(a: m.InspectionArea) -> dict:
    return {"id": a.id, "version": a.version, "plan_id": a.floor_plan_id,
            "plan": a.floor_plan.name if a.floor_plan else "",
            "name": a.name, "x": a.x, "y": a.y, "w": a.w, "h": a.h,
            "status": a.status, "progress": a.progress_pct, "date": _iso(a.appointment_date),
            "note": a.status_note, "notes": a.notes, "devices": a.expected_devices,
            "inspector_id": a.inspector_id,
            "inspector": (a.inspector.full_name or a.inspector.username) if a.inspector else "",
            "status_by": a.status_by, "status_at": _iso(a.status_at), "overdue": a.overdue}


def plan_json(p: m.FloorPlan, area_list: list | None = None) -> dict:
    data = {"id": p.id, "version": p.version, "name": p.name, "location": p.location,
            "w": p.width_m, "h": p.height_m, "notes": p.notes, "has_background": p.has_background}
    if area_list is not None:
        summ = ins.summary(area_list)
        data["summary"] = {"total": summ["total"], "progress": summ["progress"],
                           "counts": summ["counts"], "overdue": summ["overdue"]}
    return data


def _parse_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ApiError(400, "Ungültiges Datum") from None


def _int(value, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Ungültige Zahl") from None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def api_login(s, body, _user, _q):
    time.sleep(LOGIN_DELAY)
    try:
        user = auth.authenticate(s, str(body.get("username", "")), str(body.get("password", "")))
    except BusinessError:
        time.sleep(LOGIN_DELAY * 3)
        raise ApiError(401, "Benutzername oder Passwort falsch") from None
    if not user.may("inspection"):
        raise ApiError(403, "Dieser Benutzer hat keine Berechtigung für die Prüfplanung")
    return {"token": make_token(user), "user": user_json(user)}


def api_me(s, _b, user, _q):
    return {"user": user_json(user), "server_version": __version__,
            "company": get_setting(s, "company_name"),
            "statuses": [{"key": k, "label": v, "fill": m.AREA_COLORS[k][0], "border": m.AREA_COLORS[k][1]}
                         for k, v in m.AREA_STATUS.items()]}


def api_plans(s, _b, _user, _q):
    all_areas = ins.areas(s)
    by_plan: dict[int, list] = {}
    for a in all_areas:
        by_plan.setdefault(a.floor_plan_id, []).append(a)
    return {"plans": [plan_json(p, by_plan.get(p.id, [])) for p in ins.floor_plans(s)]}


def api_plan(s, _b, _user, _q, plan_id):
    plan = s.get(m.FloorPlan, plan_id)
    if plan is None or not plan.active:
        raise ApiError(404, "Grundriss nicht gefunden")
    area_list = ins.areas(s, plan_id)
    return {"plan": plan_json(plan, area_list), "areas": [area_json(a) for a in area_list]}


def api_areas(s, _b, user, q):
    who = q.get("inspector")
    inspector_id = user.id if who == "me" else (-1 if who == "none" else _int(who))
    status = q.get("status") or None
    if status and status not in m.AREA_STATUS:
        raise ApiError(400, "Unbekannter Status")
    area_list = ins.areas(s, _int(q.get("plan")), inspector_id, status, q.get("q", ""))
    if status == "appointment":
        area_list.sort(key=lambda a: a.appointment_date or dt.date.max)
    return {"areas": [area_json(a) for a in area_list]}


def api_history(s, _b, _user, _q, area_id):
    return {"history": [{"timestamp": _iso(h.timestamp), "username": h.username, "status": h.status,
                         "progress": h.progress_pct, "date": _iso(h.appointment_date), "note": h.note}
                        for h in ins.history(s, area_id)]}


def api_status(s, body, _user, _q, area_id):
    area = ins.set_status(s, area_id, str(body.get("status", "")),
                          date=_parse_date(body.get("date")), note=str(body.get("note") or ""),
                          progress=_int(body.get("progress")), version=_int(body.get("version")))
    return {"area": area_json(area)}


def api_claim(s, body, _user, _q, area_id):
    return {"area": area_json(ins.claim(s, area_id, _int(body.get("version"))))}


ROUTES = [
    ("POST", re.compile(r"^/api/login$"), api_login, False),
    ("GET", re.compile(r"^/api/me$"), api_me, True),
    ("GET", re.compile(r"^/api/plans$"), api_plans, True),
    ("GET", re.compile(r"^/api/plans/(\d+)$"), api_plan, True),
    ("GET", re.compile(r"^/api/areas$"), api_areas, True),
    ("GET", re.compile(r"^/api/areas/(\d+)/history$"), api_history, True),
    ("POST", re.compile(r"^/api/areas/(\d+)/status$"), api_status, True),
    ("POST", re.compile(r"^/api/areas/(\d+)/claim$"), api_claim, True),
]
BACKGROUND = re.compile(r"^/api/plans/(\d+)/background$")


def _image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = f"PruefplanungMobil/{__version__}"
    protocol_version = "HTTP/1.1"
    quiet = False

    # -- Hilfen ------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: dict):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Anfrage zu groß")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Ungültige Anfrage") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Ungültige Anfrage")
        return data

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    # -- Verteilung ----------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str):
        url = urlparse(self.path)
        path = url.path
        try:
            if method == "GET" and path in STATIC_FILES:
                return self._static(STATIC_FILES[path])
            if method == "GET" and (mt := BACKGROUND.match(path)):
                return self._background(int(mt.group(1)))
            for meth, pattern, fn, needs_auth in ROUTES:
                mt = pattern.match(path)
                if mt and meth == method:
                    return self._api(fn, needs_auth, [int(g) for g in mt.groups()],
                                     {k: v[0] for k, v in parse_qs(url.query).items()})
            raise ApiError(404, "Nicht gefunden")
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})

    def _api(self, fn, needs_auth: bool, args: list, query: dict):
        body = self._body() if self.command == "POST" else {}
        try:
            with db.session_scope() as s:
                user = None
                if needs_auth:
                    user = check_token(s, self._token())
                    context.username, context.role, context.user_id = user.username, user.role, user.id
                result = fn(s, body, user, query, *args)
            self._json(200, result)
        except ApiError:
            raise
        except ConflictError as exc:
            self._json(409, {"error": str(exc), "conflict": True})
        except StaleDataError:
            self._json(409, {"error": "Der Bereich wurde gerade von einem anderen Prüfer geändert.",
                             "conflict": True})
        except BusinessError as exc:
            self._json(400, {"error": str(exc)})
        except OperationalError:
            self._json(503, {"error": "Datenbank nicht erreichbar"})
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": "Interner Fehler"})
        finally:
            context.username, context.role, context.user_id = "system", "admin", None

    def _token(self) -> str:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ApiError(401, "Bitte anmelden")
        return header[7:].strip()

    def _background(self, plan_id: int):
        with db.session_scope() as s:
            check_token(s, self._token())
            plan = s.get(m.FloorPlan, plan_id)
            data = ins.background(s, plan_id) if plan and plan.has_background else None
            version = plan.version if plan else 0
        if not data:
            raise ApiError(404, "Kein Hintergrundbild")
        etag = f'"bg-{plan_id}-{version}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, data, _image_type(data), {"ETag": etag, "Cache-Control": "private, max-age=0"})

    def _static(self, name: str):
        data = resources.files("erp_mobile").joinpath("static", name).read_bytes()
        ext = "." + name.rsplit(".", 1)[-1]
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        headers = {"Cache-Control": "no-cache"}
        if name == "index.html":
            headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; "
                "connect-src 'self'; manifest-src 'self'; worker-src 'self'")
        if name == "sw.js":
            headers["Service-Worker-Allowed"] = "/"
        self._send(200, data, ctype, headers)


def make_server(host: str = "0.0.0.0", port: int = 8080, certfile: str | None = None,
                keyfile: str | None = None, quiet: bool = False) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"quiet": quiet})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    if certfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd

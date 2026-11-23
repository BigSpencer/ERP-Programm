"""Web-Version des ERP (Browser statt Desktop-Client).

Start lokal:   ERP_INSECURE_COOKIES=1 uvicorn erp.web.app:app --reload
Start Render:  uvicorn erp.web.app:app --host 0.0.0.0 --port $PORT

Konfiguration über Umgebungsvariablen:
  ERP_DATABASE_URL     Datenbank, z. B. die "Internal Database URL" von Render (postgresql://…).
                       Ohne Angabe: lokale SQLite-Datei (nur zum Ausprobieren).
  ERP_SECRET_KEY       Langer Zufallswert zum Signieren der Anmelde-Cookies (Pflicht im Betrieb).
  ERP_ADMIN_PASSWORD   Optional: Startpasswort für den Benutzer "admin" einer neuen Datenbank.
  ERP_INSECURE_COOKIES Nur lokal ohne HTTPS auf 1 setzen.

Die Oberfläche lädt den Datenbestand über /api/state und ändert ihn ausschließlich
über /api/action/<name>. Alle Regeln (Status, Buchungen, Storno …) liegen in der
gemeinsamen Service-Schicht und gelten damit identisch für Desktop und Web.
"""
from __future__ import annotations

import datetime as dt
import base64
import hashlib
import logging
import os
import secrets
import json
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__, db, models as m
from . import beta
from ..services import BusinessError, auth, context, dealer, maintenance, get_setting, masterdata, set_setting, set_user

log = logging.getLogger("erp.web")
STATIC = Path(__file__).parent / "static"
DEFAULT_PASSWORD = "admin"

# Interne Schlüssel <-> Schlüssel der Oberfläche
V_STATUS = {"stock": "lager", "showroom": "ausstellung", "reserved": "reserviert", "sold": "verkauft",
            "removed": "ausgebucht", "pending": "inzahlung"}
P_STATUS = {"done": "angekauft", "trade_in": "inzahlungnahme", "settled": "verrechnet", "cancelled": "storniert"}
P_STATUS_FROM_WEB = {v: k for k, v in P_STATUS.items()}
O_STATUS = {"ordered": "bestellt", "arrived": "eingetroffen", "delivered": "ausgeliefert", "cancelled": "storniert"}
O_STATUS_FROM_WEB = {v: k for k, v in O_STATUS.items()}
OF_STATUS = {"open": "offen", "declined": "abgelehnt", "expired": "abgelaufen", "ordered": "bestellt"}
OF_STATUS_FROM_WEB = {v: k for k, v in OF_STATUS.items()}
D_STATUS_FROM_WEB = {"ok": "done", "storno": "cancelled"}
D_STATUS = {"done": "ok", "cancelled": "storno"}
PAY_TO_WEB = {"1200": "bank", "1000": "bar"}
PAY_FROM_WEB = {v: k for k, v in PAY_TO_WEB.items()}
COND_FROM_WEB = {"neu": "new", "gebraucht": "used"}

# Ansicht der Oberfläche -> Modul für die Rechteprüfung
VIEW_MODULES = {
    "cockpit": "cockpit", "bestand": "vehicles", "ausstellung": "showroom", "bestellungen": "vehicle_orders",
    "angebote": "offers", "verkauf": "vehicle_sales", "ankauf": "used_purchases", "kunden": "customers", "katalog": "vehicle_models",
    "mitarbeiter": "employees", "einstellungen": "admin", "benutzer": "admin",
}


def database_url() -> str:
    url = os.environ.get("ERP_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        data = Path(os.environ.get("ERP_DATA_DIR", "."))
        return f"sqlite:///{data / 'erp-web.sqlite'}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


# --------------------------------------------------------------------------
# Umwandlung
# --------------------------------------------------------------------------

def _s(v) -> str | None:
    return None if v is None else str(v)


def _f(v) -> float:
    return float(v or 0)


def _date(v) -> str | None:
    return v.isoformat() if v else None


def _id(v) -> int | None:
    if v in (None, "", "null"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise BusinessError("Ungültige Auswahl") from exc


def _pdate(v) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise BusinessError(f"Ungültiges Datum: {v}") from exc


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def _pay(v) -> str:
    return PAY_FROM_WEB.get(v or "bank", "1200")


def state(s, user: m.User) -> dict:
    """Kompletter Datenbestand in dem Format, das die Oberfläche erwartet."""
    vehicles = s.scalars(select(m.Vehicle)).all()
    data = {
        "version": f"{__version__} · {asset_version()[:7]}",
        "beta": beta.info(),
        "me": me(s, user),
        "settings": {"company": get_setting(s, "company_name"), "seller": user.full_name or user.username,
                     "logoVersion": logo_version(s), "customLogo": bool(get_setting(s, "logo_data"))},
        "models": [{"id": _s(x.id), "nr": x.number, "make": x.make, "model": x.model, "cat": x.category,
                    "ek": _f(x.purchase_price), "vk": _f(x.sales_price), "active": x.active}
                   for x in s.scalars(select(m.VehicleModel))],
        "vehicles": [{"id": _s(v.id), "nr": v.number, "modelId": _s(v.model_id), "make": v.make, "model": v.model,
                      "cat": v.category, "cond": "neu" if v.condition == "new" else "gebraucht",
                      "status": V_STATUS.get(v.status, v.status), "source": v.source, "vin": v.vin,
                      "plate": v.plate, "color": v.color, "year": v.year, "km": v.mileage,
                      "ek": _f(v.purchase_price), "vk": _f(v.list_price), "inDate": _date(v.purchase_date),
                      "loc": v.location, "showSince": _date(v.showroom_since),
                      "resFor": _s(v.reserved_for_id), "resUntil": _date(v.reserved_until), "notes": v.notes}
                     for v in vehicles],
        "orders": [{"id": _s(o.id), "nr": o.number, "modelId": _s(o.model_id), "customerId": _s(o.customer_id),
                    "color": o.color, "ek": _f(o.purchase_price), "vk": _f(o.sales_price), "deposit": _f(o.deposit),
                    "date": _date(o.order_date), "expected": _date(o.expected_date), "arrival": _date(o.arrival_date),
                    "status": O_STATUS.get(o.status, o.status), "vehicleId": _s(o.vehicle_id), "notes": o.notes,
                    "createdBy": o.created_by}
                   for o in s.scalars(select(m.VehicleOrder))],
        "sales": [{"id": _s(x.id), "nr": x.number, "vehicleId": _s(x.vehicle_id), "customerId": _s(x.customer_id),
                   "date": _date(x.sale_date), "price": _f(x.price), "ek": _f(x.purchase_price),
                   "tradeId": _s(x.trade_in_vehicle_id), "tradeValue": _f(x.trade_in_value),
                   "pay": PAY_TO_WEB.get(x.payment_account, "bank"), "status": D_STATUS.get(x.status, x.status),
                   "seller": x.salesperson, "notes": x.notes, "orderId": _s(x.order_id), "deposit": _f(x.deposit)}
                  for x in s.scalars(select(m.VehicleSale))],
        "offers": [{"id": _s(x.id), "nr": x.number, "customerId": _s(x.customer_id), "modelId": _s(x.model_id),
                    "vehicleId": _s(x.vehicle_id), "color": x.color, "price": _f(x.price), "date": _date(x.offer_date),
                    "validUntil": _date(x.valid_until), "status": OF_STATUS.get(x.status, x.status),
                    "orderId": _s(x.order_id), "notes": x.notes, "createdBy": x.created_by}
                   for x in s.scalars(select(m.Offer))],
        "purchases": [{"id": _s(x.id), "nr": x.number, "vehicleId": _s(x.vehicle_id), "customerId": _s(x.customer_id),
                       "date": _date(x.purchase_date), "price": _f(x.price),
                       "pay": PAY_TO_WEB.get(x.payment_account, "bank"), "status": P_STATUS.get(x.status, x.status),
                       "buyer": x.buyer, "notes": x.notes, "saleId": _s(x.sale_id)}
                      for x in s.scalars(select(m.UsedPurchase))],
        "customers": [{"id": _s(c.id), "nr": c.number, "name": c.name, "phone": c.phone, "email": c.email,
                       "notes": c.notes, "active": c.active}
                      for c in s.scalars(select(m.Customer))],
    }
    mods = auth.modules_for(s, user)
    roles = s.scalars(select(m.Role).order_by(m.Role.builtin.desc(), m.Role.name)).all()
    data["roleNames"] = {r.key: r.name for r in roles}
    if mods & {"admin", "employees"}:
        users = s.scalars(select(m.User).order_by(m.User.username)).all()
        data["users"] = [{"id": _s(u.id), "username": u.username, "fullName": u.full_name, "roles": u.role_keys,
                          "admin": u.is_admin, "active": u.active} for u in users]
        data["roles"] = [{"id": _s(r.id), "key": r.key, "name": r.name, "modules": r.module_list, "builtin": r.builtin,
                          "users": sum(1 for u in users if r.key in u.role_keys)} for r in roles]
        data["modules"] = m.WEB_MODULES
    return data


def me(s, user: m.User) -> dict:
    mods, names = auth.modules_for(s, user), auth.role_names(s)
    return {"username": user.username, "fullName": user.full_name, "role": "admin" if user.is_admin else user.role,
            "roleName": " · ".join(names.get(k, k) for k in user.role_keys),
            "views": [v for v, mod in VIEW_MODULES.items() if mod in mods],
            "mustChangePassword": auth.verify_password(DEFAULT_PASSWORD, user.password_hash)}


# --------------------------------------------------------------------------
# Aktionen (Name -> Modul für Rechteprüfung, Funktion)
# --------------------------------------------------------------------------

def _vehicle_data(b: dict, edit: bool) -> dict:
    d = {"model_id": _id(b.get("modelId")), "make": _str(b.get("make")), "model": _str(b.get("model")),
         "category": _str(b.get("cat")), "condition": COND_FROM_WEB.get(b.get("cond") or "neu", "new"),
         "vin": _str(b.get("vin")), "plate": _str(b.get("plate")), "color": _str(b.get("color")),
         "year": b.get("year") or 0, "mileage": b.get("km") or 0, "purchase_date": _pdate(b.get("inDate")),
         "location": _str(b.get("loc")), "notes": _str(b.get("notes"))}
    for web, key in (("ek", "purchase_price"), ("vk", "list_price")):
        val = b.get(web)
        if val not in (None, ""):
            d[key] = val
        elif not edit:
            d[key] = ""          # leer = Katalogpreis
    return d


def a_save_vehicle(s, b):
    vid = _id(b.get("id"))
    v = dealer.save_vehicle(s, vid, _vehicle_data(b, bool(vid)))
    return {"id": _s(v.id), "message": f"Fahrzeug {v.number} gespeichert"}


def a_showroom(s, b):
    dealer.to_showroom(s, _id(b["id"]), _str(b.get("loc")))
    return {"message": "Fahrzeug ausgestellt"}


def a_showroom_out(s, b):
    dealer.from_showroom(s, _id(b["id"]), _str(b.get("loc")))
    return {"message": "Fahrzeug aus der Ausstellung genommen"}


def a_reserve(s, b):
    cid = _id(b.get("customerId"))
    if not cid:
        raise BusinessError("Bitte einen Kunden wählen")
    dealer.reserve(s, _id(b["id"]), cid, _pdate(b.get("until")))
    return {"message": "Fahrzeug reserviert"}


def a_unreserve(s, b):
    dealer.unreserve(s, _id(b["id"]))
    return {"message": "Reservierung aufgehoben"}


def a_remove(s, b):
    if not _str(b.get("reason")):
        raise BusinessError("Bitte einen Grund angeben")
    dealer.remove_vehicle(s, _id(b["id"]), _str(b.get("reason")))
    return {"message": "Fahrzeug ausgebucht"}


def a_save_order(s, b):
    oid = _id(b.get("id"))
    o = dealer.save_order(s, oid, {
        "model_id": _id(b.get("modelId")), "customer_id": _id(b.get("customerId")), "color": _str(b.get("color")),
        "purchase_price": b.get("ek") or "", "sales_price": b.get("vk") or "", "deposit": b.get("deposit") or "",
        "order_date": _pdate(b.get("date")), "expected_date": _pdate(b.get("expected")), "notes": _str(b.get("notes")),
    })
    return {"id": _s(o.id), "message": f"Bestellung {o.number} gespeichert"}


def a_receive_order(s, b):
    v = dealer.receive_order(s, _id(b["id"]), {
        "vin": _str(b.get("vin")), "plate": _str(b.get("plate")), "year": b.get("year") or 0,
        "mileage": b.get("km") or 0, "arrival_date": _pdate(b.get("date"))})
    return {"message": f"Fahrzeug {v.number} ist im Bestand"}


def a_cancel_order(s, b):
    dealer.cancel_order(s, _id(b["id"]))
    return {"message": "Bestellung storniert"}


def a_sell(s, b):
    trade = None
    if b.get("trade"):
        t = b["trade"]
        trade = {"make": _str(t.get("make")), "model": _str(t.get("model")), "plate": _str(t.get("plate")),
                 "mileage": t.get("km") or 0, "trade_in_value": t.get("value") or "", "list_price": t.get("vk") or ""}
    sale = dealer.sell_vehicle(s, {
        "vehicle_id": _id(b.get("vehicleId")), "customer_id": _id(b.get("customerId")),
        "sale_date": _pdate(b.get("date")), "price": b.get("price") or "", "payment_account": _pay(b.get("pay")),
        "notes": _str(b.get("notes")), "trade_in": trade,
        "trade_in_purchase_id": _id(b.get("tradePurchaseId"))})
    return {"id": _s(sale.id), "message": f"Kaufvertrag {sale.number} erstellt · Rohertrag "
                                          f"${dealer.money(sale.margin)}"}


def a_cancel_sale(s, b):
    dealer.cancel_sale(s, _id(b["id"]))
    return {"message": "Verkauf storniert"}


def a_purchase(s, b):
    p = dealer.purchase_used(s, {
        "customer_id": _id(b.get("customerId")), "model_id": _id(b.get("modelId")), "make": _str(b.get("make")),
        "model": _str(b.get("model")), "category": _str(b.get("cat")), "color": _str(b.get("color")),
        "vin": _str(b.get("vin")), "plate": _str(b.get("plate")), "year": b.get("year") or 0,
        "mileage": b.get("km") or 0, "price": b.get("price") or "", "list_price": b.get("vk") or "",
        "payment_account": _pay(b.get("pay")), "purchase_date": _pdate(b.get("date")),
        "purchase_notes": _str(b.get("pnotes")),
        "status": P_STATUS_FROM_WEB.get(b.get("status") or "angekauft", "done")})
    if p.status == "trade_in":
        return {"id": _s(p.id), "message": f"Inzahlungnahme {p.number} erfasst. Sie kann jetzt bei einem Verkauf ausgewählt werden."}
    return {"id": _s(p.id), "message": f"Ankauf {p.number} gebucht. Das Fahrzeug steht im Bestand."}


def a_purchase_status(s, b):
    status = P_STATUS_FROM_WEB.get(b.get("status"))
    if status is None:
        raise BusinessError("Unbekannter Status")
    dealer.set_purchase_status(s, _id(b["id"]), status, {"payment_account": _pay(b.get("pay"))})
    return {"message": {"done": "Ankauf gebucht – das Fahrzeug steht im Bestand",
                        "cancelled": "Storniert"}.get(status, "Status geändert")}


def a_save_offer(s, b):
    oid = _id(b.get("id"))
    o = dealer.save_offer(s, oid, {
        "customer_id": _id(b.get("customerId")), "model_id": _id(b.get("modelId")), "vehicle_id": _id(b.get("vehicleId")),
        "color": _str(b.get("color")), "price": b.get("price") or "", "offer_date": _pdate(b.get("date")),
        "valid_until": _pdate(b.get("validUntil")), "notes": _str(b.get("notes"))})
    return {"id": _s(o.id), "message": f"Angebot {o.number} gespeichert"}


def a_offer_status(s, b):
    status = OF_STATUS_FROM_WEB.get(b.get("status"))
    if status is None:
        raise BusinessError("Unbekannter Status")
    res = dealer.set_offer_status(s, _id(b["id"]), status, {
        "deposit": b.get("deposit") or "", "expected_date": _pdate(b.get("expected")), "order_date": _pdate(b.get("date"))})
    if isinstance(res, m.VehicleOrder):
        return {"id": _s(res.id), "message": f"Bestellung {res.number} angelegt"
                + (" – Fahrzeug aus dem Bestand ist für den Kunden reserviert" if res.status == "arrived" else "")}
    return {"message": "Status geändert"}


def a_delete_offer(s, b):
    return {"message": f"Angebot {dealer.delete_offer(s, _id(b['id']))} gelöscht"}


def a_order_status(s, b):
    status = O_STATUS_FROM_WEB.get(b.get("status"))
    if status is None:
        raise BusinessError("Unbekannter Status")
    me_user = s.scalars(select(m.User).where(m.User.username == context.username)).first()
    if status == "delivered" and (me_user is None or not auth.may(s, me_user, "vehicle_sales")):
        raise BusinessError("Ausliefern erzeugt einen Verkauf – dafür fehlt Ihrer Rolle die Berechtigung")
    trade = None
    if b.get("trade"):
        t = b["trade"]
        trade = {"make": _str(t.get("make")), "model": _str(t.get("model")), "plate": _str(t.get("plate")),
                 "mileage": t.get("km") or 0, "trade_in_value": t.get("value") or "", "list_price": t.get("vk") or ""}
    res = dealer.set_order_status(s, _id(b["id"]), status, {
        "vin": _str(b.get("vin")), "plate": _str(b.get("plate")), "year": b.get("year") or 0, "mileage": b.get("km") or 0,
        "arrival_date": _pdate(b.get("date")), "sale_date": _pdate(b.get("date")), "price": b.get("price") or "",
        "payment_account": _pay(b.get("pay")), "notes": _str(b.get("notes")), "trade_in": trade,
        "trade_in_purchase_id": _id(b.get("tradePurchaseId"))})
    if isinstance(res, m.VehicleSale):
        return {"id": _s(res.id), "sale": True,
                "message": f"Kaufvertrag {res.number} erstellt · Restzahlung ${dealer.money(res.payment_amount)}"}
    if isinstance(res, m.Vehicle):
        return {"message": f"Fahrzeug {res.number} ist im Bestand"}
    return {"message": "Status geändert"}


def a_delete_order(s, b):
    return {"message": f"Bestellung {dealer.delete_order(s, _id(b['id']))} gelöscht"}


def a_sale_status(s, b):
    status = D_STATUS_FROM_WEB.get(b.get("status"))
    if status is None:
        raise BusinessError("Unbekannter Status")
    dealer.set_sale_status(s, _id(b["id"]), status)
    return {"message": "Verkauf storniert" if status == "cancelled" else "Status geändert"}


def a_delete_sale(s, b):
    return {"message": f"Verkauf {dealer.delete_sale(s, _id(b['id']))} gelöscht"}


def a_cancel_purchase(s, b):
    dealer.cancel_purchase(s, _id(b["id"]))
    return {"message": "Ankauf storniert"}


def a_save_customer(s, b):
    cid = _id(b.get("id"))
    data = {"name": _str(b.get("name")), "phone": _str(b.get("phone")), "email": _str(b.get("email")),
            "notes": _str(b.get("notes"))}
    if cid:
        data["active"] = bool(b.get("active", True))
    c = masterdata.save(s, "customer", cid, data)
    return {"id": _s(c.id), "message": f"Kunde {c.number} gespeichert"}


def a_save_model(s, b):
    mid = _id(b.get("id"))
    x = dealer.save_model(s, mid, {"make": _str(b.get("make")), "model": _str(b.get("model")),
                                   "category": _str(b.get("cat")), "purchase_price": b.get("ek") or "",
                                   "sales_price": b.get("vk") or "", "active": bool(b.get("active", True))})
    return {"id": _s(x.id), "message": f"Modell {x.number} gespeichert"}


def a_import_models(s, b):
    text = b.get("text") or ""
    if len(text) > 2_000_000:
        raise BusinessError("Die Datei ist zu groß")
    created, updated = dealer.import_models_csv(s, text)
    return {"message": f"{created} Modell(e) neu, {updated} aktualisiert"}


def a_settings(s, b):
    company = _str(b.get("company"))
    if not company:
        raise BusinessError("Bitte einen Firmennamen angeben")
    set_setting(s, "company_name", company)
    return {"message": "Firmendaten gespeichert"}


def a_delete_customer(s, b):
    res = masterdata.delete(s, "customer", _id(b["id"]))
    return {"message": f"Kunde {res}"}


def a_delete_model(s, b):
    res = masterdata.delete(s, "vehicle_model", _id(b["id"]))
    return {"message": f"Modell {res}"}


def a_purge(s, b):
    label = maintenance.purge(s, _str(b.get("kind")), _id(b.get("id")))
    return {"message": f"{label} endgültig gelöscht"}


def a_wipe(s, b):
    counts = maintenance.wipe_database(s, b.get("password") or "", _str(b.get("confirmation")),
                                       keep_catalog=bool(b.get("keepCatalog", True)))
    n = counts.get("vehicles", 0) + counts.get("customers", 0) + counts.get("vehicle_sales", 0)
    return {"message": f"Datenbank bereinigt ({counts.get('vehicles', 0)} Fahrzeuge, {counts.get('customers', 0)} Kunden, "
                       f"{counts.get('vehicle_sales', 0)} Verkäufe entfernt)" if n else "Datenbank bereinigt"}


def a_save_user(s, b):
    uid = _id(b.get("id"))
    password = b.get("password") or None
    roles = b.get("roles") if isinstance(b.get("roles"), list) else [_str(b.get("role"))]
    if b.get("admin"):
        roles = ["admin"]
    elif "admin" in roles:
        raise BusinessError("Administratoren werden unter „Admins & Rollen“ angelegt")
    u = auth.save_user(s, uid, _str(b.get("username")), _str(b.get("fullName")), roles,
                       bool(b.get("active", True)), password)
    return {"id": _s(u.id), "message": f"{'Admin' if u.is_admin else 'Mitarbeiter'} {u.username} gespeichert"}


def a_save_employee(s, b):
    """Mitarbeiter anlegen/ändern – wer Mitarbeiter verwalten darf, aber kein Admin ist, darf keine Admins anlegen."""
    uid = _id(b.get("id"))
    if uid:
        existing = s.get(m.User, uid)
        if existing is not None and existing.is_admin:
            raise BusinessError("Administratoren werden unter „Admins & Rollen“ verwaltet")
    roles = [r for r in (b.get("roles") or []) if r != "admin"]
    u = auth.save_user(s, uid, _str(b.get("username")), _str(b.get("fullName")), roles,
                       bool(b.get("active", True)), b.get("password") or None)
    return {"id": _s(u.id), "message": f"Mitarbeiter {u.username} gespeichert"}


def a_save_role(s, b):
    r = auth.save_role(s, _id(b.get("id")), _str(b.get("name")), list(b.get("modules") or []))
    return {"id": _s(r.id), "message": f"Rolle {r.name} gespeichert"}


def a_delete_role(s, b):
    return {"message": f"Rolle {auth.delete_role(s, _id(b['id']))} gelöscht"}


ACTIONS: dict[str, tuple[str, Callable[[Any, dict], dict]]] = {
    "saveVehicle": ("vehicles", a_save_vehicle),
    "showroom": ("vehicles", a_showroom),
    "showroomOut": ("vehicles", a_showroom_out),
    "reserve": ("vehicles", a_reserve),
    "unreserve": ("vehicles", a_unreserve),
    "removeVehicle": ("vehicles", a_remove),
    "saveOrder": ("vehicle_orders", a_save_order),
    "receiveOrder": ("vehicle_orders", a_receive_order),
    "cancelOrder": ("vehicle_orders", a_cancel_order),
    "sell": ("vehicle_sales", a_sell),
    "cancelSale": ("vehicle_sales", a_cancel_sale),
    "purchase": ("used_purchases", a_purchase),
    "cancelPurchase": ("used_purchases", a_cancel_purchase),
    "purchaseStatus": ("used_purchases", a_purchase_status),
    "saveOffer": ("offers", a_save_offer),
    "offerStatus": ("offers", a_offer_status),
    "deleteOffer": ("admin", a_delete_offer),
    "orderStatus": ("vehicle_orders", a_order_status),
    "deleteOrder": ("admin", a_delete_order),
    "saleStatus": ("vehicle_sales", a_sale_status),
    "deleteSale": ("admin", a_delete_sale),
    "saveCustomer": ("customers", a_save_customer),
    "saveModel": ("vehicle_models", a_save_model),
    "importModels": ("vehicle_models", a_import_models),
    "settings": ("admin", a_settings),
    "saveUser": ("admin", a_save_user),
    "saveEmployee": ("employees", a_save_employee),
    "saveRole": ("admin", a_save_role),
    "deleteRole": ("admin", a_delete_role),
    "purge": ("admin", a_purge),
    "deleteCustomer": ("admin", a_delete_customer),
    "deleteModel": ("admin", a_delete_model),
    "wipe": ("admin", a_wipe),
}
# Der Verkauf braucht auch Fahrzeuge, die Kundenpflege ist in Verkauf/Ankauf nötig
EXTRA_MODULES = {"reserve": ("vehicle_sales", "showroom"), "showroom": ("showroom",), "showroomOut": ("showroom",),
                 "saveCustomer": ("vehicle_sales", "used_purchases")}


# --------------------------------------------------------------------------
# Anwendung
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def asset_version() -> str:
    """Prüfsumme über die Oberflächen-Dateien – ändert sich mit jedem Update."""
    h = hashlib.sha256()
    for name in ("app.js", "app.css", "index.html"):
        h.update((STATIC / name).read_bytes())
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------
# Logo (vom Administrator hochgeladen, unverändert in der Datenbank gespeichert –
# das Dateisystem bei Render ist nicht dauerhaft)
# --------------------------------------------------------------------------

LOGO_TYPES = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",), "image/webp": (b"RIFF",)}
LOGO_MAX_BYTES = 4 * 1024 * 1024


def logo_version(s) -> str:
    return get_setting(s, "logo_version") or "standard"


def a_upload_logo(s, b):
    data_url = b.get("data") or ""
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise BusinessError("Bitte eine Bilddatei auswählen")
    mime, payload = data_url[5:].split(";base64,", 1)
    if mime not in LOGO_TYPES:
        raise BusinessError("Bitte ein Bild im Format PNG, JPG oder WebP hochladen")
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise BusinessError("Die Bilddatei ist beschädigt") from exc
    if len(raw) > LOGO_MAX_BYTES:
        raise BusinessError("Das Bild ist zu groß (höchstens 4 MB)")
    if not any(raw.startswith(sig) for sig in LOGO_TYPES[mime]) or (mime == "image/webp" and raw[8:12] != b"WEBP"):
        raise BusinessError("Die Datei ist kein gültiges Bild")
    set_setting(s, "logo_data", base64.b64encode(raw).decode("ascii"))
    set_setting(s, "logo_mime", mime)
    set_setting(s, "logo_version", hashlib.sha256(raw).hexdigest()[:12])
    return {"message": "Logo gespeichert"}


def a_reset_logo(s, b):
    for key in ("logo_data", "logo_mime", "logo_version"):
        set_setting(s, key, "")
    return {"message": "Standardlogo wiederhergestellt"}


ACTIONS.update({"uploadLogo": ("admin", a_upload_logo), "resetLogo": ("admin", a_reset_logo)})


@lru_cache(maxsize=1)
def index_html() -> str:
    v = asset_version()
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return (html.replace("APPVERSION", __version__).replace("/static/app.js", f"/static/app.js?v={v}")
                .replace("/static/app.css", f"/static/app.css?v={v}"))


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(url: str | None = None) -> FastAPI:
    secret = os.environ.get("ERP_SECRET_KEY")
    if not secret:
        secret = secrets.token_hex(32)
        log.warning("ERP_SECRET_KEY ist nicht gesetzt – Anmeldungen gelten nur bis zum nächsten Neustart.")
    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(_startup)
        yield

    def _startup():
        if beta.is_beta():                       # Testumgebung: frische Kopie der Live-Daten
            db.connect(beta.target_url(url, database_url()))
            beta.clone(db.engine())
        else:
            db.connect(url or database_url())
        db.init_schema()
        with db.session_scope() as s:            # Spielbetrieb: Fahrzeuge werden immer ohne Steuer gerechnet
            set_setting(s, "vehicle_vat_rate", "0")
            dealer.link_vehicles_to_catalog(s)  # Fahrzeuge ohne Katalogbezug nachträglich verknüpfen
        initial = os.environ.get("ERP_ADMIN_PASSWORD")
        if initial:
            with db.session_scope() as s:
                admin = s.scalars(select(m.User).where(m.User.username == "admin")).first()
                if admin and auth.verify_password(DEFAULT_PASSWORD, admin.password_hash):
                    admin.password_hash = auth.hash_password(initial)

    app = FastAPI(title="ERP Desk Web", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="erp_session", max_age=12 * 3600,
                       same_site="strict", https_only=os.environ.get("ERP_INSECURE_COOKIES") != "1")

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            # Dateien tragen ?v=<Prüfsumme>; ohne passende Version immer neu prüfen
            response.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                                 if request.query_params.get("v") == asset_version()
                                                 else "no-cache")
        return response

    def current_user(s, request: Request) -> m.User | None:
        uid = request.session.get("uid")
        user = s.get(m.User, uid) if uid else None
        if user is None or not user.active or request.session.get("pw") != user.password_hash[-16:]:
            return None
        set_user(user)
        return user

    def with_session(fn):
        """Führt fn(session) in einer Transaktion aus und übersetzt Fehler in JSON."""
        try:
            with db.session_scope() as s:
                return fn(s)
        except BusinessError as exc:
            return _error(400, str(exc))
        except StaleDataError:
            return _error(409, "Der Datensatz wurde inzwischen von einem anderen Benutzer geändert. "
                               "Bitte neu laden.")
        except OperationalError:
            log.exception("Datenbankfehler")
            return _error(503, "Die Datenbank ist gerade nicht erreichbar. Bitte gleich erneut versuchen.")

    def json_body(request: Request, body: bytes) -> dict | JSONResponse:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return _error(415, "JSON erwartet")
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return _error(400, "Ungültige Anfrage")
        return data if isinstance(data, dict) else _error(400, "Ungültige Anfrage")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.post("/api/login")
    async def login(request: Request):
        data = json_body(request, await request.body())
        if isinstance(data, JSONResponse):
            return data

        def do(s):
            try:
                user = auth.authenticate(s, _str(data.get("username")), data.get("password") or "")
            except BusinessError:
                time.sleep(1.0)       # bremst das Durchprobieren von Passwörtern
                raise
            request.session.clear()
            request.session.update(uid=user.id, pw=user.password_hash[-16:])
            set_user(user)
            return state(s, user)
        return await run_in_threadpool(with_session, do)

    @app.post("/api/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/state")
    def get_state(request: Request):
        def do(s):
            user = current_user(s, request)
            if user is None:
                return _error(401, "Bitte anmelden")
            return state(s, user)
        return with_session(do)

    @app.post("/api/beta/reload")
    def beta_reload(request: Request):
        """Beta: Kopie der Live-Daten neu laden (nur Administratoren)."""
        if not beta.is_beta():
            return _error(404, "Nur in der Testumgebung verfügbar")
        user = with_session(lambda s: current_user(s, request))
        if not isinstance(user, m.User) or not user.is_admin:
            return _error(403, "Nur Administratoren dürfen die Testdaten neu laden")
        try:
            rows = beta.clone(db.engine())
            db.init_schema()
            with db.session_scope() as s:
                set_setting(s, "vehicle_vat_rate", "0")
                dealer.link_vehicles_to_catalog(s)
        except Exception:                                     # noqa: BLE001
            log.exception("Beta-Daten konnten nicht geladen werden")
            return _error(503, "Die Live-Daten konnten nicht geladen werden. Bitte die Variable ERP_BETA_SOURCE_URL prüfen.")
        return {"message": f"Live-Daten neu geladen ({rows} Datensätze). Bitte neu anmelden, falls sich Zugänge geändert haben."}

    @app.post("/api/password")
    async def password(request: Request):
        data = json_body(request, await request.body())
        if isinstance(data, JSONResponse):
            return data

        def do(s):
            user = current_user(s, request)
            if user is None:
                return _error(401, "Bitte anmelden")
            new = data.get("new") or ""
            if not new:
                raise BusinessError("Bitte ein neues Passwort eingeben")
            if new == DEFAULT_PASSWORD:
                raise BusinessError("Bitte ein anderes Passwort wählen")
            auth.change_password(s, user.id, data.get("old") or "", new)
            s.flush()
            request.session["pw"] = user.password_hash[-16:]
            return {"message": "Passwort geändert"}
        return await run_in_threadpool(with_session, do)

    @app.post("/api/action/{name}")
    async def action(name: str, request: Request):
        data = json_body(request, await request.body())
        if isinstance(data, JSONResponse):
            return data
        spec = ACTIONS.get(name)
        if spec is None:
            return _error(404, "Unbekannte Aktion")
        module, fn = spec

        def do(s):
            user = current_user(s, request)
            if user is None:
                return _error(401, "Bitte anmelden")
            if auth.verify_password(DEFAULT_PASSWORD, user.password_hash):
                return _error(403, "Bitte zuerst das Standardpasswort ändern")
            allowed = (module,) + EXTRA_MODULES.get(name, ())
            mods = auth.modules_for(s, user)
            if not any(mod in mods for mod in allowed):
                return _error(403, "Dafür fehlt Ihrer Rolle die Berechtigung")
            return fn(s, data)
        return await run_in_threadpool(with_session, do)

    @app.get("/")
    def index():
        def do(s):
            html = index_html().replace("LOGOVERSION", logo_version(s))
            if beta.is_beta():                   # deutlicher Hinweis auf jeder Seite, auch beim Anmelden
                html = html.replace("<body>", '<body class="is-beta"><div class="betaRibbon" role="note">BETA · '
                                    'Testumgebung mit einer Kopie der Live-Daten – Änderungen werden nicht übernommen</div>', 1)
                html = html.replace("<title>", "<title>BETA · ", 1)
            return html
        html = with_session(do)
        if not isinstance(html, str):       # Datenbank nicht erreichbar: Seite trotzdem ausliefern
            html = index_html().replace("LOGOVERSION", "standard")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/logo")
    def logo(request: Request):
        def do(s):
            return get_setting(s, "logo_data"), get_setting(s, "logo_mime"), logo_version(s)
        res = with_session(do)
        data, mime, version = res if isinstance(res, tuple) else ("", "", "standard")
        cache = ("public, max-age=31536000, immutable" if request.query_params.get("v") == version
                 else "no-cache")
        if data:
            return Response(base64.b64decode(data), media_type=mime or "image/png",
                            headers={"Cache-Control": cache, "Content-Security-Policy": "default-src 'none'"})
        return FileResponse(STATIC / "logo.png", headers={"Cache-Control": cache})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()

"""Werkzeuge für Administratoren: einzelne Datensätze endgültig löschen, Datenbank bereinigen.

Anders als Storno/Löschen in den Fachfunktionen wird hier nichts zurückgebucht und kein
Status angepasst – der Datensatz verschwindet einfach. Abhängige Datensätze, die ohne ihn
nicht bestehen können (z. B. die Verkäufe eines gelöschten Fahrzeugs), werden mitgelöscht;
optionale Verweise werden geleert.
"""
from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import db as _db
from .. import models as m
from . import BusinessError, audit, context
from .auth import verify_password

WIPE_CONFIRMATION = "LÖSCHEN"


# --------------------------------------------------------------------------
# Einzelne Datensätze
# --------------------------------------------------------------------------

def _del_sale(s: Session, sale_id: int) -> None:
    s.execute(update(m.UsedPurchase).where(m.UsedPurchase.sale_id == sale_id).values(sale_id=None))
    s.execute(delete(m.VehicleSale).where(m.VehicleSale.id == sale_id))


def _del_purchase(s: Session, purchase_id: int) -> None:
    s.execute(delete(m.UsedPurchase).where(m.UsedPurchase.id == purchase_id))


def _del_offer(s: Session, offer_id: int) -> None:
    s.execute(delete(m.Offer).where(m.Offer.id == offer_id))


def _del_order(s: Session, order_id: int) -> None:
    s.execute(update(m.Offer).where(m.Offer.order_id == order_id).values(order_id=None))
    s.execute(update(m.VehicleSale).where(m.VehicleSale.order_id == order_id).values(order_id=None))
    s.execute(delete(m.VehicleOrder).where(m.VehicleOrder.id == order_id))


def _del_vehicle(s: Session, vehicle_id: int) -> None:
    for sid in s.scalars(select(m.VehicleSale.id).where(m.VehicleSale.vehicle_id == vehicle_id)).all():
        _del_sale(s, sid)
    for pid in s.scalars(select(m.UsedPurchase.id).where(m.UsedPurchase.vehicle_id == vehicle_id)).all():
        _del_purchase(s, pid)
    s.execute(update(m.VehicleSale).where(m.VehicleSale.trade_in_vehicle_id == vehicle_id)
              .values(trade_in_vehicle_id=None))
    s.execute(update(m.VehicleOrder).where(m.VehicleOrder.vehicle_id == vehicle_id).values(vehicle_id=None))
    s.execute(update(m.Offer).where(m.Offer.vehicle_id == vehicle_id).values(vehicle_id=None))
    s.execute(delete(m.Vehicle).where(m.Vehicle.id == vehicle_id))


def _del_customer(s: Session, customer_id: int) -> None:
    for sid in s.scalars(select(m.VehicleSale.id).where(m.VehicleSale.customer_id == customer_id)).all():
        _del_sale(s, sid)
    for pid in s.scalars(select(m.UsedPurchase.id).where(m.UsedPurchase.customer_id == customer_id)).all():
        _del_purchase(s, pid)
    s.execute(delete(m.Offer).where(m.Offer.customer_id == customer_id))
    s.execute(update(m.VehicleOrder).where(m.VehicleOrder.customer_id == customer_id).values(customer_id=None))
    s.execute(update(m.Vehicle).where(m.Vehicle.reserved_for_id == customer_id, m.Vehicle.status == "reserved")
              .values(status="stock"))
    s.execute(update(m.Vehicle).where(m.Vehicle.reserved_for_id == customer_id)
              .values(reserved_for_id=None, reserved_until=None))
    s.execute(delete(m.Customer).where(m.Customer.id == customer_id))


def _del_model(s: Session, model_id: int) -> None:
    for oid in s.scalars(select(m.VehicleOrder.id).where(m.VehicleOrder.model_id == model_id)).all():
        _del_order(s, oid)
    s.execute(update(m.Vehicle).where(m.Vehicle.model_id == model_id).values(model_id=None))
    s.execute(update(m.Offer).where(m.Offer.model_id == model_id).values(model_id=None))
    # Angebote ohne Modell und ohne Fahrzeug sind wertlos
    s.execute(delete(m.Offer).where(m.Offer.model_id.is_(None), m.Offer.vehicle_id.is_(None)))
    s.execute(delete(m.VehicleModel).where(m.VehicleModel.id == model_id))


def _del_user(s: Session, user_id: int) -> None:
    user = s.get(m.User, user_id)
    if user is None:
        return
    if user.username == context.username:
        raise BusinessError("Den eigenen Benutzer kann man nicht löschen")
    others = s.scalars(select(m.User).where(m.User.role == "admin", m.User.active.is_(True),
                                            m.User.id != user_id)).all()
    if user.role == "admin" and not others:
        raise BusinessError("Es muss mindestens ein aktiver Administrator bestehen bleiben")
    s.delete(user)


PURGE = {
    "sale": (m.VehicleSale, _del_sale, "Verkauf"),
    "purchase": (m.UsedPurchase, _del_purchase, "Ankauf"),
    "offer": (m.Offer, _del_offer, "Angebot"),
    "order": (m.VehicleOrder, _del_order, "Bestellung"),
    "vehicle": (m.Vehicle, _del_vehicle, "Fahrzeug"),
    "customer": (m.Customer, _del_customer, "Kunde"),
    "model": (m.VehicleModel, _del_model, "Modell"),
    "user": (m.User, _del_user, "Benutzer"),
}


def purge(s: Session, kind: str, obj_id: int) -> str:
    """Löscht einen Datensatz endgültig (nur Administratoren). Gibt die Bezeichnung zurück."""
    if kind not in PURGE:
        raise BusinessError("Unbekannter Datensatztyp")
    cls, fn, noun = PURGE[kind]
    obj = s.get(cls, obj_id)
    if obj is None:
        raise BusinessError(f"{noun} nicht gefunden")
    label = f"{noun} {getattr(obj, 'number', None) or getattr(obj, 'username', obj_id)}"
    try:
        with s.begin_nested():
            fn(s, obj_id)
            s.flush()
    except IntegrityError as exc:
        raise BusinessError(f"{label} wird noch in anderen Bereichen (z. B. Teile & Service im "
                            "Desktop-Programm) verwendet und kann nicht gelöscht werden") from exc
    s.expire_all()
    audit(s, "purge", kind, obj_id, label)
    return label


# --------------------------------------------------------------------------
# Datenbank bereinigen
# --------------------------------------------------------------------------

# Reihenfolge so, dass keine Fremdschlüssel verletzt werden
_WIPE_ORDER = [
    m.Offer, m.UsedPurchase, m.VehicleSale, m.VehicleOrder, m.Vehicle,
    m.Invoice, m.SalesOrderItem, m.SalesOrder, m.PurchaseOrderItem, m.PurchaseOrder,
    m.StockMovement, m.StockLevel, m.Product,
    m.JournalLine, m.JournalEntry, m.Customer,
]
# Nummernkreise, die nach dem Bereinigen wieder von vorn beginnen
_RESET_RANGES = {"customer", "product", "sales_order", "invoice", "purchase_order", "journal", "vehicle",
                 "vehicle_order", "vehicle_sale", "used_purchase", "offer"}


def wipe_database(s: Session, password: str, confirmation: str, keep_catalog: bool = True) -> dict:
    """Entfernt alle Geschäftsdaten. Benutzer, Firmendaten, Kontenplan und Lagerorte bleiben.

    Erfordert das Passwort des angemeldeten Administrators und die Eingabe „LÖSCHEN“.
    """
    user = s.scalars(select(m.User).where(m.User.username == context.username)).first()
    if user is None or user.role != "admin":
        raise BusinessError("Nur Administratoren dürfen die Datenbank bereinigen")
    if (confirmation or "").strip().upper() != WIPE_CONFIRMATION:
        raise BusinessError(f"Bitte zur Bestätigung „{WIPE_CONFIRMATION}“ eintragen")
    if not verify_password(password or "", user.password_hash):
        raise BusinessError("Das Passwort ist falsch")
    counts = {}
    tables = list(_WIPE_ORDER)
    if not keep_catalog:
        tables += [m.VehicleModel, m.Supplier]
    else:
        s.execute(update(m.VehicleModel).values(default_supplier_id=None))
        tables += [m.Supplier]
    for cls in tables:
        counts[cls.__tablename__] = s.execute(delete(cls)).rowcount or 0
    s.execute(delete(m.AuditLog))
    starts = {key: (prefix, start) for key, prefix, start, _ in _db.DEFAULT_RANGES}
    resets = set(_RESET_RANGES) | ({"vehicle_model", "supplier"} if not keep_catalog else {"supplier"})
    for key in resets:
        rng = s.get(m.NumberRange, key)
        if rng is not None and key in starts:
            rng.next_value = starts[key][1]
    s.flush()
    s.expire_all()
    audit(s, "wipe", "database", "", f"Katalog {'behalten' if keep_catalog else 'gelöscht'}")
    return counts

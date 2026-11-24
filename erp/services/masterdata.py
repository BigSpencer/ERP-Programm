"""Stammdatenpflege: Kunden, Lieferanten, Artikel, Lager, Konten."""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models as m
from . import BusinessError, audit, dec, next_number

ENTITIES = {
    "customer": m.Customer,
    "supplier": m.Supplier,
    "product": m.Product,
    "warehouse": m.Warehouse,
    "account": m.Account,
    "vehicle_model": m.VehicleModel,
}

DECIMAL_FIELDS = {"credit_limit", "sales_price", "purchase_price", "tax_rate", "min_stock"}
INT_FIELDS = {"payment_terms_days"}
REQUIRED = {
    "customer": ["name"],
    "supplier": ["name"],
    "product": ["name"],
    "warehouse": ["code", "name"],
    "account": ["number", "name", "account_type"],
}
NUMBER_KEY = {"customer": "customer", "supplier": "supplier", "product": "product"}


def search(s: Session, entity: str, text: str = "", only_active: bool = False, limit: int = 1000):
    cls = ENTITIES[entity]
    q = select(cls)
    if text:
        like = f"%{text.lower()}%"
        cols = [c for c in ("number", "name", "city", "code", "email") if hasattr(cls, c)]
        q = q.where(or_(*[func.lower(getattr(cls, c)).like(like) for c in cols]))
    if only_active and hasattr(cls, "active"):
        q = q.where(cls.active.is_(True))
    order = getattr(cls, "number", None) or getattr(cls, "code")
    return s.scalars(q.order_by(order).limit(limit)).all()


def save(s: Session, entity: str, obj_id: int | None, data: dict, version: int | None = None):
    """Legt an oder ändert. ``version`` dient der Konflikterkennung im Mehrbenutzerbetrieb."""
    cls = ENTITIES[entity]
    for field in REQUIRED[entity]:
        if not str(data.get(field, "")).strip():
            raise BusinessError(f"Pflichtfeld fehlt: {field}")
    if obj_id:
        obj = s.get(cls, obj_id)
        if obj is None:
            raise BusinessError("Datensatz wurde inzwischen gelöscht")
        if version is not None and obj.version != version:
            raise BusinessError("Der Datensatz wurde inzwischen von einem anderen Benutzer "
                                "geändert. Bitte neu laden.")
    else:
        obj = cls()
    for key, value in data.items():
        if not hasattr(cls, key) or key in ("id", "version"):
            continue
        if key in DECIMAL_FIELDS:
            value = dec(value)
        elif key in INT_FIELDS:
            value = int(dec(value))
        elif isinstance(value, str):
            value = value.strip()
        setattr(obj, key, value)
    if entity in NUMBER_KEY and not getattr(obj, "number", None):
        obj.number = next_number(s, NUMBER_KEY[entity])
    if not obj_id:
        s.add(obj)
    try:
        s.flush()
    except IntegrityError as exc:
        raise BusinessError("Nummer/Kürzel ist bereits vergeben") from exc
    audit(s, "create" if not obj_id else "update", entity, obj.id,
          getattr(obj, "number", "") or getattr(obj, "code", ""))
    return obj


def delete(s: Session, entity: str, obj_id: int) -> str:
    """Löscht einen Datensatz; ist er referenziert, wird er nur deaktiviert."""
    cls = ENTITIES[entity]
    obj = s.get(cls, obj_id)
    if obj is None:
        return "nicht gefunden"
    try:
        with s.begin_nested():
            s.delete(obj)
            s.flush()
        audit(s, "delete", entity, obj_id)
        return "gelöscht"
    except IntegrityError:
        obj = s.get(cls, obj_id)
        if hasattr(obj, "active"):
            obj.active = False
            audit(s, "deactivate", entity, obj_id)
            return "deaktiviert (wird noch verwendet)"
        raise BusinessError("Datensatz wird noch verwendet und kann nicht gelöscht werden")

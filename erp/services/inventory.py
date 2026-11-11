"""Bestandsführung (Lager)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from . import BusinessError, context, dec


def _level(s: Session, product_id: int, warehouse_id: int) -> m.StockLevel:
    lvl = s.scalars(select(m.StockLevel).where(
        m.StockLevel.product_id == product_id,
        m.StockLevel.warehouse_id == warehouse_id).with_for_update()).first()
    if lvl is None:
        lvl = m.StockLevel(product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal(0))
        s.add(lvl)
        s.flush()
    return lvl


def book(s: Session, product_id: int, warehouse_id: int, quantity, movement_type: str,
         reference: str = "", allow_negative: bool = False) -> m.StockMovement:
    """Bucht eine Bestandsbewegung. ``quantity`` ist vorzeichenbehaftet."""
    qty = dec(quantity)
    if qty == 0:
        raise BusinessError("Menge darf nicht 0 sein")
    if movement_type not in m.MOVEMENT_TYPES:
        raise BusinessError("Unbekannte Bewegungsart")
    product = s.get(m.Product, product_id)
    if product is None:
        raise BusinessError("Artikel nicht gefunden")
    if not product.stock_managed:
        raise BusinessError(f"Artikel {product.number} ist nicht bestandsgeführt")
    lvl = _level(s, product_id, warehouse_id)
    new_qty = Decimal(lvl.quantity) + qty
    if new_qty < 0 and not allow_negative:
        raise BusinessError(
            f"Nicht genug Bestand für {product.number} {product.name}: "
            f"verfügbar {lvl.quantity:.3f}, benötigt {-qty:.3f}")
    lvl.quantity = new_qty
    mv = m.StockMovement(product_id=product_id, warehouse_id=warehouse_id,
                         movement_type=movement_type, quantity=qty,
                         reference=reference, username=context.username)
    s.add(mv)
    s.flush()
    return mv


def transfer(s: Session, product_id: int, from_wh: int, to_wh: int, quantity, reference=""):
    qty = dec(quantity)
    if qty <= 0:
        raise BusinessError("Menge muss positiv sein")
    if from_wh == to_wh:
        raise BusinessError("Quell- und Ziellager sind identisch")
    book(s, product_id, from_wh, -qty, "transfer_out", reference)
    book(s, product_id, to_wh, qty, "transfer_in", reference)


def set_inventory(s: Session, product_id: int, warehouse_id: int, counted, reference="Inventur"):
    """Inventur: setzt den Bestand auf die gezählte Menge."""
    counted = dec(counted)
    if counted < 0:
        raise BusinessError("Gezählte Menge darf nicht negativ sein")
    lvl = _level(s, product_id, warehouse_id)
    diff = counted - Decimal(lvl.quantity)
    if diff != 0:
        book(s, product_id, warehouse_id, diff, "adjust", reference)
    return diff


def stock_of(s: Session, product_id: int, warehouse_id: int | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(m.StockLevel.quantity), 0)).where(
        m.StockLevel.product_id == product_id)
    if warehouse_id:
        q = q.where(m.StockLevel.warehouse_id == warehouse_id)
    return Decimal(s.scalar(q))


def overview(s: Session, warehouse_id: int | None = None, text: str = ""):
    """Bestandsliste je Artikel: (Artikel, Bestand, Bestellt offen, Mindestbestand)."""
    q = select(m.Product).where(m.Product.stock_managed.is_(True), m.Product.active.is_(True))
    if text:
        like = f"%{text.lower()}%"
        q = q.where(func.lower(m.Product.name).like(like) | func.lower(m.Product.number).like(like))
    rows = []
    open_po = dict(s.execute(
        select(m.PurchaseOrderItem.product_id,
               func.sum(m.PurchaseOrderItem.quantity - m.PurchaseOrderItem.received_qty))
        .join(m.PurchaseOrder)
        .where(m.PurchaseOrder.status.in_(["ordered", "partial"]))
        .group_by(m.PurchaseOrderItem.product_id)).all())
    for p in s.scalars(q.order_by(m.Product.number)):
        rows.append({
            "product": p,
            "stock": stock_of(s, p.id, warehouse_id),
            "on_order": Decimal(open_po.get(p.id) or 0),
            "min_stock": Decimal(p.min_stock or 0),
        })
    return rows


def below_minimum(s: Session):
    return [r for r in overview(s) if r["stock"] + r["on_order"] < r["min_stock"]]


def movements(s: Session, product_id: int | None = None, limit: int = 500):
    q = select(m.StockMovement).order_by(m.StockMovement.id.desc()).limit(limit)
    if product_id:
        q = q.where(m.StockMovement.product_id == product_id)
    return s.scalars(q).all()

"""Belegfluss Vertrieb (Auftrag → Lieferung → Rechnung) und Einkauf
(Bestellung → Wareneingang)."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from . import BusinessError, audit, context, dec, finance, inventory, next_number


def _default_warehouse(s: Session) -> m.Warehouse:
    wh = s.scalars(select(m.Warehouse).order_by(m.Warehouse.id)).first()
    if wh is None:
        raise BusinessError("Kein Lager angelegt")
    return wh


def _apply_items(s: Session, order, item_cls, items: list[dict], price_field: str) -> None:
    """Ersetzt die Positionen eines Belegs."""
    order.items.clear()
    s.flush()
    pos = 10
    for data in items:
        product = s.get(m.Product, data["product_id"]) if data.get("product_id") else None
        qty = dec(data.get("quantity"), "1")
        if qty <= 0:
            raise BusinessError("Mengen müssen positiv sein")
        price = data.get("unit_price")
        if price in (None, "") and product is not None:
            price = getattr(product, price_field)
        tax = data.get("tax_rate")
        if tax in (None, "") and product is not None:
            tax = product.tax_rate
        desc = data.get("description") or (product.name if product else "")
        if not desc:
            raise BusinessError("Position ohne Artikel benötigt eine Beschreibung")
        order.items.append(item_cls(
            position=pos, product_id=product.id if product else None, description=desc,
            quantity=qty, unit_price=dec(price), discount_pct=dec(data.get("discount_pct")),
            tax_rate=dec(tax, "19")))
        pos += 10
    if not order.items:
        raise BusinessError("Der Beleg benötigt mindestens eine Position")


def _check_version(obj, version):
    if version is not None and obj.version != version:
        raise BusinessError("Der Beleg wurde inzwischen von einem anderen Benutzer geändert. "
                            "Bitte neu laden.")


# --------------------------------------------------------------------------
# Vertrieb
# --------------------------------------------------------------------------

def save_sales_order(s: Session, order_id: int | None, header: dict, items: list[dict],
                     version: int | None = None) -> m.SalesOrder:
    if not header.get("customer_id"):
        raise BusinessError("Bitte einen Kunden wählen")
    if order_id:
        so = s.get(m.SalesOrder, order_id)
        _check_version(so, version)
        if so.status != "draft":
            raise BusinessError("Nur Aufträge im Status 'Entwurf' können geändert werden")
    else:
        so = m.SalesOrder(number=next_number(s, "sales_order"), created_by=context.username)
        s.add(so)
    so.customer_id = header["customer_id"]
    so.order_date = header.get("order_date") or dt.date.today()
    so.delivery_date = header.get("delivery_date")
    so.warehouse_id = header.get("warehouse_id") or _default_warehouse(s).id
    so.customer_reference = header.get("customer_reference", "")
    so.notes = header.get("notes", "")
    _apply_items(s, so, m.SalesOrderItem, items, "sales_price")
    s.flush()
    audit(s, "save", "sales_order", so.number)
    return so


def open_receivables(s: Session, customer_id: int) -> Decimal:
    invs = s.scalars(select(m.Invoice).where(m.Invoice.customer_id == customer_id,
                                             m.Invoice.status.in_(["open", "partial"])))
    return sum((i.open_amount for i in invs), Decimal(0))


def confirm_sales_order(s: Session, order_id: int) -> list[str]:
    """Bestätigt einen Auftrag. Gibt Warnungen zurück (z. B. Bestand knapp)."""
    so = s.get(m.SalesOrder, order_id)
    if so.status != "draft":
        raise BusinessError("Nur Entwürfe können bestätigt werden")
    limit = Decimal(so.customer.credit_limit or 0)
    if limit > 0:
        exposure = open_receivables(s, so.customer_id) + so.gross_total
        if exposure > limit:
            raise BusinessError(f"Kreditlimit überschritten: {exposure:.2f} > {limit:.2f} EUR")
    warnings = []
    for it in so.items:
        if it.product and it.product.stock_managed:
            avail = inventory.stock_of(s, it.product_id, so.warehouse_id)
            if avail < it.quantity:
                warnings.append(f"{it.product.number}: Bestand {avail:.3f} < Bedarf {it.quantity:.3f}")
    so.status = "confirmed"
    audit(s, "confirm", "sales_order", so.number)
    return warnings


def deliver_sales_order(s: Session, order_id: int) -> m.SalesOrder:
    so = s.get(m.SalesOrder, order_id)
    if so.status != "confirmed":
        raise BusinessError("Nur bestätigte Aufträge können geliefert werden")
    for it in so.items:
        if it.product and it.product.stock_managed:
            inventory.book(s, it.product_id, so.warehouse_id, -Decimal(it.quantity), "issue", so.number)
    so.status = "delivered"
    audit(s, "deliver", "sales_order", so.number)
    return so


def invoice_sales_order(s: Session, order_id: int, invoice_date: dt.date | None = None) -> m.Invoice:
    so = s.get(m.SalesOrder, order_id)
    if so.status != "delivered":
        raise BusinessError("Nur gelieferte Aufträge können fakturiert werden")
    invoice_date = invoice_date or dt.date.today()
    inv = m.Invoice(
        number=next_number(s, "invoice"), customer_id=so.customer_id, sales_order_id=so.id,
        invoice_date=invoice_date,
        due_date=invoice_date + dt.timedelta(days=so.customer.payment_terms_days or 0),
        net_amount=so.net_total, tax_amount=so.tax_total, gross_amount=so.gross_total)
    inv.customer = so.customer
    s.add(inv)
    s.flush()
    finance.post_sales_invoice(s, inv, so.items)
    so.status = "invoiced"
    audit(s, "invoice", "sales_order", so.number, inv.number)
    return inv


def cancel_sales_order(s: Session, order_id: int) -> None:
    so = s.get(m.SalesOrder, order_id)
    if so.status not in ("draft", "confirmed"):
        raise BusinessError("Gelieferte oder fakturierte Aufträge können nicht storniert werden")
    so.status = "cancelled"
    audit(s, "cancel", "sales_order", so.number)


def list_sales_orders(s: Session, status: str | None = None, text: str = ""):
    q = select(m.SalesOrder).join(m.Customer).order_by(m.SalesOrder.id.desc())
    if status:
        q = q.where(m.SalesOrder.status == status)
    if text:
        like = f"%{text}%"
        q = q.where(m.SalesOrder.number.ilike(like) | m.Customer.name.ilike(like))
    return s.scalars(q.limit(1000)).all()


def list_invoices(s: Session, status: str | None = None, text: str = ""):
    q = select(m.Invoice).join(m.Customer).order_by(m.Invoice.id.desc())
    if status:
        q = q.where(m.Invoice.status == status)
    if text:
        like = f"%{text}%"
        q = q.where(m.Invoice.number.ilike(like) | m.Customer.name.ilike(like))
    return s.scalars(q.limit(1000)).all()


# --------------------------------------------------------------------------
# Einkauf
# --------------------------------------------------------------------------

def save_purchase_order(s: Session, order_id: int | None, header: dict, items: list[dict],
                        version: int | None = None) -> m.PurchaseOrder:
    if not header.get("supplier_id"):
        raise BusinessError("Bitte einen Lieferanten wählen")
    if order_id:
        po = s.get(m.PurchaseOrder, order_id)
        _check_version(po, version)
        if po.status != "draft":
            raise BusinessError("Nur Bestellungen im Status 'Entwurf' können geändert werden")
    else:
        po = m.PurchaseOrder(number=next_number(s, "purchase_order"), created_by=context.username)
        s.add(po)
    po.supplier_id = header["supplier_id"]
    po.order_date = header.get("order_date") or dt.date.today()
    po.expected_date = header.get("expected_date")
    po.warehouse_id = header.get("warehouse_id") or _default_warehouse(s).id
    po.notes = header.get("notes", "")
    _apply_items(s, po, m.PurchaseOrderItem, items, "purchase_price")
    s.flush()
    audit(s, "save", "purchase_order", po.number)
    return po


def place_purchase_order(s: Session, order_id: int) -> None:
    po = s.get(m.PurchaseOrder, order_id)
    if po.status != "draft":
        raise BusinessError("Nur Entwürfe können bestellt werden")
    po.status = "ordered"
    audit(s, "order", "purchase_order", po.number)


def receive_purchase_order(s: Session, order_id: int, quantities: dict[int, object] | None = None):
    """Wareneingang. ``quantities`` = {item_id: Menge}; None = alles Offene."""
    po = s.get(m.PurchaseOrder, order_id)
    if po.status not in ("ordered", "partial"):
        raise BusinessError("Wareneingang nur für bestellte Bestellungen möglich")
    received = []
    for it in po.items:
        qty = it.open_qty if quantities is None else dec(quantities.get(it.id, 0))
        if qty == 0:
            continue
        if qty < 0 or qty > it.open_qty:
            raise BusinessError(f"Pos. {it.position}: Menge muss zwischen 0 und {it.open_qty:.3f} liegen")
        if it.product and it.product.stock_managed:
            inventory.book(s, it.product_id, po.warehouse_id, qty, "receipt", po.number)
        it.received_qty = Decimal(it.received_qty or 0) + qty
        received.append((it, qty))
    if not received:
        raise BusinessError("Keine Mengen erfasst")
    finance.post_goods_receipt(s, po, received)
    po.status = "received" if all(i.open_qty == 0 for i in po.items) else "partial"
    audit(s, "receive", "purchase_order", po.number)
    return po


def cancel_purchase_order(s: Session, order_id: int) -> None:
    po = s.get(m.PurchaseOrder, order_id)
    if po.status not in ("draft", "ordered"):
        raise BusinessError("Teil- oder vollständig gelieferte Bestellungen können nicht storniert werden")
    po.status = "cancelled"
    audit(s, "cancel", "purchase_order", po.number)


def list_purchase_orders(s: Session, status: str | None = None, text: str = ""):
    q = select(m.PurchaseOrder).join(m.Supplier).order_by(m.PurchaseOrder.id.desc())
    if status:
        q = q.where(m.PurchaseOrder.status == status)
    if text:
        like = f"%{text}%"
        q = q.where(m.PurchaseOrder.number.ilike(like) | m.Supplier.name.ilike(like))
    return s.scalars(q.limit(1000)).all()


def create_reorder_proposals(s: Session) -> list[m.PurchaseOrder]:
    """Disposition: legt Bestellentwürfe für Artikel unter Mindestbestand an
    (gruppiert nach Standardlieferant)."""
    by_supplier: dict[int, list[dict]] = defaultdict(list)
    for row in inventory.below_minimum(s):
        p = row["product"]
        if not p.default_supplier_id:
            continue
        need = row["min_stock"] * 2 - row["stock"] - row["on_order"]
        by_supplier[p.default_supplier_id].append({"product_id": p.id, "quantity": need})
    created = []
    for supplier_id, items in by_supplier.items():
        created.append(save_purchase_order(
            s, None, {"supplier_id": supplier_id, "notes": "Automatischer Bestellvorschlag"}, items))
    return created

"""Kennzahlen für das Dashboard und Belegdruck."""
from __future__ import annotations

import datetime as dt
import html
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from . import finance, get_setting, inventory


def money(value) -> str:
    v = Decimal(value or 0).quantize(Decimal("0.01"))
    s = f"{v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def kpis(s: Session) -> dict:
    today = dt.date.today()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    revenue_month = s.scalar(select(func.coalesce(func.sum(m.Invoice.net_amount), 0)).where(
        m.Invoice.invoice_date >= month_start, m.Invoice.status != "cancelled"))
    revenue_year = s.scalar(select(func.coalesce(func.sum(m.Invoice.net_amount), 0)).where(
        m.Invoice.invoice_date >= year_start, m.Invoice.status != "cancelled"))
    open_items = finance.open_items(s)
    overdue = [i for i in open_items if i.due_date < today]
    return {
        "revenue_month": Decimal(revenue_month),
        "revenue_year": Decimal(revenue_year),
        "open_receivables": sum((i.open_amount for i in open_items), Decimal(0)),
        "overdue_count": len(overdue),
        "overdue_amount": sum((i.open_amount for i in overdue), Decimal(0)),
        "open_sales_orders": s.scalar(select(func.count()).select_from(m.SalesOrder).where(
            m.SalesOrder.status.in_(["draft", "confirmed", "delivered"]))),
        "open_purchase_orders": s.scalar(select(func.count()).select_from(m.PurchaseOrder).where(
            m.PurchaseOrder.status.in_(["draft", "ordered", "partial"]))),
        "below_minimum": len(inventory.below_minimum(s)),
        "customers": s.scalar(select(func.count()).select_from(m.Customer).where(m.Customer.active.is_(True))),
        "products": s.scalar(select(func.count()).select_from(m.Product).where(m.Product.active.is_(True))),
    }


def monthly_revenue(s: Session, months: int = 12) -> list[tuple[str, Decimal]]:
    today = dt.date.today()
    out = []
    y, mth = today.year, today.month
    for _ in range(months):
        start = dt.date(y, mth, 1)
        end = dt.date(y + (mth == 12), mth % 12 + 1, 1)
        val = s.scalar(select(func.coalesce(func.sum(m.Invoice.net_amount), 0)).where(
            m.Invoice.invoice_date >= start, m.Invoice.invoice_date < end,
            m.Invoice.status != "cancelled"))
        out.append((f"{mth:02d}/{str(y)[2:]}", Decimal(val)))
        mth -= 1
        if mth == 0:
            mth, y = 12, y - 1
    return list(reversed(out))


def _doc_html(s: Session, title: str, number: str, date: dt.date, partner, items,
              totals: tuple, extra: str = "") -> str:
    e = html.escape
    company = get_setting(s, "company_name")
    rows = "".join(
        f"<tr><td>{it.position}</td><td>{e(it.description)}</td>"
        f"<td align='right'>{Decimal(it.quantity):.2f}</td>"
        f"<td align='right'>{money(it.unit_price)}</td>"
        f"<td align='right'>{Decimal(it.tax_rate):.0f} %</td>"
        f"<td align='right'>{money(it.net_amount)}</td></tr>" for it in items)
    net, tax, gross = totals
    return f"""
<html><body style="font-family:Arial; font-size:10pt">
<p style="font-size:8pt">{e(company)} · {e(get_setting(s, 'company_street'))} · {e(get_setting(s, 'company_city'))}</p>
<p>{e(partner.name)}<br>{e(partner.street)}<br>{e(partner.zip)} {e(partner.city)}</p>
<h2>{e(title)} {e(number)}</h2>
<p>Datum: {date:%d.%m.%Y}<br>Kundennr./Lieferantennr.: {e(partner.number)}{extra}</p>
<table width="100%" cellspacing="0" cellpadding="4" border="1" style="border-collapse:collapse">
<tr style="background:#ddd"><th>Pos</th><th>Bezeichnung</th><th>Menge</th><th>Preis</th><th>USt</th><th>Netto</th></tr>
{rows}
<tr><td colspan="5" align="right">Nettobetrag</td><td align="right">{money(net)}</td></tr>
<tr><td colspan="5" align="right">Umsatzsteuer</td><td align="right">{money(tax)}</td></tr>
<tr><td colspan="5" align="right"><b>Gesamtbetrag $</b></td><td align="right"><b>{money(gross)}</b></td></tr>
</table>
<p style="font-size:8pt">USt-IdNr.: {e(get_setting(s, 'company_vat_id'))} · IBAN: {e(get_setting(s, 'company_iban'))}</p>
</body></html>"""


def invoice_html(s: Session, invoice_id: int) -> str:
    inv = s.get(m.Invoice, invoice_id)
    items = inv.sales_order.items if inv.sales_order else []
    extra = f"<br>Zahlbar bis: {inv.due_date:%d.%m.%Y}"
    if inv.sales_order:
        extra += f"<br>Auftrag: {inv.sales_order.number}"
    return _doc_html(s, "Rechnung", inv.number, inv.invoice_date, inv.customer, items,
                     (inv.net_amount, inv.tax_amount, inv.gross_amount), extra)


def sales_order_html(s: Session, order_id: int) -> str:
    so = s.get(m.SalesOrder, order_id)
    return _doc_html(s, "Auftragsbestätigung", so.number, so.order_date, so.customer, so.items,
                     (so.net_total, so.tax_total, so.gross_total))


def purchase_order_html(s: Session, order_id: int) -> str:
    po = s.get(m.PurchaseOrder, order_id)
    return _doc_html(s, "Bestellung", po.number, po.order_date, po.supplier, po.items,
                     (po.net_total, po.tax_total, po.gross_total))

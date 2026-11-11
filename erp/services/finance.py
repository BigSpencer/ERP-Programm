"""Finanzbuchhaltung: doppelte Buchführung, Kontensalden, offene Posten."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from . import BusinessError, audit, context, dec, next_number

# Steuersatz -> (Erlöskonto, USt-Konto)
SALES_ACCOUNTS = {Decimal("19"): ("8400", "1776"), Decimal("7"): ("8300", "1771"),
                  Decimal("0"): ("8100", None)}
# Steuersatz -> (Aufwandskonto, Vorsteuerkonto)
PURCHASE_ACCOUNTS = {Decimal("19"): ("3400", "1576"), Decimal("7"): ("3300", "1571"),
                     Decimal("0"): ("3400", None)}
RECEIVABLES = "1400"
PAYABLES = "1600"
BANK = "1200"


def account(s: Session, number: str) -> m.Account:
    acc = s.scalars(select(m.Account).where(m.Account.number == number)).first()
    if acc is None:
        raise BusinessError(f"Konto {number} existiert nicht")
    return acc


def post(s: Session, description: str, lines: list[tuple[str, object, object]],
         entry_date: dt.date | None = None, reference: str = "") -> m.JournalEntry:
    """Bucht einen Buchungssatz. ``lines`` = [(Kontonummer, Soll, Haben), …]."""
    parsed = []
    for number, debit, credit in lines:
        d, c = dec(debit), dec(credit)
        if d < 0 or c < 0:
            raise BusinessError("Beträge dürfen nicht negativ sein")
        if d == 0 and c == 0:
            continue
        parsed.append((account(s, number), d, c))
    if len(parsed) < 2:
        raise BusinessError("Ein Buchungssatz benötigt mindestens zwei Zeilen")
    total_d = sum(p[1] for p in parsed)
    total_c = sum(p[2] for p in parsed)
    if total_d != total_c:
        raise BusinessError(f"Soll ({total_d:.2f}) und Haben ({total_c:.2f}) sind nicht ausgeglichen")
    entry = m.JournalEntry(number=next_number(s, "journal"), entry_date=entry_date or dt.date.today(),
                           description=description, reference=reference, username=context.username)
    for acc, d, c in parsed:
        entry.lines.append(m.JournalLine(account=acc, debit=d, credit=c))
    s.add(entry)
    s.flush()
    audit(s, "post", "journal", entry.number, description)
    return entry


def _tax_split(items) -> dict[Decimal, tuple[Decimal, Decimal]]:
    """Gruppiert Positionen nach Steuersatz -> (Netto, Steuer)."""
    groups: dict[Decimal, list[Decimal]] = {}
    for it in items:
        # Decimal("19.00") == Decimal("19") und gleicher Hash -> direkt als Schlüssel nutzbar
        g = groups.setdefault(Decimal(it.tax_rate), [Decimal(0), Decimal(0)])
        g[0] += it.net_amount
        g[1] += it.tax_amount
    return {k: (v[0], v[1]) for k, v in groups.items()}


def post_sales_invoice(s: Session, invoice: m.Invoice, items) -> m.JournalEntry:
    lines = [(RECEIVABLES, invoice.gross_amount, 0)]
    for rate, (net, tax) in _tax_split(items).items():
        rev, vat = SALES_ACCOUNTS.get(rate, SALES_ACCOUNTS[Decimal("19")])
        lines.append((rev, 0, net))
        if vat and tax:
            lines.append((vat, 0, tax))
    return post(s, f"Ausgangsrechnung {invoice.number} {invoice.customer.name}", lines,
                invoice.invoice_date, invoice.number)


def post_goods_receipt(s: Session, po: m.PurchaseOrder, received: list[tuple]) -> m.JournalEntry | None:
    """``received`` = [(Position, Menge)] – bucht Wareneingang gegen Verbindlichkeiten."""
    lines: dict[str, Decimal] = {}
    total = Decimal(0)
    for item, qty in received:
        net = (Decimal(qty) * Decimal(item.unit_price)
               * (1 - Decimal(item.discount_pct or 0) / 100)).quantize(Decimal("0.01"))
        tax = (net * Decimal(item.tax_rate) / 100).quantize(Decimal("0.01"))
        exp, vst = PURCHASE_ACCOUNTS.get(Decimal(item.tax_rate), PURCHASE_ACCOUNTS[Decimal("19")])
        lines[exp] = lines.get(exp, Decimal(0)) + net
        if vst and tax:
            lines[vst] = lines.get(vst, Decimal(0)) + tax
        total += net + tax
    if total == 0:
        return None
    entry_lines = [(acc, amt, 0) for acc, amt in lines.items()] + [(PAYABLES, 0, total)]
    return post(s, f"Wareneingang {po.number} {po.supplier.name}", entry_lines, reference=po.number)


def register_payment(s: Session, invoice_id: int, amount, pay_date: dt.date | None = None,
                     bank_account: str = BANK) -> m.Invoice:
    inv = s.get(m.Invoice, invoice_id)
    if inv is None:
        raise BusinessError("Rechnung nicht gefunden")
    if inv.status in ("paid", "cancelled"):
        raise BusinessError("Rechnung ist bereits ausgeglichen oder storniert")
    amount = dec(amount)
    if amount <= 0:
        raise BusinessError("Zahlbetrag muss positiv sein")
    if amount > inv.open_amount:
        raise BusinessError(f"Zahlbetrag übersteigt offenen Betrag ({inv.open_amount:.2f})")
    post(s, f"Zahlungseingang {inv.number} {inv.customer.name}",
         [(bank_account, amount, 0), (RECEIVABLES, 0, amount)], pay_date, inv.number)
    inv.paid_amount = Decimal(inv.paid_amount) + amount
    inv.status = "paid" if inv.open_amount == 0 else "partial"
    s.flush()
    return inv


def balances(s: Session, until: dt.date | None = None):
    """Summen- und Saldenliste: [(Konto, Soll, Haben, Saldo)]."""
    q = (select(m.Account,
                func.coalesce(func.sum(m.JournalLine.debit), 0),
                func.coalesce(func.sum(m.JournalLine.credit), 0))
         .outerjoin(m.JournalLine, m.JournalLine.account_id == m.Account.id)
         .outerjoin(m.JournalEntry, m.JournalEntry.id == m.JournalLine.entry_id))
    if until:
        q = q.where((m.JournalEntry.entry_date <= until) | (m.JournalEntry.id.is_(None)))
    q = q.group_by(m.Account.id).order_by(m.Account.number)
    out = []
    for acc, d, c in s.execute(q):
        d, c = Decimal(d), Decimal(c)
        out.append((acc, d, c, d - c))
    return out


def profit_and_loss(s: Session, start: dt.date, end: dt.date) -> dict:
    q = (select(m.Account.account_type,
                func.coalesce(func.sum(m.JournalLine.credit - m.JournalLine.debit), 0))
         .join(m.JournalLine, m.JournalLine.account_id == m.Account.id)
         .join(m.JournalEntry)
         .where(m.JournalEntry.entry_date.between(start, end),
                m.Account.account_type.in_(["revenue", "expense"]))
         .group_by(m.Account.account_type))
    res = {k: Decimal(v) for k, v in s.execute(q)}
    revenue = res.get("revenue", Decimal(0))
    expense = -res.get("expense", Decimal(0))
    return {"revenue": revenue, "expense": expense, "result": revenue - expense}


def journal(s: Session, limit: int = 500):
    return s.scalars(select(m.JournalEntry).order_by(m.JournalEntry.id.desc()).limit(limit)).all()


def open_items(s: Session):
    return s.scalars(select(m.Invoice).where(m.Invoice.status.in_(["open", "partial"]))
                     .order_by(m.Invoice.due_date)).all()

"""Seiten für Lager, Finanzbuchhaltung, Administration und das Dashboard."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)
from sqlalchemy import select

from .. import models as m
from ..services import BusinessError, auth, dec, finance, get_setting, inventory, reports, set_setting
from .masterdata import AccountsPage, WarehousesPage
from .widgets import (
    DataTable, DateField, Field, FormDialog, ListPage, SearchCombo, dcell, fmt_money, fmt_qty, num, run,
)


def _products(s):
    return [(p.id, f"{p.number} {p.name}") for p in
            s.scalars(select(m.Product).where(m.Product.stock_managed.is_(True), m.Product.active.is_(True))
                      .order_by(m.Product.number))]


def _warehouses(s):
    return [(w.id, f"{w.code} {w.name}") for w in s.scalars(select(m.Warehouse).order_by(m.Warehouse.code))]


# --------------------------------------------------------------------------
# Lager
# --------------------------------------------------------------------------

class StockPage(ListPage):
    title = "Lager – Bestandsübersicht"
    columns = ["Artikel", "Bezeichnung", "ME", "Bestand", "Bestellt", "Mindestbestand", "Hinweis"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Zugang/Abgang", self.manual, primary=True)
        self.add_button("Umlagerung", self.transfer)
        self.add_button("Inventur", self.count)

    def extra_filters(self):
        self.wh = QComboBox()
        self.wh.addItem("Alle Lager", None)
        self._wh_loaded = False
        self.wh.currentIndexChanged.connect(self.refresh)
        self.toolbar.addWidget(self.wh)

    def load(self, s, text):
        if not self._wh_loaded:
            self.wh.blockSignals(True)
            for wid, label in _warehouses(s):
                self.wh.addItem(label, wid)
            self.wh.blockSignals(False)
            self._wh_loaded = True
        rows = []
        for r in inventory.overview(s, self.wh.currentData(), text):
            p = r["product"]
            hint = ""
            if r["stock"] + r["on_order"] < r["min_stock"]:
                hint = "⚠ unter Mindestbestand"
            elif r["stock"] < 0:
                hint = "⚠ negativ"
            rows.append((p.id, [p.number, p.name, p.unit, num(r["stock"], "qty"),
                                num(r["on_order"], "qty"), num(r["min_stock"], "qty"), hint]))
        return rows

    def _dialog(self, title, fields, values=None):
        dlg = FormDialog(self, title, fields, values or {})
        return dlg.values() if dlg.exec() else None

    def _base_fields(self):
        opts = run(self, lambda s: (_products(s), _warehouses(s)))
        if not opts:
            return None
        return opts

    def manual(self):
        opts = self._base_fields()
        if not opts:
            return
        v = self._dialog("Bestandsbuchung", [
            Field("product", "Artikel", "choice", opts[0], required=True),
            Field("warehouse", "Lager", "choice", opts[1], required=True),
            Field("type", "Art", "choice", [("receipt", "Zugang"), ("issue", "Abgang")], required=True),
            Field("qty", "Menge", "decimal", required=True),
            Field("ref", "Referenz/Grund"),
        ], {"product": self.table.selected_id(), "type": "receipt"})
        if v:
            from ..services import dec
            q = dec(v["qty"]) if v["qty"] else 0
            sign = 1 if v["type"] == "receipt" else -1
            if run(self, lambda s: inventory.book(s, v["product"], v["warehouse"], sign * q, v["type"], v["ref"])):
                self.refresh()

    def transfer(self):
        opts = self._base_fields()
        if not opts:
            return
        v = self._dialog("Umlagerung", [
            Field("product", "Artikel", "choice", opts[0], required=True),
            Field("src", "Von Lager", "choice", opts[1], required=True),
            Field("dst", "Nach Lager", "choice", opts[1], required=True),
            Field("qty", "Menge", "decimal", required=True),
        ], {"product": self.table.selected_id()})
        if v and run(self, lambda s: inventory.transfer(s, v["product"], v["src"], v["dst"], v["qty"], "Umlagerung")):
            self.refresh()

    def count(self):
        opts = self._base_fields()
        if not opts:
            return
        v = self._dialog("Inventur – gezählte Menge erfassen", [
            Field("product", "Artikel", "choice", opts[0], required=True),
            Field("warehouse", "Lager", "choice", opts[1], required=True),
            Field("qty", "Gezählte Menge", "decimal", required=True),
        ], {"product": self.table.selected_id()})
        if v:
            diff = run(self, lambda s: inventory.set_inventory(s, v["product"], v["warehouse"], v["qty"]))
            if diff is not None:
                QMessageBox.information(self, "Inventur", f"Differenz gebucht: {fmt_qty(diff)}")
                self.refresh()


class MovementsPage(ListPage):
    title = "Lager – Bewegungen"
    columns = ["Zeitpunkt", "Artikel", "Lager", "Art", "Menge", "Referenz", "Benutzer"]

    def load(self, s, text):
        rows = []
        for mv in inventory.movements(s):
            if text and text.lower() not in f"{mv.product.number} {mv.product.name} {mv.reference}".lower():
                continue
            rows.append((mv.id, [mv.timestamp.strftime("%d.%m.%Y %H:%M") if mv.timestamp else "",
                                 f"{mv.product.number} {mv.product.name}", mv.warehouse.code,
                                 m.MOVEMENT_TYPES.get(mv.movement_type, mv.movement_type),
                                 num(mv.quantity, "qty"), mv.reference, mv.username]))
        return rows


class TabsPage(QWidget):
    """Mehrere Seiten in Registerkarten."""

    def __init__(self, pages: list[tuple[str, QWidget]], parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        for label, page in pages:
            self.tabs.addTab(page, label)
        self.tabs.currentChanged.connect(self.refresh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)

    def refresh(self, *_):
        w = self.tabs.currentWidget()
        if hasattr(w, "refresh"):
            w.refresh()


def inventory_module():
    return TabsPage([("Bestand", StockPage()), ("Bewegungen", MovementsPage()),
                     ("Lagerorte", WarehousesPage())])


# --------------------------------------------------------------------------
# Finanzen
# --------------------------------------------------------------------------

class JournalPage(ListPage):
    title = "Finanzbuchhaltung – Journal"
    columns = ["Beleg", "Datum", "Buchungstext", "Konto", "Soll", "Haben", "Referenz", "Benutzer"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Manuelle Buchung", self.manual, primary=True)

    def load(self, s, text):
        rows = []
        for e in finance.journal(s):
            if text and text.lower() not in f"{e.number} {e.description} {e.reference}".lower():
                continue
            for ln in e.lines:
                rows.append((e.id, [e.number, dcell(e.entry_date), e.description,
                                    f"{ln.account.number} {ln.account.name}",
                                    num(ln.debit) if ln.debit else "", num(ln.credit) if ln.credit else "",
                                    e.reference, e.username]))
        return rows

    def manual(self):
        accounts = run(self, lambda s: [(a.number, f"{a.number} {a.name}") for a in
                                        s.scalars(select(m.Account).where(m.Account.active.is_(True))
                                                  .order_by(m.Account.number))])
        if not accounts:
            return
        dlg = ManualEntryDialog(self, accounts)
        if dlg.exec():
            self.refresh()


class ManualEntryDialog(QDialog):
    """Buchungssatz mit beliebig vielen Soll-/Haben-Zeilen."""

    def __init__(self, parent, accounts):
        super().__init__(parent)
        self.setWindowTitle("Manuelle Buchung")
        self.resize(720, 420)
        self.accounts = accounts
        form = QFormLayout()
        self.date = DateField()
        self.text = QLineEdit()
        self.ref = QLineEdit()
        form.addRow("Datum", self.date)
        form.addRow("Buchungstext *", self.text)
        form.addRow("Referenz", self.ref)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Konto", "Soll", "Haben"])
        self.table.setColumnWidth(0, 360)
        self.table.cellChanged.connect(self._sum)
        add = QPushButton("+ Zeile")
        add.clicked.connect(self._add)
        self.sum = QLabel()
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch(1)
        row.addWidget(self.sum)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.table)
        lay.addLayout(row)
        lay.addWidget(box)
        self._add()
        self._add()

    def _add(self):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setCellWidget(r, 0, SearchCombo(self.accounts, allow_empty=True))
        for c in (1, 2):
            it = QTableWidgetItem("")
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, c, it)

    def _lines(self):
        out = []
        for r in range(self.table.rowCount()):
            acc = self.table.cellWidget(r, 0).value()
            d, c = self.table.item(r, 1).text(), self.table.item(r, 2).text()
            if acc and (d or c):
                out.append((acc, d, c))
        return out

    def _sum(self, *_):
        from ..services import BusinessError, dec
        try:
            d = sum((dec(x[1]) for x in self._lines()), Decimal(0))
            c = sum((dec(x[2]) for x in self._lines()), Decimal(0))
            diff = d - c
            self.sum.setText(f"Soll {fmt_money(d)} · Haben {fmt_money(c)} · "
                             + ("<b style='color:green'>ausgeglichen</b>" if diff == 0
                                else f"<b style='color:#c00'>Differenz {fmt_money(diff)}</b>"))
        except BusinessError:
            self.sum.setText("Ungültige Eingabe")

    def _save(self):
        if not self.text.text().strip():
            QMessageBox.warning(self, "Pflichtfeld", "Bitte einen Buchungstext angeben.")
            return
        lines = self._lines()
        if run(self, lambda s: finance.post(s, self.text.text().strip(), lines, self.date.value(),
                                            self.ref.text().strip())):
            self.accept()


class BalancesPage(ListPage):
    title = "Summen- und Saldenliste"
    columns = ["Konto", "Bezeichnung", "Typ", "Soll", "Haben", "Saldo"]

    def load(self, s, text):
        return [(a.id, [a.number, a.name, m.ACCOUNT_TYPES.get(a.account_type, ""), num(d), num(c), num(b)])
                for a, d, c, b in finance.balances(s)
                if (d or c) and (not text or text.lower() in f"{a.number} {a.name}".lower())]


class OpenItemsPage(ListPage):
    title = "Offene Posten Debitoren"
    columns = ["Rechnung", "Kunde", "Fällig", "Tage überfällig", "Offen"]

    def load(self, s, text):
        today = dt.date.today()
        return [(i.id, [i.number, i.customer.name, dcell(i.due_date),
                        num(max((today - i.due_date).days, 0), "qty"), num(i.open_amount)])
                for i in finance.open_items(s)
                if not text or text.lower() in f"{i.number} {i.customer.name}".lower()]


class PnLPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        today = dt.date.today()
        self.start = DateField(today.replace(month=1, day=1))
        self.end = DateField(today)
        btn = QPushButton("Auswerten")
        btn.setObjectName("primary")
        btn.clicked.connect(self.refresh)
        head = QHBoxLayout()
        head.addWidget(QLabel("Zeitraum"))
        head.addWidget(self.start)
        head.addWidget(QLabel("bis"))
        head.addWidget(self.end)
        head.addWidget(btn)
        head.addStretch(1)
        self.result = QLabel()
        self.result.setObjectName("pnl")
        self.result.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay = QVBoxLayout(self)
        title = QLabel("Gewinn- und Verlustrechnung")
        title.setObjectName("pageTitle")
        lay.addWidget(title)
        lay.addLayout(head)
        lay.addWidget(self.result, 1)

    def refresh(self):
        r = run(self, lambda s: finance.profit_and_loss(s, self.start.value(), self.end.value()))
        if r and r is not True:
            color = "#1a7f37" if r["result"] >= 0 else "#c62828"
            self.result.setText(
                f"<table cellpadding=6 style='font-size:13pt'>"
                f"<tr><td>Erlöse</td><td align=right>{fmt_money(r['revenue'])} EUR</td></tr>"
                f"<tr><td>Aufwand</td><td align=right>{fmt_money(r['expense'])} EUR</td></tr>"
                f"<tr><td><b>Ergebnis</b></td><td align=right style='color:{color}'><b>"
                f"{fmt_money(r['result'])} EUR</b></td></tr></table>")


def finance_module():
    return TabsPage([("Journal", JournalPage()), ("Summen && Salden", BalancesPage()),
                     ("Offene Posten", OpenItemsPage()), ("GuV", PnLPage()),
                     ("Kontenplan", AccountsPage())])


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------

class UsersPage(ListPage):
    title = "Benutzer & Rollen"
    columns = ["Benutzer", "Name", "Rolle", "Aktiv"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neu", lambda: self._open(None), primary=True)
        self.add_button("Bearbeiten", lambda: (sel := self.require_selection()) and self._open(sel))

    def load(self, s, text):
        return [(u.id, [u.username, u.full_name, m.ROLES.get(u.role, u.role), "ja" if u.active else "nein"])
                for u in s.scalars(select(m.User).order_by(m.User.username))
                if not text or text.lower() in f"{u.username} {u.full_name}".lower()]

    def on_double_click(self, *_):
        sel = self.table.selected_id()
        if sel:
            self._open(sel)

    def _open(self, uid):
        values = {}
        if uid:
            values = run(self, lambda s: (lambda u: {"username": u.username, "full_name": u.full_name,
                                                     "role": u.role, "active": u.active})(s.get(m.User, uid)))
            if not values:
                return
        dlg = FormDialog(self, "Benutzer", [
            Field("username", "Benutzername", required=True),
            Field("full_name", "Name"),
            Field("role", "Rolle", "choice", list(m.ROLES.items()), required=True),
            Field("password", "Passwort" + (" (leer = unverändert)" if uid else ""), "password",
                  required=not uid),
            Field("active", "Aktiv", "bool"),
        ], values)
        if dlg.exec():
            v = dlg.values()
            if run(self, lambda s: auth.save_user(s, uid, v["username"], v["full_name"], v["role"],
                                                  v["active"], v["password"] or None)):
                self.refresh()


class AuditPage(ListPage):
    title = "Änderungsprotokoll"
    columns = ["Zeitpunkt", "Benutzer", "Aktion", "Objekt", "ID", "Details"]

    def load(self, s, text):
        q = select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(1000)
        return [(a.id, [a.timestamp.strftime("%d.%m.%Y %H:%M:%S") if a.timestamp else "", a.username,
                        a.action, a.entity, a.entity_id, a.details])
                for a in s.scalars(q)
                if not text or text.lower() in f"{a.username} {a.entity} {a.entity_id} {a.details}".lower()]


COMPANY_KEYS = [("company_name", "Firmenname"), ("company_street", "Straße"),
                ("company_city", "PLZ / Ort"), ("company_vat_id", "USt-IdNr."), ("company_iban", "IBAN"),
                ("vehicle_vat_rate", "USt-Satz Fahrzeuge in % (0 = ohne USt)")]


class CompanyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        title = QLabel("Firmendaten (erscheinen auf Belegen)")
        title.setObjectName("pageTitle")
        lay.addWidget(title)
        form = QFormLayout()
        self.edits = {}
        for key, label in COMPANY_KEYS:
            self.edits[key] = QLineEdit()
            form.addRow(label, self.edits[key])
        lay.addLayout(form)
        btn = QPushButton("Speichern")
        btn.setObjectName("primary")
        btn.clicked.connect(self.save)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)

    def refresh(self):
        vals = run(self, lambda s: {k: get_setting(s, k) for k, _ in COMPANY_KEYS})
        if vals and vals is not True:
            for k, v in vals.items():
                self.edits[k].setText(v)

    def save(self):
        vals = {k: e.text().strip() for k, e in self.edits.items()}
        try:
            rate = dec(vals.get("vehicle_vat_rate") or "0")
            if not 0 <= rate < 100:
                raise BusinessError("USt-Satz muss zwischen 0 und 99 liegen")
        except BusinessError as exc:
            QMessageBox.warning(self, "Firmendaten", str(exc))
            return
        run(self, lambda s: [set_setting(s, k, v) for k, v in vals.items()], success="Gespeichert.")


def admin_module():
    return TabsPage([("Benutzer", UsersPage()), ("Firmendaten", CompanyPage()),
                     ("Protokoll", AuditPage())])


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

class BarChart(QWidget):
    def __init__(self, parent=None, title="Umsatz netto je Monat (EUR)"):
        super().__init__(parent)
        self.title = title
        self.data: list[tuple[str, Decimal]] = []
        self.setMinimumHeight(220)

    def set_data(self, data):
        self.data = data
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor("#6b7280"))
        p.drawText(8, 16, self.title)
        if not self.data:
            return
        top, bottom, left = 30, 26, 10
        maxv = max((float(v) for _, v in self.data), default=0) or 1
        n = len(self.data)
        slot = (w - left * 2) / n
        bw = slot * 0.6
        for i, (label, v) in enumerate(self.data):
            x = left + i * slot + (slot - bw) / 2
            bh = (h - top - bottom) * float(v) / maxv
            p.fillRect(QRectF(x, h - bottom - bh, bw, bh), QColor("#0a6ed1"))
            p.setPen(QColor("#6b7280"))
            p.drawText(QRectF(left + i * slot, h - bottom + 4, slot, 18),
                       Qt.AlignmentFlag.AlignHCenter, label)
            if v:
                p.drawText(QRectF(left + i * slot, h - bottom - bh - 16, slot, 14),
                           Qt.AlignmentFlag.AlignHCenter, f"{float(v) / 1000:.1f}k" if v >= 1000 else f"{float(v):.0f}")
        p.setPen(QPen(QColor("#d1d5db")))
        p.drawLine(left, h - bottom, w - left, h - bottom)


class Tile(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        t = QLabel(title)
        t.setObjectName("tileTitle")
        self.value = QLabel("–")
        self.value.setObjectName("tileValue")
        self.sub = QLabel("")
        self.sub.setObjectName("hint")
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)


class DashboardPage(QWidget):
    TILES = [
        ("revenue_month", "Umsatz lfd. Monat", "money"),
        ("revenue_year", "Umsatz lfd. Jahr", "money"),
        ("open_receivables", "Offene Forderungen", "money"),
        ("overdue_amount", "Überfällig", "money"),
        ("open_sales_orders", "Offene Aufträge", "int"),
        ("open_purchase_orders", "Offene Bestellungen", "int"),
        ("below_minimum", "Artikel unter Mindestbestand", "int"),
        ("customers", "Aktive Kunden", "int"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.greeting = QLabel("Übersicht")
        self.greeting.setObjectName("pageTitle")
        lay.addWidget(self.greeting)
        grid = QGridLayout()
        self.tiles = {}
        for i, (key, title, _) in enumerate(self.TILES):
            tile = Tile(title)
            self.tiles[key] = tile
            grid.addWidget(tile, i // 4, i % 4)
        lay.addLayout(grid)
        self.chart = BarChart()
        lay.addWidget(self.chart)
        lower = QHBoxLayout()
        self.overdue = DataTable(["Rechnung", "Kunde", "Fällig", "Offen"])
        self.low = DataTable(["Artikel", "Bezeichnung", "Bestand", "Mindestbestand"])
        for title, table in (("Überfällige Rechnungen", self.overdue), ("Nachschubbedarf", self.low)):
            box = QVBoxLayout()
            lbl = QLabel(title)
            lbl.setObjectName("section")
            box.addWidget(lbl)
            box.addWidget(table)
            lower.addLayout(box)
        lay.addLayout(lower, 1)

    def refresh(self):
        def load(s):
            today = dt.date.today()
            return (reports.kpis(s), reports.monthly_revenue(s),
                    [(i.id, [i.number, i.customer.name, dcell(i.due_date), num(i.open_amount)])
                     for i in finance.open_items(s) if i.due_date < today],
                    [(r["product"].id, [r["product"].number, r["product"].name, num(r["stock"], "qty"),
                                        num(r["min_stock"], "qty")]) for r in inventory.below_minimum(s)])

        res = run(self, load)
        if not res or res is True:
            return
        k, monthly, overdue, low = res
        for key, _, kind in self.TILES:
            v = k[key]
            self.tiles[key].value.setText(f"{fmt_money(v)} €" if kind == "money" else str(v))
        self.tiles["overdue_amount"].sub.setText(f"{k['overdue_count']} Rechnung(en)")
        self.chart.set_data(monthly)
        self.overdue.fill(overdue)
        self.low.fill(low)

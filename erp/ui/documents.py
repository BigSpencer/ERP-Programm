"""Vertriebs- und Einkaufsbelege: Aufträge, Rechnungen, Bestellungen."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from sqlalchemy import select

from .. import models as m
from ..services import BusinessError, dec, documents, finance, reports
from .masterdata import all_options
from .widgets import (
    DateField, Field, FormDialog, ListPage, SearchCombo, confirm, dcell, fmt_money, fmt_qty,
    num, output_menu_button, run,
)

ITEM_COLS = ["Artikel", "Beschreibung", "Menge", "Preis netto", "Rabatt %", "USt %", "Netto"]


class DocumentDialog(QDialog):
    """Erfassung eines Kundenauftrags (kind='sales') oder einer Bestellung (kind='purchase')."""

    def __init__(self, parent, kind: str, order_id: int | None = None):
        super().__init__(parent)
        self.kind, self.order_id = kind, order_id
        self.sales = kind == "sales"
        self.setWindowTitle(("Kundenauftrag" if self.sales else "Bestellung")
                            + (" bearbeiten" if order_id else " anlegen"))
        self.resize(980, 620)
        self.version = None
        self.loaded = False
        data = run(parent, self._load)
        if not data:
            return
        self.loaded = True
        opts, header, items, editable = data
        self.products = opts["products"]
        self.editable = editable

        form = QFormLayout()
        partners = opts["customers" if self.sales else "suppliers"]
        self.partner = SearchCombo(partners, allow_empty=True)
        self.partner.set_value(header.get("partner_id"))
        self.order_date = DateField(header.get("order_date"))
        self.date2 = DateField(header.get("date2"), optional=True)
        self.warehouse = SearchCombo(opts["warehouses"])
        self.warehouse.set_value(header.get("warehouse_id"))
        self.reference = QLineEdit(header.get("customer_reference", ""))
        self.notes = QLineEdit(header.get("notes", ""))
        if header.get("number"):
            form.addRow("Belegnummer", QLabel(f"<b>{header['number']}</b> – {header['status_text']}"))
        form.addRow("Kunde *" if self.sales else "Lieferant *", self.partner)
        form.addRow("Belegdatum", self.order_date)
        form.addRow("Liefertermin" if self.sales else "Erwartet am", self.date2)
        form.addRow("Lager", self.warehouse)
        if self.sales:
            form.addRow("Kundenreferenz", self.reference)
        form.addRow("Notiz", self.notes)

        self.table = QTableWidget(0, len(ITEM_COLS))
        self.table.setHorizontalHeaderLabels(ITEM_COLS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 260)
        self.table.verticalHeader().setVisible(False)
        self.table.cellChanged.connect(self._recalc)

        btns = QHBoxLayout()
        self.add_btn = QPushButton("+ Position")
        self.add_btn.clicked.connect(lambda: self._add_row())
        self.del_btn = QPushButton("− Position")
        self.del_btn.clicked.connect(self._del_row)
        btns.addWidget(self.add_btn)
        btns.addWidget(self.del_btn)
        btns.addStretch(1)
        self.totals = QLabel()
        self.totals.setObjectName("totals")
        btns.addWidget(self.totals)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        if not editable:
            box.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
            for w in (self.partner, self.order_date, self.date2, self.warehouse, self.reference,
                      self.notes, self.add_btn, self.del_btn):
                w.setEnabled(False)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.table, 1)
        lay.addLayout(btns)
        lay.addWidget(box)

        for it in items:
            self._add_row(it)
        if not items:
            self._add_row()
        self._recalc()

    # -- Daten -------------------------------------------------------------
    def _load(self, s):
        opts = all_options(s)
        header, items, editable = {}, [], True
        wh = s.scalars(select(m.Warehouse).order_by(m.Warehouse.id)).first()
        header["warehouse_id"] = wh.id if wh else None
        if self.order_id:
            cls = m.SalesOrder if self.sales else m.PurchaseOrder
            o = s.get(cls, self.order_id)
            self.version = o.version
            status_map = m.SALES_STATUS if self.sales else m.PURCHASE_STATUS
            header = {
                "number": o.number, "status_text": status_map[o.status],
                "partner_id": o.customer_id if self.sales else o.supplier_id,
                "order_date": o.order_date,
                "date2": o.delivery_date if self.sales else o.expected_date,
                "warehouse_id": o.warehouse_id, "notes": o.notes,
                "customer_reference": getattr(o, "customer_reference", ""),
            }
            items = [{"product_id": i.product_id, "description": i.description,
                      "quantity": i.quantity, "unit_price": i.unit_price,
                      "discount_pct": i.discount_pct, "tax_rate": i.tax_rate} for i in o.items]
            editable = o.status == "draft"
            # Inaktive, aber verwendete Artikel trotzdem anzeigen
            for i in o.items:
                if i.product_id and i.product_id not in opts["products"]:
                    opts["products"][i.product_id] = i.product
        return opts, header, items, editable

    def _product_combo(self, value=None) -> SearchCombo:
        combo = SearchCombo([(pid, f"{p.number} {p.name}") for pid, p in self.products.items()],
                            allow_empty=True)
        combo.set_value(value)
        combo.setEnabled(self.editable)
        combo.currentIndexChanged.connect(lambda _=None, c=combo: self._product_changed(c))
        return combo

    def _add_row(self, data: dict | None = None):
        data = data or {}
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setCellWidget(r, 0, self._product_combo(data.get("product_id")))
        values = [data.get("description", ""), fmt_qty(data.get("quantity", 1)),
                  fmt_money(data.get("unit_price", 0)), fmt_qty(data.get("discount_pct", 0)),
                  fmt_qty(data.get("tax_rate", 19)), ""]
        for c, v in enumerate(values, start=1):
            item = QTableWidgetItem(v)
            if c >= 2:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if c == 6:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self._recalc()

    def _del_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._recalc()

    def _product_changed(self, combo):
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 0) is combo:
                p = self.products.get(combo.value())
                if p is None:
                    return
                self.table.blockSignals(True)
                self.table.item(r, 1).setText(p.name)
                price = p.sales_price if self.sales else p.purchase_price
                self.table.item(r, 3).setText(fmt_money(price))
                self.table.item(r, 5).setText(fmt_qty(p.tax_rate))
                self.table.blockSignals(False)
                self._recalc()
                return

    def _row_values(self, r) -> dict:
        def cell(c):
            it = self.table.item(r, c)
            return it.text() if it else ""
        return {"product_id": self.table.cellWidget(r, 0).value(), "description": cell(1),
                "quantity": cell(2), "unit_price": cell(3), "discount_pct": cell(4),
                "tax_rate": cell(5)}

    def _recalc(self, *_):
        net = tax = Decimal(0)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            v = self._row_values(r)
            try:
                line = dec(v["quantity"]) * dec(v["unit_price"]) * (1 - dec(v["discount_pct"]) / 100)
                line = line.quantize(Decimal("0.01"))
                net += line
                tax += (line * dec(v["tax_rate"]) / 100).quantize(Decimal("0.01"))
                self.table.item(r, 6).setText(fmt_money(line))
            except BusinessError:
                self.table.item(r, 6).setText("?")
        self.table.blockSignals(False)
        self.totals.setText(f"Netto {fmt_money(net)}  ·  USt {fmt_money(tax)}  ·  "
                            f"<b>Brutto {fmt_money(net + tax)} EUR</b>")

    def _save(self):
        items = [v for v in (self._row_values(r) for r in range(self.table.rowCount()))
                 if v["product_id"] or v["description"].strip()]
        header = {"order_date": self.order_date.value(), "warehouse_id": self.warehouse.value(),
                  "notes": self.notes.text()}
        if self.sales:
            header.update(customer_id=self.partner.value(), delivery_date=self.date2.value(),
                          customer_reference=self.reference.text())
            fn = documents.save_sales_order
        else:
            header.update(supplier_id=self.partner.value(), expected_date=self.date2.value())
            fn = documents.save_purchase_order
        if run(self, lambda s: fn(s, self.order_id, header, items, self.version)):
            self.accept()


# --------------------------------------------------------------------------
# Listen
# --------------------------------------------------------------------------

class _StatusFilterPage(ListPage):
    status_map: dict = {}

    def extra_filters(self):
        self.status_filter = QComboBox()
        self.status_filter.addItem("Alle Status", None)
        for k, v in self.status_map.items():
            self.status_filter.addItem(v, k)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.toolbar.addWidget(self.status_filter)


class SalesOrdersPage(_StatusFilterPage):
    title = "Vertrieb – Kundenaufträge"
    columns = ["Auftrag", "Datum", "Kunde", "Referenz", "Netto", "Brutto", "Status", "Erfasst von"]
    status_map = m.SALES_STATUS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neuer Auftrag", self.create, primary=True)
        self.add_button("Öffnen", self.open)
        self.add_button("Bestätigen", self.confirm_order)
        self.add_button("Liefern", self.deliver)
        self.add_button("Fakturieren", self.invoice)
        self.add_button("Stornieren", self.cancel)
        self.toolbar.addWidget(output_menu_button(self, self._html))

    def load(self, s, text):
        return [(o.id, [o.number, dcell(o.order_date), o.customer.name, o.customer_reference,
                        num(o.net_total), num(o.gross_total), m.SALES_STATUS[o.status], o.created_by])
                for o in documents.list_sales_orders(s, self.status_filter.currentData(), text)]

    def on_double_click(self, *_):
        self.open()

    def create(self):
        dlg = DocumentDialog(self, "sales")
        if dlg.loaded and dlg.exec():
            self.refresh()

    def open(self):
        sel = self.require_selection()
        if sel:
            dlg = DocumentDialog(self, "sales", sel)
            if dlg.loaded and dlg.exec():
                self.refresh()

    def confirm_order(self):
        sel = self.require_selection()
        if not sel:
            return
        warnings = run(self, lambda s: documents.confirm_sales_order(s, sel))
        if warnings is not None:
            if isinstance(warnings, list) and warnings:
                QMessageBox.warning(self, "Verfügbarkeit", "Auftrag bestätigt, aber Bestand knapp:\n\n"
                                    + "\n".join(warnings))
            self.refresh()

    def deliver(self):
        sel = self.require_selection()
        if sel and confirm(self, "Lieferung buchen? Der Bestand wird reduziert."):
            if run(self, lambda s: documents.deliver_sales_order(s, sel)):
                self.refresh()

    def invoice(self):
        sel = self.require_selection()
        if sel and confirm(self, "Rechnung erstellen und buchen?"):
            num_ = run(self, lambda s: documents.invoice_sales_order(s, sel).number)
            if num_:
                QMessageBox.information(self, "Rechnung", f"Rechnung {num_} wurde erstellt und gebucht.")
                self.refresh()

    def cancel(self):
        sel = self.require_selection()
        if sel and confirm(self, "Auftrag stornieren?"):
            if run(self, lambda s: documents.cancel_sales_order(s, sel)):
                self.refresh()

    def _html(self):
        sel = self.require_selection()
        if sel:
            return run(self, lambda s: (reports.sales_order_html(s, sel),
                                        f"{s.get(m.SalesOrder, sel).number}.pdf"))
        return None


class InvoicesPage(_StatusFilterPage):
    title = "Ausgangsrechnungen"
    columns = ["Rechnung", "Datum", "Fällig", "Kunde", "Netto", "Brutto", "Bezahlt", "Offen", "Status"]
    status_map = m.INVOICE_STATUS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Zahlung erfassen", self.payment, primary=True)
        self.toolbar.addWidget(output_menu_button(self, self._html))

    def load(self, s, text):
        today = dt.date.today()
        rows = []
        for i in documents.list_invoices(s, self.status_filter.currentData(), text):
            status = m.INVOICE_STATUS[i.status]
            if i.status in ("open", "partial") and i.due_date < today:
                status += " (überfällig)"
            rows.append((i.id, [i.number, dcell(i.invoice_date), dcell(i.due_date), i.customer.name,
                                num(i.net_amount), num(i.gross_amount), num(i.paid_amount),
                                num(i.open_amount), status]))
        return rows

    def payment(self):
        sel = self.require_selection()
        if not sel:
            return
        info = run(self, lambda s: s.get(m.Invoice, sel).open_amount)
        if info is None:
            return
        dlg = FormDialog(self, "Zahlungseingang", [
            Field("amount", "Betrag EUR", "money", required=True),
            Field("date", "Zahlungsdatum", "date"),
            Field("account", "Geldkonto", "choice", [("1200", "1200 Bank"), ("1000", "1000 Kasse")],
                  required=True),
        ], {"amount": info, "account": "1200"})
        if dlg.exec():
            v = dlg.values()
            if run(self, lambda s: finance.register_payment(s, sel, v["amount"], v["date"], v["account"])):
                self.refresh()

    def _html(self):
        sel = self.require_selection()
        if sel:
            return run(self, lambda s: (reports.invoice_html(s, sel),
                                        f"{s.get(m.Invoice, sel).number}.pdf"))
        return None


class PurchaseOrdersPage(_StatusFilterPage):
    title = "Einkauf – Bestellungen"
    columns = ["Bestellung", "Datum", "Lieferant", "Erwartet", "Netto", "Brutto", "Status", "Erfasst von"]
    status_map = m.PURCHASE_STATUS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neue Bestellung", self.create, primary=True)
        self.add_button("Öffnen", self.open)
        self.add_button("Bestellen", self.place)
        self.add_button("Wareneingang", self.receive)
        self.add_button("Stornieren", self.cancel)
        self.add_button("Bestellvorschläge", self.proposals)
        self.toolbar.addWidget(output_menu_button(self, self._html))

    def load(self, s, text):
        return [(o.id, [o.number, dcell(o.order_date), o.supplier.name, dcell(o.expected_date),
                        num(o.net_total), num(o.gross_total), m.PURCHASE_STATUS[o.status], o.created_by])
                for o in documents.list_purchase_orders(s, self.status_filter.currentData(), text)]

    def on_double_click(self, *_):
        self.open()

    def create(self):
        dlg = DocumentDialog(self, "purchase")
        if dlg.loaded and dlg.exec():
            self.refresh()

    def open(self):
        sel = self.require_selection()
        if sel:
            dlg = DocumentDialog(self, "purchase", sel)
            if dlg.loaded and dlg.exec():
                self.refresh()

    def place(self):
        sel = self.require_selection()
        if sel and run(self, lambda s: documents.place_purchase_order(s, sel)):
            self.refresh()

    def receive(self):
        sel = self.require_selection()
        if not sel:
            return
        items = run(self, lambda s: [(i.id, f"Pos. {i.position}: {i.description}", i.open_qty)
                                     for i in s.get(m.PurchaseOrder, sel).items if i.open_qty > 0])
        if not items or items is True:
            QMessageBox.information(self, "Wareneingang", "Keine offenen Positionen.")
            return
        fields = [Field(str(iid), f"{label} (offen {fmt_qty(open_)})", "decimal") for iid, label, open_ in items]
        dlg = FormDialog(self, "Wareneingang erfassen", fields, {str(i): q for i, _, q in items})
        if dlg.exec():
            qty = {int(k): v for k, v in dlg.values().items()}
            if run(self, lambda s: documents.receive_purchase_order(s, sel, qty)):
                self.refresh()

    def cancel(self):
        sel = self.require_selection()
        if sel and confirm(self, "Bestellung stornieren?"):
            if run(self, lambda s: documents.cancel_purchase_order(s, sel)):
                self.refresh()

    def proposals(self):
        res = run(self, lambda s: [p.number for p in documents.create_reorder_proposals(s)])
        if res is not None:
            if res and res is not True:
                QMessageBox.information(self, "Bestellvorschläge",
                                        "Angelegte Bestellentwürfe:\n" + "\n".join(res))
            else:
                QMessageBox.information(self, "Bestellvorschläge",
                                        "Kein Bedarf (oder Artikel ohne Standardlieferant).")
            self.refresh()

    def _html(self):
        sel = self.require_selection()
        if sel:
            return run(self, lambda s: (reports.purchase_order_html(s, sel),
                                        f"{s.get(m.PurchaseOrder, sel).number}.pdf"))
        return None

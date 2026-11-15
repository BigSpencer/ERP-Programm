"""Stammdaten-Seiten: Kunden, Lieferanten, Artikel, Lager, Kontenplan."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QCheckBox, QDialog, QMessageBox
from sqlalchemy import select

from .. import models as m
from ..services import masterdata
from .widgets import Field, FormDialog, ListPage, num, run

COUNTRIES = [(c, c) for c in ["DE", "AT", "CH", "FR", "NL", "BE", "IT", "ES", "PL", "CZ", "DK", "LU",
                              "GB", "US"]]

PARTNER_FIELDS = [
    Field("number", "Nummer (leer = automatisch)"),
    Field("name", "Name", required=True),
    Field("street", "Straße"),
    Field("zip", "PLZ"),
    Field("city", "Ort"),
    Field("country", "Land", "choice", COUNTRIES, required=True),
    Field("email", "E-Mail"),
    Field("phone", "Telefon"),
    Field("vat_id", "USt-IdNr."),
    Field("payment_terms_days", "Zahlungsziel (Tage)", "int"),
]


def _supplier_options(s):
    return [(x.id, f"{x.number} {x.name}") for x in masterdata.search(s, "supplier", only_active=True)]


class MasterDataPage(ListPage):
    entity = ""
    noun = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neu", self.create, primary=True)
        self.add_button("Bearbeiten", self.edit)
        self.add_button("Löschen", self.remove)

    def extra_filters(self):
        self.only_active = QCheckBox("nur aktive")
        self.only_active.setChecked(True)
        self.only_active.toggled.connect(self.refresh)
        self.toolbar.addWidget(self.only_active)

    def fields(self, s) -> list[Field]:
        raise NotImplementedError

    def row(self, obj) -> list:
        raise NotImplementedError

    def load(self, s, text):
        return [(o.id, self.row(o)) for o in
                masterdata.search(s, self.entity, text, self.only_active.isChecked())]

    def on_double_click(self, *_):
        self.edit()

    def _open(self, obj_id):
        def prepare(s):
            fields = self.fields(s)
            values, version = {}, None
            if obj_id:
                obj = s.get(masterdata.ENTITIES[self.entity], obj_id)
                values = {f.key: getattr(obj, f.key) for f in fields}
                for f in fields:  # Dezimalwerte in Auswahllisten (USt-Satz) als Text
                    if f.kind == "choice" and isinstance(values[f.key], Decimal):
                        values[f.key] = f"{values[f.key]:.2f}"
                version = obj.version
            return fields, values, version

        res = run(self, prepare)
        if not res:
            return
        fields, values, version = res
        dlg = FormDialog(self, f"{self.noun} {'bearbeiten' if obj_id else 'anlegen'}", fields, values)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.values()
            if run(self, lambda s: masterdata.save(s, self.entity, obj_id, data, version)):
                self.refresh()
                return

    def create(self):
        self._open(None)

    def edit(self):
        sel = self.require_selection()
        if sel:
            self._open(sel)

    def remove(self):
        sel = self.require_selection()
        if not sel:
            return
        if QMessageBox.question(self, "Löschen", f"{self.noun} wirklich löschen?") \
                != QMessageBox.StandardButton.Yes:
            return
        res = run(self, lambda s: masterdata.delete(s, self.entity, sel))
        if res:
            QMessageBox.information(self, "Löschen", f"{self.noun} {res}.")
            self.refresh()


class CustomersPage(MasterDataPage):
    title = "Kunden"
    entity, noun = "customer", "Kunde"
    columns = ["Nr.", "Name", "PLZ", "Ort", "Land", "E-Mail", "Telefon", "Kreditlimit", "Aktiv"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Fahrzeughistorie", self.history)

    def history(self):
        sel = self.require_selection()
        if sel:
            from .dealer import CustomerHistoryDialog
            CustomerHistoryDialog(self, sel).exec()

    def fields(self, s):
        return PARTNER_FIELDS + [Field("credit_limit", "Kreditlimit EUR (0 = keins)", "money"),
                                 Field("notes", "Notizen", "multiline"),
                                 Field("active", "Aktiv", "bool")]

    def row(self, c):
        return [c.number, c.name, c.zip, c.city, c.country, c.email, c.phone,
                num(c.credit_limit), "ja" if c.active else "nein"]


class SuppliersPage(MasterDataPage):
    title = "Lieferanten"
    entity, noun = "supplier", "Lieferant"
    columns = ["Nr.", "Name", "PLZ", "Ort", "Land", "E-Mail", "Telefon", "Aktiv"]

    def fields(self, s):
        return PARTNER_FIELDS + [Field("notes", "Notizen", "multiline"),
                                 Field("active", "Aktiv", "bool")]

    def row(self, c):
        return [c.number, c.name, c.zip, c.city, c.country, c.email, c.phone,
                "ja" if c.active else "nein"]


class ProductsPage(MasterDataPage):
    title = "Artikel / Material"
    entity, noun = "product", "Artikel"
    columns = ["Nr.", "Bezeichnung", "ME", "VK netto", "EK netto", "USt %", "Mindestbestand",
               "Lieferant", "Aktiv"]

    def fields(self, s):
        return [
            Field("number", "Nummer (leer = automatisch)"),
            Field("name", "Bezeichnung", required=True),
            Field("description", "Beschreibung", "multiline"),
            Field("unit", "Mengeneinheit", "choice", [(u, u) for u in m.UNITS], required=True),
            Field("sales_price", "Verkaufspreis netto", "money"),
            Field("purchase_price", "Einkaufspreis netto", "money"),
            Field("tax_rate", "USt-Satz", "choice",
                  [("19.00", "19 %"), ("7.00", "7 %"), ("0.00", "0 %")], required=True),
            Field("min_stock", "Mindestbestand", "decimal"),
            Field("stock_managed", "Bestandsgeführt", "bool"),
            Field("default_supplier_id", "Standardlieferant", "choice", _supplier_options(s)),
            Field("active", "Aktiv", "bool"),
        ]

    def row(self, p):
        return [p.number, p.name, p.unit, num(p.sales_price), num(p.purchase_price),
                num(p.tax_rate, "qty"), num(p.min_stock, "qty"),
                p.default_supplier.name if p.default_supplier else "", "ja" if p.active else "nein"]


class WarehousesPage(MasterDataPage):
    title = "Lagerorte"
    entity, noun = "warehouse", "Lager"
    columns = ["Kürzel", "Bezeichnung"]

    def extra_filters(self):
        self.only_active = QCheckBox()
        self.only_active.setVisible(False)

    def fields(self, s):
        return [Field("code", "Kürzel", required=True), Field("name", "Bezeichnung", required=True)]

    def row(self, w):
        return [w.code, w.name]


class AccountsPage(MasterDataPage):
    title = "Kontenplan"
    entity, noun = "account", "Konto"
    columns = ["Konto", "Bezeichnung", "Typ", "Aktiv"]

    def fields(self, s):
        return [Field("number", "Kontonummer", required=True),
                Field("name", "Bezeichnung", required=True),
                Field("account_type", "Kontotyp", "choice", list(m.ACCOUNT_TYPES.items()), required=True),
                Field("active", "Aktiv", "bool")]

    def row(self, a):
        return [a.number, a.name, m.ACCOUNT_TYPES.get(a.account_type, a.account_type),
                "ja" if a.active else "nein"]


def all_options(s):
    """Optionen für Belegdialoge."""
    return {
        "customers": [(c.id, f"{c.number} {c.name}") for c in masterdata.search(s, "customer", only_active=True)],
        "suppliers": [(c.id, f"{c.number} {c.name}") for c in masterdata.search(s, "supplier", only_active=True)],
        "warehouses": [(w.id, f"{w.code} {w.name}") for w in s.scalars(select(m.Warehouse).order_by(m.Warehouse.code))],
        "products": {p.id: p for p in masterdata.search(s, "product", only_active=True)},
    }

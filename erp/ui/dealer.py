"""Autohaus-Oberfläche: Cockpit, Fahrzeugbestand, Ausstellung, Katalog, Bestellungen, Verkauf, Ankauf."""
from __future__ import annotations

import datetime as dt

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QVBoxLayout, QWidget,
)

from .. import models as m
from ..services import dealer, get_setting, masterdata
from .modules import BarChart, TabsPage, Tile
from .widgets import (
    DataTable, Field, FormDialog, ListPage, dcell, fmt_money, num, output_menu_button, run,
)

CATEGORIES = [(c, c) for c in m.VEHICLE_CATEGORIES]
CONDITIONS = list(m.VEHICLE_CONDITION.items())
PAYMENTS = list(m.PAYMENT_ACCOUNTS.items())


def _customers(s):
    return [(c.id, f"{c.number} {c.name}") for c in masterdata.search(s, "customer", only_active=True)]


def _suppliers(s):
    return [(c.id, f"{c.number} {c.name}") for c in masterdata.search(s, "supplier", only_active=True)]


def _models(s):
    models = sorted(dealer.search_models(s, only_active=True), key=lambda x: (x.model.lower(), x.make.lower()))
    return [(x.id, f"{x.model} – {x.make}  ·  VK {fmt_money(x.sales_price)} €") for x in models]


def _available(s, exclude_status=()):
    return [(v.id, f"{v.label}  ·  {m.VEHICLE_STATUS[v.status]}  ·  VK {fmt_money(v.list_price)} €")
            for v in dealer.available_vehicles(s) if v.status not in exclude_status]


def _ask(parent, title, fields, values=None):
    dlg = FormDialog(parent, title, fields, values or {})
    return dlg.values() if dlg.exec() == QDialog.DialogCode.Accepted else None


def _int_cell(v):
    return (f"{v:,}".replace(",", "."), v or 0)


def vehicle_fields(s, new: bool, sold=False) -> list[Field]:
    fields = [
        Field("model_id", "Katalogmodell", "choice", _models(s)),
        Field("make", "Hersteller", hint="leer = aus Katalog"),
        Field("model", "Modell", hint="leer = aus Katalog"),
        Field("category", "Klasse", "choice", CATEGORIES),
        Field("condition", "Zustand", "choice", CONDITIONS, required=True),
        Field("color", "Farbe"),
        Field("vin", "Fahrgestellnummer (FIN)"),
        Field("plate", "Kennzeichen"),
        Field("year", "Baujahr", "int", maximum=2100),
        Field("mileage", "Kilometerstand", "int", maximum=9_999_999),
        Field("purchase_price", "Einkaufspreis", "money", readonly=sold,
              hint="leer = Katalogpreis" if new else ""),
        Field("list_price", "Verkaufspreis / Listenpreis", "money",
              hint="leer = Katalogpreis" if new else ""),
        Field("purchase_date", "Eingangsdatum", "date"),
        Field("location", "Standort / Stellplatz"),
        Field("notes", "Notizen", "multiline"),
    ]
    return fields


# --------------------------------------------------------------------------
# Cockpit
# --------------------------------------------------------------------------

class CockpitPage(QWidget):
    TILES = [
        ("stock_count", "Fahrzeuge im Bestand", "int"),
        ("stock_value", "Bestandswert (EK)", "money"),
        ("showroom_count", "In der Ausstellung", "int"),
        ("open_orders", "Offene Bestellungen", "int"),
        ("sales_month", "Verkäufe lfd. Monat", "money"),
        ("margin_month", "Rohertrag lfd. Monat", "money"),
        ("sales_year", "Verkäufe lfd. Jahr", "money"),
        ("margin_year", "Rohertrag lfd. Jahr", "money"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.head = QLabel("Autohaus-Cockpit")
        self.head.setObjectName("pageTitle")
        lay.addWidget(self.head)
        grid = QGridLayout()
        self.tiles = {}
        for i, (key, title, _) in enumerate(self.TILES):
            self.tiles[key] = Tile(title)
            grid.addWidget(self.tiles[key], i // 4, i % 4)
        lay.addLayout(grid)
        self.chart = BarChart(title="Fahrzeugverkäufe je Monat (EUR)")
        lay.addWidget(self.chart)
        lower = QHBoxLayout()
        self.aging = DataTable(["Fahrzeug", "Status", "Standtage", "VK"])
        self.orders = DataTable(["Bestellung", "Modell", "Für", "Liefertermin"])
        for title, table in (("Längste Standzeit", self.aging), ("Erwartete Fahrzeuge", self.orders)):
            box = QVBoxLayout()
            lbl = QLabel(title)
            lbl.setObjectName("section")
            box.addWidget(lbl)
            box.addWidget(table)
            lower.addLayout(box)
        lay.addLayout(lower, 1)

    def refresh(self):
        def load(s):
            stock = sorted(dealer.available_vehicles(s), key=lambda v: v.days_in_stock, reverse=True)[:15]
            orders = [o for o in dealer.search_orders(s, only_open=True) if o.status == "ordered"]
            orders.sort(key=lambda o: o.expected_date or dt.date.max)
            return (get_setting(s, "company_name"), dealer.kpis(s), dealer.monthly_sales(s),
                    [(v.id, [v.label, m.VEHICLE_STATUS[v.status], num(v.days_in_stock, "qty"),
                             num(v.list_price)]) for v in stock],
                    [(o.id, [o.number, o.model.name, o.customer.name if o.customer else "Lager",
                             dcell(o.expected_date)]) for o in orders[:15]])

        res = run(self, load)
        if not res or res is True:
            return
        company, k, monthly, aging, orders = res
        self.head.setText(f"{company} – Cockpit" if company else "Autohaus-Cockpit")
        for key, _, kind in self.TILES:
            v = k[key]
            self.tiles[key].value.setText(f"{fmt_money(v)} €" if kind == "money" else str(v))
        self.tiles["stock_count"].sub.setText(f"davon {k['used_count']} gebraucht, {k['reserved_count']} reserviert")
        self.tiles["stock_value"].sub.setText(f"Listenwert {fmt_money(k['stock_list_value'])} €")
        self.tiles["showroom_count"].sub.setText(f"{k['models']} Modelle im Katalog")
        self.tiles["open_orders"].sub.setText(
            f"{k['overdue_orders']} überfällig · Anzahlungen {fmt_money(k['deposits'])} €")
        self.tiles["sales_month"].sub.setText(f"{k['sales_month_count']} Fahrzeug(e)")
        self.tiles["sales_year"].sub.setText(f"{k['sales_year_count']} Fahrzeug(e)")
        self.chart.set_data(monthly)
        self.aging.fill(aging)
        self.orders.fill(orders)


# --------------------------------------------------------------------------
# Fahrzeugkatalog
# --------------------------------------------------------------------------

class VehicleModelsPage(ListPage):
    title = "Fahrzeugkatalog – Modelle und Preise"
    columns = ["Nr.", "Modell", "Hersteller", "Klasse", "EK", "VK", "Marge",
               "Marge %", "Lieferant", "Im Bestand", "Aktiv"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neu", self.create, primary=True)
        self.add_button("Bearbeiten", self.edit)
        self.add_button("Löschen", self.remove)
        self.add_button("Bestellen", self.order)
        self.add_button("CSV-Import …", self.import_csv)

    def extra_filters(self):
        self.only_active = QCheckBox("nur aktive")
        self.only_active.setChecked(True)
        self.only_active.toggled.connect(self.refresh)
        self.toolbar.addWidget(self.only_active)

    def load(self, s, text):
        counts = {}
        for v in dealer.available_vehicles(s):
            counts[v.model_id] = counts.get(v.model_id, 0) + 1
        rows = []
        for x in dealer.search_models(s, text, self.only_active.isChecked()):
            pct = float(x.margin / x.sales_price * 100) if x.sales_price else 0
            rows.append((x.id, [x.number, x.model, x.make, x.category, num(x.purchase_price),
                                num(x.sales_price), num(x.margin), (f"{pct:.1f} %".replace(".", ","), pct),
                                x.default_supplier.name if x.default_supplier else "",
                                num(counts.get(x.id, 0), "qty"), "ja" if x.active else "nein"]))
        return rows

    def on_double_click(self, *_):
        self.edit()

    def _open(self, model_id):
        def prepare(s):
            values, version = {"active": True}, None
            if model_id:
                x = s.get(m.VehicleModel, model_id)
                values = {k: getattr(x, k) for k in ("number", "make", "model", "category", "purchase_price",
                                                      "sales_price", "default_supplier_id", "notes", "active")}
                version = x.version
            return _suppliers(s), values, version

        res = run(self, prepare)
        if not res:
            return
        suppliers, values, version = res
        fields = [
            Field("number", "Nummer", readonly=True),
            Field("make", "Hersteller / Marke", required=True),
            Field("model", "Modell", required=True),
            Field("category", "Klasse", "choice", CATEGORIES),
            Field("purchase_price", "Einkaufspreis", "money"),
            Field("sales_price", "Verkaufspreis", "money"),
            Field("default_supplier_id", "Standardlieferant / Hersteller", "choice", suppliers),
            Field("notes", "Notizen / Ausstattung", "multiline"),
            Field("active", "Aktiv", "bool"),
        ]
        dlg = FormDialog(self, f"Modell {'bearbeiten' if model_id else 'anlegen'}", fields, values)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.values()
            data.pop("number", None)
            if run(self, lambda s: dealer.save_model(s, model_id, data, version)):
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
        if sel and QMessageBox.question(self, "Löschen", "Modell wirklich löschen?") == QMessageBox.StandardButton.Yes:
            res = run(self, lambda s: masterdata.delete(s, "vehicle_model", sel))
            if res:
                QMessageBox.information(self, "Löschen", f"Modell {res}.")
                self.refresh()

    def order(self):
        sel = self.require_selection()
        if sel:
            open_order_dialog(self, None, {"model_id": sel})

    def import_csv(self):
        QMessageBox.information(
            self, "CSV-Import",
            "Exportieren Sie Ihre Tabelle z. B. in Google Sheets über\n"
            "Datei → Herunterladen → CSV.\n\n"
            "Erkannte Spaltenüberschriften:\n"
            "  Hersteller / Marke\n  Modell / Fahrzeug / Name\n  Klasse / Kategorie\n"
            "  EK / Einkaufspreis\n  VK / Verkaufspreis / Preis\n\n"
            "Bereits vorhandene Modelle (gleicher Hersteller + Modell) werden aktualisiert.\n"
            "Kunden- und Kaufdaten werden nicht importiert.")
        path, _ = QFileDialog.getOpenFileName(self, "CSV-Datei wählen", "", "CSV (*.csv *.txt)")
        if not path:
            return
        raw = open(path, "rb").read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        res = run(self, lambda s: dealer.import_models_csv(s, text))
        if res and res is not True:
            QMessageBox.information(self, "CSV-Import", f"{res[0]} Modell(e) neu angelegt, {res[1]} aktualisiert.")
            self.refresh()


# --------------------------------------------------------------------------
# Fahrzeugbestand
# --------------------------------------------------------------------------

STATUS_FILTERS = [
    ("available", "Verfügbar (Lager, Ausstellung, Reserviert)", m.VEHICLE_AVAILABLE),
    *[(k, v, (k,)) for k, v in m.VEHICLE_STATUS.items()],
    ("all", "Alle Fahrzeuge", None),
]


class VehicleActions:
    """Gemeinsame Aktionen für Bestands- und Ausstellungsseite."""

    def edit_vehicle(self, vehicle_id=None):
        def prepare(s):
            if vehicle_id:
                v = s.get(m.Vehicle, vehicle_id)
                values = {f: getattr(v, f) for f in dealer.VEHICLE_FIELDS}
                return vehicle_fields(s, False, v.status in ("sold", "removed")), values, v.version
            return vehicle_fields(s, True), {"condition": "new", "margin_taxation": False,
                                             "year": dt.date.today().year}, None

        res = run(self, prepare)
        if not res:
            return
        fields, values, version = res
        dlg = FormDialog(self, "Fahrzeug bearbeiten" if vehicle_id else "Fahrzeug anlegen", fields, values)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.values()
            if vehicle_id and data.get("purchase_price") in ("", None):
                data.pop("purchase_price")
            if run(self, lambda s: dealer.save_vehicle(s, vehicle_id, data, version)):
                self.refresh()
                return

    def showroom_vehicle(self, vehicle_id=None):
        def prepare(s):
            loc = s.get(m.Vehicle, vehicle_id).location if vehicle_id else ""
            return [(v.id, v.label) for v in dealer.available_vehicles(s)], loc
        res = run(self, prepare)
        if not res:
            return
        options, loc = res
        v = _ask(self, "Fahrzeug in die Ausstellung", [
            Field("vehicle_id", "Fahrzeug", "choice", options, required=True),
            Field("location", "Stellplatz", hint="z. B. Halle 1 / Platz 3")],
            {"vehicle_id": vehicle_id, "location": loc})
        if v and run(self, lambda s: dealer.to_showroom(s, v["vehicle_id"], v["location"])):
            self.refresh()

    def reserve_vehicle(self, vehicle_id):
        customers = run(self, _customers)
        if not customers or customers is True:
            QMessageBox.information(self, "Reservieren", "Bitte zuerst unter Stammdaten → Kunden einen Kunden anlegen.")
            return
        v = _ask(self, "Fahrzeug reservieren", [
            Field("customer_id", "Kunde", "choice", customers, required=True),
            Field("until", "Reserviert bis", "optdate")])
        if v and run(self, lambda s: dealer.reserve(s, vehicle_id, v["customer_id"], v["until"])):
            self.refresh()

    def sell(self, vehicle_id=None):
        open_sale_dialog(self, vehicle_id)
        self.refresh()


class VehiclesPage(VehicleActions, ListPage):
    title = "Fahrzeugbestand"
    columns = ["Nr.", "Fahrzeug", "Klasse", "Zustand", "Farbe", "Kennzeichen", "FIN", "Baujahr", "km",
               "EK", "VK", "Marge", "Standtage", "Status", "Stellplatz / Reserviert für"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neu", lambda: self.edit_vehicle(None), primary=True)
        self.add_button("Bearbeiten", self.edit)
        self.add_button("Ausstellen", self.showroom)
        self.add_button("Reservieren", self.reserve)
        self.add_button("Reservierung aufheben", self.unreserve)
        self.add_button("Verkaufen", self.sell_selected)
        self.add_button("Ausbuchen", self.remove)

    def extra_filters(self):
        self.status_filter = QComboBox()
        for key, label, _ in STATUS_FILTERS:
            self.status_filter.addItem(label, key)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.cond_filter = QComboBox()
        self.cond_filter.addItem("Neu & gebraucht", None)
        for k, v in CONDITIONS:
            self.cond_filter.addItem(v, k)
        self.cond_filter.currentIndexChanged.connect(self.refresh)
        self.toolbar.addWidget(self.status_filter)
        self.toolbar.addWidget(self.cond_filter)

    def load(self, s, text):
        statuses = STATUS_FILTERS[self.status_filter.currentIndex()][2]
        rows = []
        for v in dealer.search_vehicles(s, text, statuses, self.cond_filter.currentData()):
            extra = v.reserved_for.name if v.status == "reserved" and v.reserved_for else v.location
            active = v.status in m.VEHICLE_AVAILABLE
            rows.append((v.id, [v.number, v.name, v.category, m.VEHICLE_CONDITION.get(v.condition, ""), v.color,
                                v.plate, v.vin, v.year or "", _int_cell(v.mileage), num(v.purchase_price),
                                num(v.list_price), num(v.margin),
                                num(v.days_in_stock if active else 0, "qty"),
                                m.VEHICLE_STATUS.get(v.status, v.status), extra]))
        ek = sum(r[1][9][1] for r in rows)
        vk = sum(r[1][10][1] for r in rows)
        self._summary = (f"{len(rows)} Fahrzeug(e)  ·  Einkaufswert {fmt_money(ek)} €  ·  "
                         f"Listenwert {fmt_money(vk)} €")
        return rows

    def refresh(self):
        super().refresh()
        if getattr(self, "_summary", None):
            self.status.setText(self._summary)

    def on_double_click(self, *_):
        self.edit()

    def edit(self):
        sel = self.require_selection()
        if sel:
            self.edit_vehicle(sel)

    def showroom(self):
        sel = self.require_selection()
        if sel:
            self.showroom_vehicle(sel)

    def reserve(self):
        sel = self.require_selection()
        if sel:
            self.reserve_vehicle(sel)

    def unreserve(self):
        sel = self.require_selection()
        if sel and run(self, lambda s: dealer.unreserve(s, sel)):
            self.refresh()

    def sell_selected(self):
        sel = self.require_selection()
        if sel:
            self.sell(sel)

    def remove(self):
        sel = self.require_selection()
        if not sel:
            return
        v = _ask(self, "Fahrzeug ausbuchen (ohne Verkauf)", [Field("reason", "Grund", required=True,
                                                                     hint="z. B. Totalschaden, Eigenbedarf")])
        if v and run(self, lambda s: dealer.remove_vehicle(s, sel, v["reason"])):
            self.refresh()


# --------------------------------------------------------------------------
# Ausstellung
# --------------------------------------------------------------------------

class ShowroomPage(VehicleActions, ListPage):
    title = "Ausstellung – Showroom"
    columns = ["Stellplatz", "Nr.", "Fahrzeug", "Klasse", "Farbe", "Zustand", "VK", "Ausgestellt seit",
               "Tage", "Status", "Reserviert für"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Fahrzeug ausstellen", lambda: self.showroom_vehicle(None), primary=True)
        self.add_button("Stellplatz ändern", self.move)
        self.add_button("Aus Ausstellung nehmen", self.take_out)
        self.add_button("Reservieren", self.reserve)
        self.add_button("Verkaufen", self.sell_selected)

    def load(self, s, text):
        today = dt.date.today()
        rows = []
        for v in dealer.search_vehicles(s, text, ("showroom", "reserved")):
            if v.showroom_since is None:
                continue
            rows.append((v.id, [v.location or "–", v.number, v.name, v.category, v.color,
                                m.VEHICLE_CONDITION.get(v.condition, ""), num(v.list_price),
                                dcell(v.showroom_since), num((today - v.showroom_since).days, "qty"),
                                m.VEHICLE_STATUS[v.status], v.reserved_for.name if v.reserved_for else ""]))
        rows.sort(key=lambda r: r[1][0])
        return rows

    def move(self):
        sel = self.require_selection()
        if sel:
            self.showroom_vehicle(sel)

    def take_out(self):
        sel = self.require_selection()
        if not sel:
            return
        v = _ask(self, "Aus der Ausstellung nehmen", [Field("location", "Neuer Standort", hint="z. B. Lager hinten")])
        if v is not None and run(self, lambda s: dealer.from_showroom(s, sel, v["location"])):
            self.refresh()

    def reserve(self):
        sel = self.require_selection()
        if sel:
            self.reserve_vehicle(sel)

    def sell_selected(self):
        sel = self.require_selection()
        if sel:
            self.sell(sel)

    def on_double_click(self, *_):
        sel = self.table.selected_id()
        if sel:
            self.edit_vehicle(sel)


# --------------------------------------------------------------------------
# Bestellungen
# --------------------------------------------------------------------------

def open_order_dialog(parent, order_id=None, preset=None) -> bool:
    def prepare(s):
        values, version = dict(preset or {}), None
        if order_id:
            o = s.get(m.VehicleOrder, order_id)
            values = {k: getattr(o, k) for k in ("model_id", "supplier_id", "customer_id", "color", "purchase_price",
                                                  "sales_price", "deposit", "order_date", "expected_date", "notes")}
            version = o.version
        return _models(s), _suppliers(s), _customers(s), values, version

    res = run(parent, prepare)
    if not res:
        return False
    models, suppliers, customers, values, version = res
    if not models:
        QMessageBox.information(parent, "Bestellung", "Bitte zuerst Modelle im Fahrzeugkatalog anlegen.")
        return False
    fields = [
        Field("model_id", "Modell (Katalog)", "choice", models, required=True),
        Field("color", "Farbe / Ausstattung"),
        Field("customer_id", "Kunde (leer = Lagerfahrzeug)", "choice", customers),
        Field("supplier_id", "Lieferant / Hersteller", "choice", suppliers),
        Field("purchase_price", "Einkaufspreis", "money", hint="leer = Katalogpreis"),
        Field("sales_price", "Vereinbarter Verkaufspreis", "money", hint="leer = Katalogpreis"),
        Field("deposit", "Anzahlung des Kunden", "money"),
        Field("order_date", "Bestelldatum", "date"),
        Field("expected_date", "Voraussichtliche Lieferung", "optdate"),
        Field("notes", "Notizen", "multiline"),
    ]
    dlg = FormDialog(parent, "Bestellung bearbeiten" if order_id else "Neue Fahrzeugbestellung", fields, values)
    while dlg.exec() == QDialog.DialogCode.Accepted:
        data = dlg.values()
        o = run(parent, lambda s: dealer.save_order(s, order_id, data, version).number)
        if o:
            if not order_id:
                QMessageBox.information(parent, "Bestellung", f"Bestellung {o} angelegt.")
            return True
    return False


class VehicleOrdersPage(ListPage):
    title = "Fahrzeugbestellungen"
    columns = ["Nr.", "Datum", "Modell", "Farbe", "Für Kunde", "Lieferant", "EK", "VK", "Anzahlung",
               "Liefertermin", "Status", "Fahrzeug"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neue Bestellung", self.create, primary=True)
        self.add_button("Bearbeiten", self.edit)
        self.add_button("Fahrzeug eingetroffen", self.receive)
        self.add_button("Stornieren", self.cancel)
        self.toolbar.addWidget(output_menu_button(self, self._html))

    def extra_filters(self):
        self.only_open = QCheckBox("nur offene")
        self.only_open.setChecked(True)
        self.only_open.toggled.connect(self.refresh)
        self.toolbar.addWidget(self.only_open)

    def load(self, s, text):
        today = dt.date.today()
        rows = []
        for o in dealer.search_orders(s, text, self.only_open.isChecked()):
            status = m.VEHICLE_ORDER_STATUS[o.status]
            if o.status == "ordered" and o.expected_date and o.expected_date < today:
                status += " ⚠ überfällig"
            rows.append((o.id, [o.number, dcell(o.order_date), o.model.name, o.color,
                                o.customer.name if o.customer else "– Lager –",
                                o.supplier.name if o.supplier else "", num(o.purchase_price), num(o.sales_price),
                                num(o.deposit), dcell(o.expected_date), status,
                                o.vehicle.number if o.vehicle else ""]))
        return rows

    def create(self):
        if open_order_dialog(self):
            self.refresh()

    def edit(self):
        sel = self.require_selection()
        if sel and open_order_dialog(self, sel):
            self.refresh()

    def on_double_click(self, *_):
        self.edit()

    def receive(self):
        sel = self.require_selection()
        if not sel:
            return
        v = _ask(self, "Fahrzeugeingang buchen", [
            Field("vin", "Fahrgestellnummer (FIN)"),
            Field("plate", "Kennzeichen"),
            Field("year", "Baujahr", "int", maximum=2100),
            Field("mileage", "Kilometerstand", "int", maximum=9_999_999),
            Field("arrival_date", "Eingangsdatum", "date")], {"year": dt.date.today().year})
        if v:
            res = run(self, lambda s: dealer.receive_order(s, sel, v).label)
            if res:
                QMessageBox.information(self, "Fahrzeugeingang", f"Fahrzeug {res} wurde in den Bestand übernommen.")
                self.refresh()

    def cancel(self):
        sel = self.require_selection()
        if sel and QMessageBox.question(self, "Stornieren", "Bestellung wirklich stornieren?") \
                == QMessageBox.StandardButton.Yes and run(self, lambda s: dealer.cancel_order(s, sel)):
            self.refresh()

    def _html(self):
        sel = self.require_selection()
        if sel:
            return run(self, lambda s: (dealer.order_html(s, sel), f"{s.get(m.VehicleOrder, sel).number}.pdf"))


# --------------------------------------------------------------------------
# Verkauf
# --------------------------------------------------------------------------

def open_sale_dialog(parent, vehicle_id=None) -> bool:
    def prepare(s):
        preset_customer = None
        if vehicle_id:
            v = s.get(m.Vehicle, vehicle_id)
            preset_customer = v.reserved_for_id
        return _available(s), _customers(s), preset_customer

    res = run(parent, prepare)
    if not res:
        return False
    vehicles, customers, preset_customer = res
    if not vehicles:
        QMessageBox.information(parent, "Verkauf", "Es ist kein verkaufbares Fahrzeug im Bestand.")
        return False
    if not customers:
        QMessageBox.information(parent, "Verkauf", "Bitte zuerst unter Stammdaten → Kunden den Käufer anlegen.")
        return False
    fields = [
        Field("vehicle_id", "Fahrzeug", "choice", vehicles, required=True),
        Field("customer_id", "Käufer (Kunde)", "choice", customers, required=True),
        Field("sale_date", "Verkaufsdatum", "date"),
        Field("price", "Verkaufspreis", "money", hint="leer = Listenpreis"),
        Field("payment_account", "Zahlungsart", "choice", PAYMENTS, required=True),
        Field("has_trade_in", "Inzahlungnahme eines Fahrzeugs", "bool"),
        Field("notes", "Vereinbarungen", "multiline"),
    ]
    values = {"vehicle_id": vehicle_id, "customer_id": preset_customer, "payment_account": "1200",
              "has_trade_in": False}
    dlg = FormDialog(parent, "Fahrzeug verkaufen", fields, values)
    while dlg.exec() == QDialog.DialogCode.Accepted:
        data = dlg.values()
        if data.pop("has_trade_in"):
            ti_fields = vehicle_fields_trade_in()
            ti = _ask(parent, "Inzahlungnahme – Fahrzeugdaten", ti_fields, {"margin_taxation": True})
            if ti is None:
                continue
            data["trade_in"] = ti
        result = run(parent, lambda s: _sell_summary(dealer.sell_vehicle(s, data)))
        if result:
            QMessageBox.information(parent, "Verkauf", result)
            return True
    return False


def vehicle_fields_trade_in():
    return [
        Field("make", "Hersteller", required=True),
        Field("model", "Modell", required=True),
        Field("category", "Klasse", "choice", CATEGORIES),
        Field("color", "Farbe"),
        Field("vin", "Fahrgestellnummer (FIN)"),
        Field("plate", "Kennzeichen"),
        Field("year", "Baujahr", "int", maximum=2100),
        Field("mileage", "Kilometerstand", "int", maximum=9_999_999),
        Field("trade_in_value", "Anrechnungswert", "money", required=True),
        Field("list_price", "Geplanter Wiederverkaufspreis", "money"),
    ]


def _sell_summary(sale: m.VehicleSale) -> str:
    text = (f"Kaufvertrag {sale.number} erstellt.\n\nFahrzeug: {sale.vehicle.label}\n"
            f"Käufer: {sale.customer.name}\nKaufpreis: {fmt_money(sale.price)} €\n")
    if sale.trade_in_value:
        text += (f"Inzahlungnahme: {fmt_money(sale.trade_in_value)} € ({sale.trade_in_vehicle.number})\n"
                 f"Zahlbetrag: {fmt_money(sale.payment_amount)} €\n")
    return text + f"Rohertrag: {fmt_money(sale.margin)} €"


class VehicleSalesPage(ListPage):
    title = "Fahrzeugverkäufe"
    columns = ["Nr.", "Datum", "Fahrzeug", "Zustand", "Käufer", "Kaufpreis", "Inzahlungnahme", "Zahlbetrag",
               "EK", "Rohertrag", "Verkäufer", "Status"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neuer Verkauf", self.create, primary=True)
        self.add_button("Stornieren", self.cancel)
        self.toolbar.addWidget(output_menu_button(self, self._html))

    def extra_filters(self):
        self.year = QComboBox()
        self.year.addItem("Alle Jahre", None)
        this = dt.date.today().year
        for y in range(this, this - 6, -1):
            self.year.addItem(str(y), y)
        self.year.setCurrentIndex(1)
        self.year.currentIndexChanged.connect(self.refresh)
        self.toolbar.addWidget(self.year)

    def load(self, s, text):
        rows, total, margin, n = [], 0.0, 0.0, 0
        for x in dealer.search_sales(s, text, self.year.currentData()):
            done = x.status == "done"
            if done:
                total += float(x.price)
                margin += float(x.margin)
                n += 1
            rows.append((x.id, [x.number, dcell(x.sale_date), x.vehicle.label,
                                m.VEHICLE_CONDITION.get(x.vehicle.condition, ""), x.customer.name, num(x.price),
                                num(x.trade_in_value), num(x.payment_amount), num(x.purchase_price),
                                num(x.margin), x.salesperson, m.DEAL_STATUS[x.status]]))
        self._summary = (f"{n} Verkauf/Verkäufe  ·  Umsatz {fmt_money(total)} €  ·  "
                         f"Rohertrag {fmt_money(margin)} €")
        return rows

    def refresh(self):
        super().refresh()
        if getattr(self, "_summary", None):
            self.status.setText(self._summary)

    def create(self):
        if open_sale_dialog(self):
            self.refresh()

    def cancel(self):
        sel = self.require_selection()
        if sel and QMessageBox.question(
                self, "Stornieren", "Verkauf wirklich stornieren?\n\nDie Buchung wird storniert und das "
                "Fahrzeug geht zurück in den Bestand.") == QMessageBox.StandardButton.Yes \
                and run(self, lambda s: dealer.cancel_sale(s, sel)):
            self.refresh()

    def on_double_click(self, *_):
        res = self._html()
        if res:
            from .widgets import export_pdf
            export_pdf(self, *res)

    def _html(self):
        sel = self.require_selection()
        if sel:
            return run(self, lambda s: (dealer.sale_contract_html(s, sel),
                                        f"Kaufvertrag_{s.get(m.VehicleSale, sel).number}.pdf"))


class SalesReportPage(QWidget):
    PERIODS = [("month", "Laufender Monat"), ("year", "Laufendes Jahr"), ("last_year", "Vorjahr"),
               ("all", "Gesamt")]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        head = QLabel("Verkaufsauswertung")
        head.setObjectName("pageTitle")
        lay.addWidget(head)
        row = QHBoxLayout()
        self.period = QComboBox()
        for k, label in self.PERIODS:
            self.period.addItem(label, k)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.refresh)
        row.addWidget(QLabel("Zeitraum:"))
        row.addWidget(self.period)
        row.addStretch(1)
        lay.addLayout(row)
        tables = QHBoxLayout()
        self.by_person = DataTable(["Verkäufer", "Anzahl", "Umsatz", "Rohertrag"])
        self.by_model = DataTable(["Modell", "Anzahl", "Umsatz", "Rohertrag"])
        for title, table in (("Nach Verkäufer", self.by_person), ("Nach Modell", self.by_model)):
            box = QVBoxLayout()
            lbl = QLabel(title)
            lbl.setObjectName("section")
            box.addWidget(lbl)
            box.addWidget(table)
            tables.addLayout(box)
        lay.addLayout(tables, 1)

    def _range(self):
        today = dt.date.today()
        key = self.period.currentData()
        if key == "month":
            return today.replace(day=1), today
        if key == "year":
            return today.replace(month=1, day=1), today
        if key == "last_year":
            return dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)
        return dt.date(1900, 1, 1), dt.date(2999, 12, 31)

    def refresh(self):
        start, end = self._range()
        res = run(self, lambda s: (dealer.sales_by_salesperson(s, start, end), dealer.sales_by_model(s, start, end)))
        if not res or res is True:
            return
        people, models = res
        self.by_person.fill([(i, [n, num(c, "qty"), num(t), num(mg)]) for i, (n, c, t, mg) in enumerate(people)])
        self.by_model.fill([(i, [n, num(c, "qty"), num(t), num(mg)]) for i, (n, c, t, mg) in enumerate(models)])


def sales_module():
    return TabsPage([("Verkäufe", VehicleSalesPage()), ("Auswertung", SalesReportPage())])


# --------------------------------------------------------------------------
# Gebrauchtwagen-Ankauf
# --------------------------------------------------------------------------

class UsedPurchasesPage(ListPage):
    title = "Gebrauchtwagen-Ankauf"
    columns = ["Nr.", "Datum", "Fahrzeug", "Kennzeichen", "km", "Verkäufer", "Ankaufpreis", "Geplanter VK",
               "Fahrzeugstatus", "Ankäufer", "Status"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("Neuer Ankauf", self.create, primary=True)
        self.add_button("Stornieren", self.cancel)
        self.toolbar.addWidget(output_menu_button(self, self._html))

    def load(self, s, text):
        rows = []
        for x in dealer.search_purchases(s, text):
            v = x.vehicle
            rows.append((x.id, [x.number, dcell(x.purchase_date), f"{v.number} {v.name}", v.plate,
                                _int_cell(v.mileage), x.customer.name, num(x.price), num(v.list_price),
                                m.VEHICLE_STATUS.get(v.status, v.status), x.buyer,
                                m.PURCHASE_STATUS.get(x.status, x.status)]))
        return rows

    def create(self):
        res = run(self, lambda s: (_customers(s), _models(s)))
        if not res:
            return
        customers, models = res
        if not customers:
            QMessageBox.information(self, "Ankauf", "Bitte zuerst unter Stammdaten → Kunden den Verkäufer anlegen.")
            return
        fields = [
            Field("customer_id", "Verkäufer (Kunde)", "choice", customers, required=True),
            Field("model_id", "Katalogmodell (optional)", "choice", models),
            Field("make", "Hersteller", hint="leer = aus Katalog"),
            Field("model", "Modell", hint="leer = aus Katalog"),
            Field("category", "Klasse", "choice", CATEGORIES),
            Field("color", "Farbe"),
            Field("vin", "Fahrgestellnummer (FIN)"),
            Field("plate", "Kennzeichen"),
            Field("year", "Baujahr", "int", maximum=2100),
            Field("mileage", "Kilometerstand", "int", maximum=9_999_999),
            Field("price", "Ankaufpreis", "money", required=True),
            Field("list_price", "Geplanter Verkaufspreis", "money"),
            Field("payment_account", "Zahlungsart", "choice", PAYMENTS, required=True),
            Field("purchase_date", "Ankaufdatum", "date"),
            Field("purchase_notes", "Vereinbarungen / bekannte Mängel", "multiline"),
        ]
        dlg = FormDialog(self, "Gebrauchtwagen ankaufen", fields,
                         {"payment_account": "1200", "margin_taxation": True})
        while dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.values()
            res = run(self, lambda s: dealer.purchase_used(s, data).number)
            if res:
                QMessageBox.information(self, "Ankauf", f"Ankauf {res} gebucht. Das Fahrzeug ist jetzt im Bestand.")
                self.refresh()
                return

    def cancel(self):
        sel = self.require_selection()
        if sel and QMessageBox.question(self, "Stornieren", "Ankauf wirklich stornieren?") \
                == QMessageBox.StandardButton.Yes and run(self, lambda s: dealer.cancel_purchase(s, sel)):
            self.refresh()

    def on_double_click(self, *_):
        res = self._html()
        if res:
            from .widgets import export_pdf
            export_pdf(self, *res)

    def _html(self):
        sel = self.require_selection()
        if sel:
            return run(self, lambda s: (dealer.purchase_contract_html(s, sel),
                                        f"Ankaufvertrag_{s.get(m.UsedPurchase, sel).number}.pdf"))


# --------------------------------------------------------------------------
# Kundenhistorie
# --------------------------------------------------------------------------

class CustomerHistoryDialog(QDialog):
    def __init__(self, parent, customer_id: int):
        super().__init__(parent)
        self.setMinimumSize(780, 420)
        res = run(parent, lambda s: (s.get(m.Customer, customer_id).name, dealer.customer_history(s, customer_id)))
        name, rows = res if res and res is not True else ("", [])
        self.setWindowTitle(f"Fahrzeughistorie – {name}")
        table = DataTable(["Datum", "Vorgang", "Beleg", "Fahrzeug", "Betrag"])
        table.fill([(i, [dcell(d), what, doc, veh, num(amount)]) for i, (d, what, doc, veh, amount) in enumerate(rows)])
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(table)
        if not rows:
            hint = QLabel("Für diesen Kunden sind noch keine Fahrzeugvorgänge erfasst.")
            hint.setObjectName("hint")
            lay.addWidget(hint)
        lay.addWidget(box)

"""Autohaus: Fahrzeugkatalog, Bestand, Ausstellung, Bestellungen, Verkauf und Ankauf.

Alle Fahrzeugpreise sind Bruttobeträge. Gebucht wird wie folgt:

* Fahrzeugeingang aus Bestellung: Wareneingang (3400) + Vorsteuer (1576) an
  Verbindlichkeiten (1600)
* Gebrauchtwagen-Ankauf: Wareneingang Gebrauchtfahrzeuge (3200) an Bank/Kasse
* Verkauf (Regelbesteuerung): Bank/Kasse an Erlöse (8400) + Umsatzsteuer (1776)
* Verkauf (Differenzbesteuerung § 25a UStG): Steuer nur auf die Marge,
  Erlöse auf 8190
* Inzahlungnahme: der Anrechnungswert wird als Wareneingang (3200) gebucht und
  mindert den Zahlbetrag des Kunden.

Der Umsatzsteuersatz für Fahrzeuge ist in den Firmendaten einstellbar
(``vehicle_vat_rate``; 0 = ohne Umsatzsteuer).
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
import re
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import models as m
from . import BusinessError, audit, context, dec, get_setting, next_number
from . import finance
from .reports import money

CENT = Decimal("0.01")

VEHICLE_FIELDS = ("make", "model", "category", "condition", "vin", "plate", "color", "year",
                  "mileage", "purchase_price", "list_price", "purchase_date", "margin_taxation",
                  "location", "notes", "model_id")
DECIMAL_FIELDS = {"purchase_price", "list_price", "sales_price", "deposit", "price", "trade_in_value"}
INT_FIELDS = {"year", "mileage"}


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

_THOUSANDS = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


def parse_money(value) -> Decimal:
    """Wie ``dec``, versteht aber auch deutsche Tausenderpunkte ("80.000", "$ 1.250.000")."""
    if value is None or isinstance(value, (Decimal, int, float)):
        return dec(value)
    text = str(value).replace("$", "").replace("EUR", "").replace("€", "").replace(" ", "").strip()
    if _THOUSANDS.match(text):
        text = text.replace(".", "")
    return dec(text or "0")


def vat_rate(s: Session) -> Decimal:
    return dec(get_setting(s, "vehicle_vat_rate", "19") or "0")


def split_regular(gross: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Bruttobetrag -> (Netto, Steuer) bei Regelbesteuerung."""
    gross = Decimal(gross)
    net = (gross * 100 / (100 + rate)).quantize(CENT)
    return net, gross - net


def split_margin(gross: Decimal, purchase: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Differenzbesteuerung: Steuer nur auf die (positive) Marge."""
    gross = Decimal(gross)
    margin = max(gross - Decimal(purchase or 0), Decimal(0))
    tax = (margin * rate / (100 + rate)).quantize(CENT)
    return gross - tax, tax


def _payment_account(code: str | None) -> str:
    code = code or "1200"
    if code not in m.PAYMENT_ACCOUNTS:
        raise BusinessError("Ungültige Zahlungsart")
    return code


def _clean(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if key in DECIMAL_FIELDS:
            value = parse_money(value)
        elif key in INT_FIELDS:
            value = int(parse_money(value))
        elif isinstance(value, str):
            value = value.strip()
        out[key] = value
    return out


def _check_version(obj, version):
    if version is not None and obj.version != version:
        raise BusinessError("Der Datensatz wurde inzwischen von einem anderen Benutzer geändert. "
                            "Bitte neu laden.")


def _get(s: Session, cls, obj_id, what: str):
    obj = s.get(cls, obj_id) if obj_id else None
    if obj is None:
        raise BusinessError(f"{what} nicht gefunden")
    return obj


def _vin_unique(s: Session, vin: str, own_id: int | None):
    if not vin:
        return
    q = select(m.Vehicle).where(func.upper(m.Vehicle.vin) == vin.upper(), m.Vehicle.status != "removed")
    other = s.scalars(q).first()
    if other is not None and other.id != own_id:
        raise BusinessError(f"Die Fahrgestellnummer {vin} ist bereits bei {other.number} erfasst")


def reverse_entry(s: Session, entry_id: int | None, description: str, reference: str):
    """Storniert eine Buchung durch eine Gegenbuchung (Soll/Haben getauscht)."""
    if not entry_id:
        return None
    entry = s.get(m.JournalEntry, entry_id)
    lines = [(ln.account.number, ln.credit, ln.debit) for ln in entry.lines]
    return finance.post(s, description, lines, reference=reference)


# --------------------------------------------------------------------------
# Fahrzeugkatalog
# --------------------------------------------------------------------------

def search_models(s: Session, text: str = "", only_active=False):
    q = select(m.VehicleModel)
    if text:
        like = f"%{text.lower()}%"
        q = q.where(or_(*[func.lower(c).like(like) for c in
                          (m.VehicleModel.number, m.VehicleModel.make, m.VehicleModel.model,
                           m.VehicleModel.category)]))
    if only_active:
        q = q.where(m.VehicleModel.active.is_(True))
    return s.scalars(q.order_by(m.VehicleModel.make, m.VehicleModel.model)).all()


def save_model(s: Session, model_id: int | None, data: dict, version: int | None = None):
    data = _clean(data)
    if not data.get("make") or not data.get("model"):
        raise BusinessError("Hersteller und Modell sind Pflichtfelder")
    if model_id:
        obj = _get(s, m.VehicleModel, model_id, "Modell")
        _check_version(obj, version)
    else:
        obj = m.VehicleModel(number=data.get("number") or next_number(s, "vehicle_model"))
        s.add(obj)
    for key in ("make", "model", "category", "purchase_price", "sales_price",
                "default_supplier_id", "notes", "active"):
        if key in data:
            setattr(obj, key, data[key])
    if not obj.number:
        obj.number = next_number(s, "vehicle_model")
    s.flush()
    audit(s, "update" if model_id else "create", "vehicle_model", obj.id, obj.name)
    return obj


HEADER_ALIASES = {
    "make": ["hersteller", "marke", "make", "brand", "fabrikat"],
    "model": ["modell", "model", "fahrzeug", "fahrzeugname", "name", "bezeichnung", "typ"],
    "category": ["klasse", "kategorie", "category", "class", "fahrzeugklasse", "art"],
    "purchase_price": ["ek", "einkauf", "einkaufspreis", "ekpreis", "purchase", "einkaufpreis"],
    "sales_price": ["vk", "verkauf", "verkaufspreis", "vkpreis", "preis", "price", "listenpreis"],
}


def _norm(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum())


def import_models_csv(s: Session, text: str) -> tuple[int, int]:
    """Importiert den Fahrzeugkatalog aus einer CSV-Datei (z. B. Export aus Google Sheets).

    Erkannte Spalten (Groß-/Kleinschreibung egal): Hersteller/Marke, Modell/Fahrzeug,
    Klasse/Kategorie, EK/Einkaufspreis, VK/Verkaufspreis. Vorhandene Modelle
    (gleicher Hersteller + Modell) werden aktualisiert. Gibt (neu, aktualisiert) zurück.
    """
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        raise BusinessError("Die Datei ist leer")
    header = [_norm(h) for h in rows[0]]
    cols: dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in header and header.index(alias) not in cols.values():
                cols[key] = header.index(alias)
                break
    if "model" not in cols:
        raise BusinessError("Spalte 'Modell' (oder 'Fahrzeug') nicht gefunden. Gefundene Spalten: "
                            + ", ".join(rows[0]))
    existing = {(x.make.lower(), x.model.lower()): x for x in s.scalars(select(m.VehicleModel))}
    created = updated = 0
    for line_no, row in enumerate(rows[1:], start=2):
        def cell(key):
            idx = cols.get(key)
            return row[idx].strip() if idx is not None and idx < len(row) else ""
        model = cell("model")
        if not model:
            continue
        make = cell("make") or "-"
        try:
            data = {"make": make, "model": model, "category": cell("category"),
                    "purchase_price": parse_money(cell("purchase_price")),
                    "sales_price": parse_money(cell("sales_price"))}
        except BusinessError as exc:
            raise BusinessError(f"Zeile {line_no}: {exc}") from exc
        obj = existing.get((make.lower(), model.lower()))
        if obj is None:
            obj = save_model(s, None, data)
            existing[(make.lower(), model.lower())] = obj
            created += 1
        else:
            if data["category"]:
                obj.category = data["category"]
            for key in ("purchase_price", "sales_price"):
                if key in cols:            # nur vorhandene Spalten überschreiben
                    setattr(obj, key, data[key])
            updated += 1
    s.flush()
    audit(s, "import", "vehicle_model", "", f"{created} neu, {updated} aktualisiert")
    return created, updated


# --------------------------------------------------------------------------
# Fahrzeugbestand
# --------------------------------------------------------------------------

def search_vehicles(s: Session, text: str = "", statuses: tuple | None = None, condition: str | None = None):
    q = select(m.Vehicle)
    if text:
        like = f"%{text.lower()}%"
        q = q.where(or_(*[func.lower(c).like(like) for c in
                          (m.Vehicle.number, m.Vehicle.make, m.Vehicle.model, m.Vehicle.plate,
                           m.Vehicle.vin, m.Vehicle.color, m.Vehicle.category, m.Vehicle.location)]))
    if statuses:
        q = q.where(m.Vehicle.status.in_(statuses))
    if condition:
        q = q.where(m.Vehicle.condition == condition)
    return s.scalars(q.order_by(m.Vehicle.number.desc())).all()


def available_vehicles(s: Session):
    return search_vehicles(s, statuses=m.VEHICLE_AVAILABLE)


def save_vehicle(s: Session, vehicle_id: int | None, data: dict, version: int | None = None,
                 source: str = "manual") -> m.Vehicle:
    """Legt ein Fahrzeug an oder ändert die Stammdaten. Der Status wird nur über
    die Vorgänge (Ausstellen, Reservieren, Verkaufen …) geändert."""
    data = _clean({k: v for k, v in data.items() if k in VEHICLE_FIELDS})
    model = s.get(m.VehicleModel, data["model_id"]) if data.get("model_id") else None
    if model is not None:   # leere Felder aus dem Katalog übernehmen
        for key, src in (("make", "make"), ("model", "model"), ("category", "category")):
            if not data.get(key):
                data[key] = getattr(model, src)
        if vehicle_id is None:
            if not data.get("purchase_price"):
                data["purchase_price"] = model.purchase_price
            if not data.get("list_price"):
                data["list_price"] = model.sales_price
    if not data.get("make") or not data.get("model"):
        raise BusinessError("Bitte ein Katalogmodell wählen oder Hersteller und Modell angeben")
    if data.get("condition", "new") not in m.VEHICLE_CONDITION:
        raise BusinessError("Ungültiger Zustand")
    for key in ("purchase_price", "list_price"):
        if data.get(key, 0) < 0:
            raise BusinessError("Preise dürfen nicht negativ sein")
    if vehicle_id:
        obj = _get(s, m.Vehicle, vehicle_id, "Fahrzeug")
        _check_version(obj, version)
        if obj.status in ("sold", "removed") and "purchase_price" in data \
                and data["purchase_price"] != obj.purchase_price:
            raise BusinessError("Der Einkaufspreis eines verkauften/ausgebuchten Fahrzeugs ist fest")
    _vin_unique(s, data.get("vin", ""), vehicle_id)
    if not vehicle_id:
        obj = m.Vehicle(source=source, status="stock", number=next_number(s, "vehicle"))
        s.add(obj)
    for key, value in data.items():
        if key == "purchase_date" and value is None:
            continue
        setattr(obj, key, value)
    if not obj.number:
        obj.number = next_number(s, "vehicle")
    if obj.purchase_date is None:
        obj.purchase_date = dt.date.today()
    s.flush()
    audit(s, "update" if vehicle_id else "create", "vehicle", obj.number, obj.name)
    return obj


def _require_status(v: m.Vehicle, allowed: tuple, action: str):
    if v.status not in allowed:
        raise BusinessError(f"{action} nicht möglich: Fahrzeug {v.number} hat den Status "
                            f"'{m.VEHICLE_STATUS.get(v.status, v.status)}'")


def to_showroom(s: Session, vehicle_id: int, location: str = "") -> m.Vehicle:
    v = _get(s, m.Vehicle, vehicle_id, "Fahrzeug")
    _require_status(v, ("stock", "showroom", "reserved"), "Ausstellen")
    location = (location or "").strip()
    if location:
        other = s.scalars(select(m.Vehicle).where(
            m.Vehicle.status == "showroom", func.lower(m.Vehicle.location) == location.lower(),
            m.Vehicle.id != v.id)).first()
        if other is not None:
            raise BusinessError(f"Stellplatz '{location}' ist bereits mit {other.label} belegt")
    if v.status == "stock":
        v.status = "showroom"
    if v.showroom_since is None:
        v.showroom_since = dt.date.today()
    v.location = location
    s.flush()
    audit(s, "showroom", "vehicle", v.number, location)
    return v


def from_showroom(s: Session, vehicle_id: int, location: str = "") -> m.Vehicle:
    v = _get(s, m.Vehicle, vehicle_id, "Fahrzeug")
    if v.status != "showroom" and v.showroom_since is None:
        raise BusinessError("Das Fahrzeug steht nicht in der Ausstellung")
    if v.status == "showroom":
        v.status = "stock"
    v.showroom_since = None
    v.location = (location or "").strip()
    s.flush()
    audit(s, "showroom_end", "vehicle", v.number)
    return v


def reserve(s: Session, vehicle_id: int, customer_id: int, until: dt.date | None = None) -> m.Vehicle:
    v = _get(s, m.Vehicle, vehicle_id, "Fahrzeug")
    _require_status(v, ("stock", "showroom", "reserved"), "Reservieren")
    _get(s, m.Customer, customer_id, "Kunde")
    v.status = "reserved"
    v.reserved_for_id = customer_id
    v.reserved_until = until
    s.flush()
    audit(s, "reserve", "vehicle", v.number, str(customer_id))
    return v


def unreserve(s: Session, vehicle_id: int) -> m.Vehicle:
    v = _get(s, m.Vehicle, vehicle_id, "Fahrzeug")
    _require_status(v, ("reserved",), "Reservierung aufheben")
    v.status = "showroom" if v.showroom_since else "stock"
    v.reserved_for_id = None
    v.reserved_until = None
    s.flush()
    audit(s, "unreserve", "vehicle", v.number)
    return v


def remove_vehicle(s: Session, vehicle_id: int, reason: str = "") -> m.Vehicle:
    """Bucht ein Fahrzeug ohne Verkauf aus (z. B. Totalschaden, Eigennutzung)."""
    v = _get(s, m.Vehicle, vehicle_id, "Fahrzeug")
    _require_status(v, ("stock", "showroom"), "Ausbuchen")
    v.status = "removed"
    v.showroom_since = None
    v.location = ""
    if reason:
        v.notes = (v.notes + "\n" if v.notes else "") + f"Ausgebucht {dt.date.today():%d.%m.%Y}: {reason}"
    s.flush()
    audit(s, "remove", "vehicle", v.number, reason)
    return v


# --------------------------------------------------------------------------
# Bestellungen
# --------------------------------------------------------------------------

def search_orders(s: Session, text: str = "", only_open: bool = False):
    q = select(m.VehicleOrder).join(m.VehicleModel).outerjoin(
        m.Customer, m.Customer.id == m.VehicleOrder.customer_id)
    if text:
        like = f"%{text.lower()}%"
        q = q.where(or_(func.lower(m.VehicleOrder.number).like(like),
                        func.lower(m.VehicleModel.make).like(like),
                        func.lower(m.VehicleModel.model).like(like),
                        func.lower(m.Customer.name).like(like)))
    if only_open:
        q = q.where(m.VehicleOrder.status.in_(["ordered", "arrived"]))
    return s.scalars(q.order_by(m.VehicleOrder.id.desc())).all()


def save_order(s: Session, order_id: int | None, data: dict, version: int | None = None) -> m.VehicleOrder:
    data = _clean(data)
    model = _get(s, m.VehicleModel, data.get("model_id"), "Katalogmodell")
    if order_id:
        o = _get(s, m.VehicleOrder, order_id, "Bestellung")
        _check_version(o, version)
        if o.status != "ordered":
            raise BusinessError("Nur offene Bestellungen können geändert werden")
    else:
        o = m.VehicleOrder(number=next_number(s, "vehicle_order"), created_by=context.username)
        s.add(o)
    o.model_id = model.id
    o.supplier_id = data.get("supplier_id") or model.default_supplier_id
    o.customer_id = data.get("customer_id") or None
    o.color = data.get("color", "")
    o.purchase_price = data.get("purchase_price") or model.purchase_price
    o.sales_price = data.get("sales_price") or model.sales_price
    o.deposit = data.get("deposit") or Decimal(0)
    if o.deposit < 0 or o.purchase_price < 0 or o.sales_price < 0:
        raise BusinessError("Beträge dürfen nicht negativ sein")
    if o.deposit and not o.customer_id:
        raise BusinessError("Eine Anzahlung ist nur bei einer Kundenbestellung möglich")
    if data.get("order_date"):
        o.order_date = data["order_date"]
    o.expected_date = data.get("expected_date")
    o.notes = data.get("notes", "")
    s.flush()
    audit(s, "update" if order_id else "create", "vehicle_order", o.number, model.name)
    return o


def receive_order(s: Session, order_id: int, data: dict | None = None) -> m.Vehicle:
    """Fahrzeug ist eingetroffen: Fahrzeug anlegen, Eingang buchen, ggf. für den Kunden reservieren."""
    data = _clean(data or {})
    o = _get(s, m.VehicleOrder, order_id, "Bestellung")
    if o.status != "ordered":
        raise BusinessError("Die Bestellung ist nicht offen")
    arrival = data.get("arrival_date") or dt.date.today()
    v = save_vehicle(s, None, {
        "model_id": o.model_id, "condition": "new", "color": o.color,
        "vin": data.get("vin", ""), "plate": data.get("plate", ""),
        "year": data.get("year") or arrival.year, "mileage": data.get("mileage", 0),
        "purchase_price": o.purchase_price, "list_price": o.sales_price,
        "purchase_date": arrival, "notes": f"Aus Bestellung {o.number}",
    }, source="order")
    if o.customer_id:
        v.status = "reserved"
        v.reserved_for_id = o.customer_id
    o.vehicle_id = v.id
    o.arrival_date = arrival
    o.status = "arrived"
    if Decimal(o.purchase_price) > 0:
        net, tax = split_regular(o.purchase_price, vat_rate(s))
        supplier = o.supplier.name if o.supplier else ""
        finance.post(s, f"Fahrzeugeingang {o.number} {v.name} {supplier}".strip(),
                     [("3400", net, 0), ("1576", tax, 0), (finance.PAYABLES, 0, o.purchase_price)],
                     arrival, o.number)
    s.flush()
    audit(s, "receive", "vehicle_order", o.number, v.number)
    return v


def cancel_order(s: Session, order_id: int) -> m.VehicleOrder:
    o = _get(s, m.VehicleOrder, order_id, "Bestellung")
    if o.status != "ordered":
        raise BusinessError("Nur offene (noch nicht eingetroffene) Bestellungen können storniert werden")
    o.status = "cancelled"
    s.flush()
    audit(s, "cancel", "vehicle_order", o.number)
    return o


# --------------------------------------------------------------------------
# Verkauf
# --------------------------------------------------------------------------

def search_sales(s: Session, text: str = "", year: int | None = None):
    q = (select(m.VehicleSale).join(m.Customer, m.Customer.id == m.VehicleSale.customer_id)
         .join(m.Vehicle, m.Vehicle.id == m.VehicleSale.vehicle_id))
    if text:
        like = f"%{text.lower()}%"
        q = q.where(or_(func.lower(m.VehicleSale.number).like(like), func.lower(m.Customer.name).like(like),
                        func.lower(m.Vehicle.make).like(like), func.lower(m.Vehicle.model).like(like),
                        func.lower(m.Vehicle.plate).like(like), func.lower(m.VehicleSale.salesperson).like(like)))
    if year:
        q = q.where(m.VehicleSale.sale_date.between(dt.date(year, 1, 1), dt.date(year, 12, 31)))
    return s.scalars(q.order_by(m.VehicleSale.id.desc())).all()


def sell_vehicle(s: Session, data: dict) -> m.VehicleSale:
    """Verkauft ein Fahrzeug.

    ``data``: vehicle_id, customer_id, sale_date, price (leer = Listenpreis),
    payment_account, notes und optional ``trade_in`` (dict mit Fahrzeugdaten und
    ``trade_in_value``).
    """
    trade_in = data.get("trade_in")
    order_id = data.get("order_id")
    data = _clean({k: v for k, v in data.items() if k not in ("trade_in", "order_id")})
    v = _get(s, m.Vehicle, data.get("vehicle_id"), "Fahrzeug")
    customer = _get(s, m.Customer, data.get("customer_id"), "Kunde")
    # Sperre gegen gleichzeitigen Verkauf desselben Fahrzeugs an zwei Arbeitsplätzen
    s.execute(select(m.Vehicle.id).where(m.Vehicle.id == v.id).with_for_update())
    s.refresh(v)
    _require_status(v, m.VEHICLE_AVAILABLE, "Verkauf")
    if v.status == "reserved" and v.reserved_for_id and v.reserved_for_id != customer.id:
        raise BusinessError(f"Das Fahrzeug ist für {v.reserved_for.name} reserviert. "
                            "Bitte zuerst die Reservierung aufheben.")
    price = data.get("price") or Decimal(v.list_price)
    if price <= 0:
        raise BusinessError("Bitte einen Verkaufspreis angeben")
    account = _payment_account(data.get("payment_account"))
    sale_date = data.get("sale_date") or dt.date.today()
    rate = vat_rate(s)
    if v.margin_taxation:
        net, tax = split_margin(price, v.purchase_price, rate)
        revenue_account = "8190"
    else:
        net, tax = split_regular(price, rate)
        revenue_account = "8400"

    deposit = Decimal(0)
    if order_id:
        order = _get(s, m.VehicleOrder, order_id, "Bestellung")
        if order.vehicle_id != v.id:
            raise BusinessError("Fahrzeug und Bestellung passen nicht zusammen")
        deposit = Decimal(order.deposit or 0)
    sale = m.VehicleSale(number=next_number(s, "vehicle_sale"), vehicle_id=v.id, customer_id=customer.id,
                         sale_date=sale_date, price=price, purchase_price=v.purchase_price,
                         tax_amount=tax, payment_account=account, salesperson=context.username,
                         notes=data.get("notes", ""), order_id=order_id or None, deposit=deposit)
    s.add(sale)
    trade_value = Decimal(0)
    if trade_in:
        trade_value = parse_money(trade_in.get("trade_in_value"))
        if trade_value <= 0:
            raise BusinessError("Bitte den Anrechnungswert der Inzahlungnahme angeben")
        if trade_value + deposit > price:
            raise BusinessError("Anrechnungswert und Anzahlung dürfen den Verkaufspreis nicht übersteigen")
        tv = save_vehicle(s, None, {
            **{k: trade_in.get(k) for k in VEHICLE_FIELDS if trade_in.get(k) not in (None, "")},
            "condition": "used", "purchase_price": trade_value, "purchase_date": sale_date,
            "margin_taxation": True,
            "notes": f"Inzahlungnahme aus {sale.number} ({customer.name})",
        }, source="trade_in")
        sale.trade_in_vehicle_id = tv.id
        sale.trade_in_value = trade_value
    lines = [(account, price - trade_value, 0)]
    if trade_value:
        lines.append(("3200", trade_value, 0))
    lines += [(revenue_account, 0, net), ("1776", 0, tax)]
    entry = finance.post(s, f"Fahrzeugverkauf {sale.number} {v.name} an {customer.name}", lines,
                         sale_date, sale.number)
    sale.journal_entry_id = entry.id
    v.status = "sold"
    v.reserved_for_id = None
    v.reserved_until = None
    v.showroom_since = None
    v.location = ""
    for o in s.scalars(select(m.VehicleOrder).where(m.VehicleOrder.vehicle_id == v.id,
                                                    m.VehicleOrder.status == "arrived")):
        o.status = "delivered"
        if sale.order_id is None and o.customer_id == customer.id:
            sale.order_id = o.id
            sale.deposit = Decimal(o.deposit or 0)
    s.flush()
    audit(s, "sell", "vehicle_sale", sale.number, f"{v.number} {price}")
    return sale


def cancel_sale(s: Session, sale_id: int) -> m.VehicleSale:
    sale = _get(s, m.VehicleSale, sale_id, "Verkauf")
    if sale.status != "done":
        raise BusinessError("Der Verkauf ist bereits storniert")
    tv = sale.trade_in_vehicle
    if tv is not None and tv.status not in ("stock", "showroom"):
        raise BusinessError(f"Das in Zahlung genommene Fahrzeug {tv.number} wurde bereits weiterverwendet "
                            f"(Status '{m.VEHICLE_STATUS[tv.status]}'). Storno nicht möglich.")
    reverse_entry(s, sale.journal_entry_id, f"Storno Fahrzeugverkauf {sale.number}", sale.number)
    sale.status = "cancelled"
    sale.vehicle.status = "stock"
    if tv is not None:
        tv.status = "removed"
        tv.showroom_since = None
        tv.notes = (tv.notes + "\n" if tv.notes else "") + f"Storniert mit {sale.number}"
    for o in s.scalars(select(m.VehicleOrder).where(m.VehicleOrder.vehicle_id == sale.vehicle_id,
                                                    m.VehicleOrder.status == "delivered")):
        o.status = "arrived"
        if o.customer_id:              # Fahrzeug bleibt dem Besteller vorbehalten
            sale.vehicle.status = "reserved"
            sale.vehicle.reserved_for_id = o.customer_id
    s.flush()
    audit(s, "cancel", "vehicle_sale", sale.number)
    return sale


# --------------------------------------------------------------------------
# Gebrauchtwagen-Ankauf
# --------------------------------------------------------------------------

def search_purchases(s: Session, text: str = ""):
    q = (select(m.UsedPurchase).join(m.Customer, m.Customer.id == m.UsedPurchase.customer_id)
         .join(m.Vehicle, m.Vehicle.id == m.UsedPurchase.vehicle_id))
    if text:
        like = f"%{text.lower()}%"
        q = q.where(or_(func.lower(m.UsedPurchase.number).like(like), func.lower(m.Customer.name).like(like),
                        func.lower(m.Vehicle.make).like(like), func.lower(m.Vehicle.model).like(like),
                        func.lower(m.Vehicle.plate).like(like), func.lower(m.Vehicle.vin).like(like)))
    return s.scalars(q.order_by(m.UsedPurchase.id.desc())).all()


def purchase_used(s: Session, data: dict) -> m.UsedPurchase:
    """Kauft einen Gebrauchtwagen an (Verkäufer = Kunde)."""
    seller = _get(s, m.Customer, data.get("customer_id"), "Verkäufer")
    price = parse_money(data.get("price"))
    if price <= 0:
        raise BusinessError("Bitte den Ankaufpreis angeben")
    account = _payment_account(data.get("payment_account"))
    date = data.get("purchase_date") or dt.date.today()
    vehicle_data = {k: data[k] for k in VEHICLE_FIELDS if k in data}
    vehicle_data.update(condition="used", purchase_price=price, purchase_date=date,
                        margin_taxation=data.get("margin_taxation", True))
    v = save_vehicle(s, None, vehicle_data, source="used_purchase")
    p = m.UsedPurchase(number=next_number(s, "used_purchase"), vehicle_id=v.id, customer_id=seller.id,
                       purchase_date=date, price=price, payment_account=account,
                       buyer=context.username, notes=str(data.get("purchase_notes", "")).strip())
    s.add(p)
    s.flush()
    entry = finance.post(s, f"Gebrauchtwagen-Ankauf {p.number} {v.name} von {seller.name}",
                         [("3200", price, 0), (account, 0, price)], date, p.number)
    p.journal_entry_id = entry.id
    v.notes = (v.notes + "\n" if v.notes else "") + f"Ankauf {p.number}"
    s.flush()
    audit(s, "purchase", "used_purchase", p.number, f"{v.number} {price}")
    return p


def cancel_purchase(s: Session, purchase_id: int) -> m.UsedPurchase:
    p = _get(s, m.UsedPurchase, purchase_id, "Ankauf")
    if p.status != "done":
        raise BusinessError("Der Ankauf ist bereits storniert")
    if p.vehicle.status not in ("stock", "showroom"):
        raise BusinessError(f"Das Fahrzeug hat den Status '{m.VEHICLE_STATUS[p.vehicle.status]}' – "
                            "Storno nicht möglich")
    reverse_entry(s, p.journal_entry_id, f"Storno Gebrauchtwagen-Ankauf {p.number}", p.number)
    p.status = "cancelled"
    p.vehicle.status = "removed"
    p.vehicle.showroom_since = None
    s.flush()
    audit(s, "cancel", "used_purchase", p.number)
    return p


# --------------------------------------------------------------------------
# Kundenhistorie
# --------------------------------------------------------------------------

def customer_history(s: Session, customer_id: int) -> list[tuple[dt.date, str, str, str, Decimal]]:
    """[(Datum, Vorgang, Beleg, Fahrzeug, Betrag)] – neueste zuerst."""
    out = []
    for x in s.scalars(select(m.VehicleSale).where(m.VehicleSale.customer_id == customer_id)):
        label = "Kauf" + (" (storniert)" if x.status == "cancelled" else "")
        out.append((x.sale_date, label, x.number, x.vehicle.label, x.price))
        if x.trade_in_vehicle:
            out.append((x.sale_date, "Inzahlungnahme", x.number, x.trade_in_vehicle.label, x.trade_in_value))
    for x in s.scalars(select(m.UsedPurchase).where(m.UsedPurchase.customer_id == customer_id)):
        label = "Ankauf von Kunde" + (" (storniert)" if x.status == "cancelled" else "")
        out.append((x.purchase_date, label, x.number, x.vehicle.label, x.price))
    for x in s.scalars(select(m.VehicleOrder).where(m.VehicleOrder.customer_id == customer_id)):
        out.append((x.order_date, f"Bestellung ({m.VEHICLE_ORDER_STATUS[x.status]})", x.number,
                    x.model.name + (f", {x.color}" if x.color else ""), x.sales_price))
    for v in s.scalars(select(m.Vehicle).where(m.Vehicle.reserved_for_id == customer_id,
                                               m.Vehicle.status == "reserved")):
        out.append((v.reserved_until or dt.date.today(), "Reservierung", v.number, v.label, v.list_price))
    return sorted(out, key=lambda r: r[0], reverse=True)


# --------------------------------------------------------------------------
# Kennzahlen
# --------------------------------------------------------------------------

def kpis(s: Session) -> dict:
    today = dt.date.today()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    stock = search_vehicles(s, statuses=m.VEHICLE_AVAILABLE)

    def sales_since(start):
        return s.scalars(select(m.VehicleSale).where(m.VehicleSale.status == "done",
                                                     m.VehicleSale.sale_date >= start)).all()

    month, year = sales_since(month_start), sales_since(year_start)
    open_orders = s.scalars(select(m.VehicleOrder).where(m.VehicleOrder.status == "ordered")).all()
    return {
        "stock_count": len(stock),
        "stock_value": sum((Decimal(v.purchase_price) for v in stock), Decimal(0)),
        "stock_list_value": sum((Decimal(v.list_price) for v in stock), Decimal(0)),
        "showroom_count": sum(1 for v in stock if v.status == "showroom"),
        "reserved_count": sum(1 for v in stock if v.status == "reserved"),
        "used_count": sum(1 for v in stock if v.condition == "used"),
        "sales_month_count": len(month),
        "sales_month": sum((Decimal(x.price) for x in month), Decimal(0)),
        "margin_month": sum((x.margin for x in month), Decimal(0)),
        "sales_year_count": len(year),
        "sales_year": sum((Decimal(x.price) for x in year), Decimal(0)),
        "margin_year": sum((x.margin for x in year), Decimal(0)),
        "open_orders": len(open_orders),
        "overdue_orders": sum(1 for o in open_orders if o.expected_date and o.expected_date < today),
        "deposits": sum((Decimal(o.deposit) for o in open_orders), Decimal(0)),
        "models": s.scalar(select(func.count()).select_from(m.VehicleModel)
                           .where(m.VehicleModel.active.is_(True))),
    }


def monthly_sales(s: Session, months: int = 12) -> list[tuple[str, Decimal]]:
    today = dt.date.today()
    y, mth = today.year, today.month
    out = []
    for _ in range(months):
        start = dt.date(y, mth, 1)
        end = dt.date(y + (mth == 12), mth % 12 + 1, 1)
        val = s.scalar(select(func.coalesce(func.sum(m.VehicleSale.price), 0)).where(
            m.VehicleSale.status == "done", m.VehicleSale.sale_date >= start, m.VehicleSale.sale_date < end))
        out.append((f"{mth:02d}/{str(y)[2:]}", Decimal(val)))
        mth -= 1
        if mth == 0:
            mth, y = 12, y - 1
    return list(reversed(out))


def sales_by_salesperson(s: Session, start: dt.date, end: dt.date):
    q = (select(m.VehicleSale.salesperson, func.count(), func.sum(m.VehicleSale.price),
                func.sum(m.VehicleSale.price - m.VehicleSale.purchase_price))
         .where(m.VehicleSale.status == "done", m.VehicleSale.sale_date.between(start, end))
         .group_by(m.VehicleSale.salesperson).order_by(func.sum(m.VehicleSale.price).desc()))
    return [(name or "–", n, Decimal(total or 0), Decimal(margin or 0)) for name, n, total, margin in s.execute(q)]


def sales_by_model(s: Session, start: dt.date, end: dt.date):
    q = (select(m.Vehicle.make, m.Vehicle.model, func.count(), func.sum(m.VehicleSale.price),
                func.sum(m.VehicleSale.price - m.VehicleSale.purchase_price))
         .join(m.Vehicle, m.Vehicle.id == m.VehicleSale.vehicle_id)
         .where(m.VehicleSale.status == "done", m.VehicleSale.sale_date.between(start, end))
         .group_by(m.Vehicle.make, m.Vehicle.model).order_by(func.count().desc()))
    return [(f"{mk} {md}", n, Decimal(t or 0), Decimal(mg or 0)) for mk, md, n, t, mg in s.execute(q)]


# --------------------------------------------------------------------------
# Belege (HTML -> Druck/PDF)
# --------------------------------------------------------------------------

def _head(s: Session, title: str, number: str, date: dt.date, partner, partner_role: str) -> str:
    e = html.escape
    company = get_setting(s, "company_name")
    lines = [partner.name, partner.street, f"{partner.zip} {partner.city}".strip(),
             f"Kundennr.: {partner.number}" if partner.number else "",
             f"Tel.: {partner.phone}" if partner.phone else ""]
    address = "<br>".join(e(x) for x in lines if x)
    return f"""
<html><body style="font-family:Arial; font-size:10pt">
<table width="100%"><tr>
<td><span style="font-size:16pt; font-weight:bold">{e(company)}</span><br>
<span style="font-size:8pt">{e(get_setting(s, 'company_street'))} · {e(get_setting(s, 'company_city'))}</span></td>
<td align="right"><b>{e(title)}</b><br>Nr. {e(number)}<br>Datum: {date:%d.%m.%Y}</td>
</tr></table>
<hr>
<p><b>{e(partner_role)}</b><br>{address}</p>"""


def _vehicle_table(v: m.Vehicle | None, model: m.VehicleModel | None = None, color: str = "") -> str:
    e = html.escape
    if v is not None:
        rows = [("Fahrzeug", v.name), ("Klasse", v.category), ("Zustand", m.VEHICLE_CONDITION.get(v.condition, "")),
                ("Fahrgestellnummer", v.vin), ("Kennzeichen", v.plate), ("Farbe", v.color),
                ("Baujahr", str(v.year or "")), ("Kilometerstand", f"{v.mileage:,} km".replace(",", ".")),
                ("Interne Nr.", v.number)]
    else:
        rows = [("Fahrzeug", model.name), ("Klasse", model.category), ("Farbe", color)]
    body = "".join(f"<tr><td width='35%' style='background:#f0f0f0'>{e(k)}</td><td>{e(val)}</td></tr>"
                   for k, val in rows if val)
    return ("<table width='100%' cellspacing='0' cellpadding='4' border='1' style='border-collapse:collapse'>"
            f"{body}</table>")


def _money_rows(rows: list[tuple[str, Decimal, bool]]) -> str:
    body = "".join(
        f"<tr><td align='right'>{'<b>' if bold else ''}{html.escape(label)}{'</b>' if bold else ''}</td>"
        f"<td align='right' width='25%'>{'<b>' if bold else ''}{money(val)} €{'</b>' if bold else ''}</td></tr>"
        for label, val, bold in rows)
    return f"<table width='100%' cellspacing='0' cellpadding='4'>{body}</table>"


def _signatures(left: str, right: str) -> str:
    return (f"<br><br><br><table width='100%'><tr><td width='45%' style='border-top:1px solid #000'>{left}</td>"
            f"<td width='10%'></td><td width='45%' style='border-top:1px solid #000'>{right}</td></tr></table>")


def sale_contract_html(s: Session, sale_id: int) -> str:
    x = _get(s, m.VehicleSale, sale_id, "Verkauf")
    rate = vat_rate(s)
    rows = [("Kaufpreis", x.price, True)]
    if x.vehicle.margin_taxation and rate:
        tax_note = "Differenzbesteuerung gem. § 25a UStG – Umsatzsteuer wird nicht gesondert ausgewiesen."
    elif rate and not x.vehicle.margin_taxation:
        rows.append((f"darin enthaltene USt {rate:.0f} %", x.tax_amount, False))
        tax_note = ""
    else:
        tax_note = ""
    if x.trade_in_value:
        rows.append((f"abzgl. Inzahlungnahme {x.trade_in_vehicle.label}", x.trade_in_value, False))
    if x.deposit:
        rows.append(("abzgl. geleistete Anzahlung", x.deposit, False))
    rows.append((f"Zahlbetrag ({m.PAYMENT_ACCOUNTS.get(x.payment_account, '')})", x.payment_amount, True))
    storno = "<p style='color:#c00; font-size:14pt'><b>STORNIERT</b></p>" if x.status == "cancelled" else ""
    trade = ""
    if x.trade_in_vehicle:
        trade = "<h3>In Zahlung genommenes Fahrzeug</h3>" + _vehicle_table(x.trade_in_vehicle)
    notes = f"<p>Vereinbarungen: {html.escape(x.notes)}</p>" if x.notes else ""
    return (_head(s, "Kaufvertrag", x.number, x.sale_date, x.customer, "Käufer") + storno
            + "<h3>Kaufgegenstand</h3>" + _vehicle_table(x.vehicle) + trade + "<h3>Preis</h3>"
            + _money_rows(rows) + (f"<p style='font-size:8pt'>{tax_note}</p>" if tax_note else "") + notes
            + f"<p>Verkäufer: {html.escape(x.salesperson)}</p>"
            + _signatures("Unterschrift Käufer", "Unterschrift Verkäufer") + "</body></html>")


def purchase_contract_html(s: Session, purchase_id: int) -> str:
    x = _get(s, m.UsedPurchase, purchase_id, "Ankauf")
    storno = "<p style='color:#c00; font-size:14pt'><b>STORNIERT</b></p>" if x.status == "cancelled" else ""
    notes = f"<p>Vereinbarungen / Mängel: {html.escape(x.notes)}</p>" if x.notes else ""
    return (_head(s, "Ankaufvertrag", x.number, x.purchase_date, x.customer, "Verkäufer") + storno
            + "<h3>Fahrzeug</h3>" + _vehicle_table(x.vehicle)
            + _money_rows([(f"Ankaufpreis ({m.PAYMENT_ACCOUNTS.get(x.payment_account, '')})", x.price, True)])
            + notes
            + "<p style='font-size:8pt'>Der Verkäufer versichert, dass das Fahrzeug sein Eigentum ist, frei von "
              "Rechten Dritter ist und keine weiteren als die angegebenen Mängel bekannt sind.</p>"
            + _signatures("Unterschrift Verkäufer", f"Unterschrift Ankäufer ({html.escape(x.buyer)})")
            + "</body></html>")


def offer_html(s: Session, offer_id: int) -> str:
    o = _get(s, m.Offer, offer_id, "Angebot")
    exp = f"<p>Gültig bis: {o.valid_until:%d.%m.%Y}</p>" if o.valid_until else ""
    return (_head(s, "Angebot", o.number, o.offer_date, o.customer, "Kunde") + "<h3>Fahrzeug</h3>"
            + _vehicle_table(o.vehicle, o.model, o.color) + exp + _money_rows([("Angebotspreis", o.price, True)])
            + (f"<p>{html.escape(o.notes)}</p>" if o.notes else "") + "</body></html>")


def order_html(s: Session, order_id: int) -> str:
    o = _get(s, m.VehicleOrder, order_id, "Bestellung")
    if o.customer:
        head = _head(s, "Fahrzeugbestellung", o.number, o.order_date, o.customer, "Besteller")
        rows = [("Vereinbarter Kaufpreis", o.sales_price, True)]
        if o.deposit:
            rows += [("geleistete Anzahlung", o.deposit, False),
                     ("Restbetrag bei Übergabe", Decimal(o.sales_price) - Decimal(o.deposit), True)]
        sign = _signatures("Unterschrift Besteller", "Unterschrift Autohaus")
    else:
        partner = o.supplier or type("P", (), {"name": "", "street": "", "zip": "", "city": "",
                                               "number": "", "phone": ""})()
        head = _head(s, "Bestellung (Lager)", o.number, o.order_date, partner, "Lieferant")
        rows = [("Einkaufspreis", o.purchase_price, True)]
        sign = ""
    exp = f"<p>Voraussichtliche Lieferung: {o.expected_date:%d.%m.%Y}</p>" if o.expected_date else ""
    return (head + "<h3>Fahrzeug</h3>" + _vehicle_table(o.vehicle, o.model, o.color) + exp
            + _money_rows(rows) + (f"<p>{html.escape(o.notes)}</p>" if o.notes else "") + sign
            + "</body></html>")


# --------------------------------------------------------------------------
# Angebote und Statuswechsel  (Angebot -> Bestellung -> Verkauf)
# --------------------------------------------------------------------------

def save_offer(s: Session, offer_id: int | None, data: dict, version: int | None = None) -> m.Offer:
    data = _clean(data)
    customer = _get(s, m.Customer, data.get("customer_id"), "Kunde")
    vehicle = s.get(m.Vehicle, data["vehicle_id"]) if data.get("vehicle_id") else None
    model = s.get(m.VehicleModel, data["model_id"]) if data.get("model_id") else None
    if vehicle is None and model is None:
        raise BusinessError("Bitte ein Fahrzeug aus dem Bestand oder ein Katalogmodell wählen")
    if vehicle is not None:
        _require_status(vehicle, m.VEHICLE_AVAILABLE, "Angebot")
        model = vehicle.model_ref
    if offer_id:
        o = _get(s, m.Offer, offer_id, "Angebot")
        _check_version(o, version)
        if o.status == "ordered":
            raise BusinessError("Das Angebot ist bereits bestellt und kann nicht mehr geändert werden")
    else:
        o = m.Offer(number=next_number(s, "offer"), created_by=context.username, status="open")
        s.add(o)
    default_price = vehicle.list_price if vehicle is not None else model.sales_price
    o.customer_id = customer.id
    o.vehicle_id = vehicle.id if vehicle is not None else None
    o.model_id = model.id if model is not None else None
    o.color = data.get("color") or (vehicle.color if vehicle is not None else "")
    o.price = data.get("price") or Decimal(default_price or 0)
    if o.price <= 0:
        raise BusinessError("Bitte einen Angebotspreis angeben")
    o.offer_date = data.get("offer_date") or o.offer_date or dt.date.today()
    o.valid_until = data.get("valid_until")
    o.notes = data.get("notes", "")
    s.flush()
    audit(s, "update" if offer_id else "create", "offer", o.number, customer.name)
    return o


def _model_for_vehicle(s: Session, v: m.Vehicle) -> m.VehicleModel:
    if v.model_ref is not None:
        return v.model_ref
    found = s.scalars(select(m.VehicleModel).where(func.lower(m.VehicleModel.make) == v.make.lower(),
                                                   func.lower(m.VehicleModel.model) == v.model.lower())).first()
    return found or save_model(s, None, {"make": v.make, "model": v.model, "category": v.category,
                                         "purchase_price": v.purchase_price, "sales_price": v.list_price})


def convert_offer(s: Session, offer_id: int, data: dict | None = None) -> m.VehicleOrder:
    """Kunde hat bezahlt oder angezahlt: aus dem Angebot wird eine Bestellung.

    Bei einem Fahrzeug aus dem Bestand ist es sofort „eingetroffen“ und für den Kunden reserviert.
    """
    data = _clean(data or {})
    o = _get(s, m.Offer, offer_id, "Angebot")
    if o.status == "ordered":
        raise BusinessError("Das Angebot ist bereits bestellt")
    deposit = data.get("deposit") or Decimal(0)
    if deposit < 0 or deposit > o.price:
        raise BusinessError("Die Anzahlung muss zwischen 0 und dem Angebotspreis liegen")
    if o.vehicle is not None:
        v = o.vehicle
        _require_status(v, m.VEHICLE_AVAILABLE, "Bestellung")
        if v.status == "reserved" and v.reserved_for_id not in (None, o.customer_id):
            raise BusinessError(f"Das Fahrzeug ist bereits für {v.reserved_for.name} reserviert")
        model = _model_for_vehicle(s, v)
        order = save_order(s, None, {"model_id": model.id, "customer_id": o.customer_id, "color": v.color,
                                     "purchase_price": v.purchase_price, "sales_price": o.price,
                                     "deposit": deposit, "order_date": data.get("order_date"),
                                     "notes": f"Aus Angebot {o.number} (Bestandsfahrzeug {v.number})"})
        order.purchase_price = v.purchase_price
        order.vehicle_id = v.id
        order.arrival_date = data.get("order_date") or dt.date.today()
        order.status = "arrived"
        v.status = "reserved"
        v.reserved_for_id = o.customer_id
    else:
        order = save_order(s, None, {"model_id": o.model_id, "customer_id": o.customer_id, "color": o.color,
                                     "sales_price": o.price, "deposit": deposit,
                                     "order_date": data.get("order_date"), "expected_date": data.get("expected_date"),
                                     "notes": f"Aus Angebot {o.number}"})
    o.status = "ordered"
    o.order_id = order.id
    s.flush()
    audit(s, "convert", "offer", o.number, order.number)
    return order


def set_offer_status(s: Session, offer_id: int, status: str, data: dict | None = None):
    o = _get(s, m.Offer, offer_id, "Angebot")
    if status not in m.OFFER_STATUS:
        raise BusinessError("Unbekannter Status")
    if status == o.status:
        return o
    if status == "ordered":
        return convert_offer(s, offer_id, data)
    if o.status == "ordered":
        raise BusinessError("Das Angebot ist bereits bestellt. Bitte die Bestellung stornieren oder löschen.")
    o.status = status
    s.flush()
    audit(s, "status", "offer", o.number, status)
    return o


def delete_offer(s: Session, offer_id: int) -> str:
    o = _get(s, m.Offer, offer_id, "Angebot")
    number = o.number
    s.delete(o)
    s.flush()
    audit(s, "delete", "offer", number)
    return number


def _undo_arrival(s: Session, o: m.VehicleOrder) -> None:
    """Macht den Fahrzeugeingang einer Bestellung rückgängig (Fahrzeug wird ausgebucht)."""
    v = o.vehicle
    if v is None:
        return
    if v.status not in m.VEHICLE_AVAILABLE:
        raise BusinessError(f"Das Fahrzeug {v.number} hat den Status '{m.VEHICLE_STATUS[v.status]}'")
    if v.source != "order":
        raise BusinessError("Das Fahrzeug stammt aus dem Bestand und wurde nicht bestellt. "
                            "Bitte die Bestellung stornieren.")
    if Decimal(o.purchase_price) > 0:
        net, tax = split_regular(o.purchase_price, vat_rate(s))
        finance.post(s, f"Storno Fahrzeugeingang {o.number}",
                     [(finance.PAYABLES, o.purchase_price, 0), ("3400", 0, net), ("1576", 0, tax)], reference=o.number)
    v.status = "removed"
    v.reserved_for_id = None
    v.showroom_since = None
    v.notes = (v.notes + "\n" if v.notes else "") + f"Eingang von {o.number} zurückgenommen"
    o.vehicle_id = None
    o.arrival_date = None


def _sale_of_order(s: Session, o: m.VehicleOrder) -> m.VehicleSale | None:
    q = select(m.VehicleSale).where(m.VehicleSale.status == "done",
                                    (m.VehicleSale.order_id == o.id) | (m.VehicleSale.vehicle_id == o.vehicle_id))
    return s.scalars(q.order_by(m.VehicleSale.id.desc())).first()


def set_order_status(s: Session, order_id: int, status: str, data: dict | None = None):
    """Statuswechsel einer Bestellung. Liefert je nach Wechsel Fahrzeug, Verkauf oder Bestellung zurück."""
    o = _get(s, m.VehicleOrder, order_id, "Bestellung")
    if status not in m.VEHICLE_ORDER_STATUS:
        raise BusinessError("Unbekannter Status")
    cur = o.status
    if status == cur:
        return o
    if (cur, status) == ("ordered", "arrived"):
        return receive_order(s, order_id, data)
    if status == "delivered":
        if cur != "arrived":
            raise BusinessError("Ausliefern geht erst, wenn das Fahrzeug eingetroffen ist")
        d = dict(data or {})
        d.update(vehicle_id=o.vehicle_id, customer_id=o.customer_id, order_id=o.id)
        if not o.customer_id:
            raise BusinessError("Lagerbestellung ohne Kunde: bitte das Fahrzeug über „Neuer Verkauf“ verkaufen")
        if not d.get("price"):
            d["price"] = o.sales_price
        return sell_vehicle(s, d)
    if (cur, status) == ("delivered", "arrived"):
        sale = _sale_of_order(s, o)
        if sale is None:
            raise BusinessError("Zugehöriger Verkauf nicht gefunden")
        cancel_sale(s, sale.id)
        return o
    if status == "cancelled":
        if cur == "delivered":
            raise BusinessError("Die Bestellung ist bereits ausgeliefert. Bitte zuerst den Verkauf stornieren.")
        if cur == "arrived" and o.vehicle is not None and o.vehicle.status == "reserved" \
                and o.vehicle.reserved_for_id == o.customer_id:
            unreserve(s, o.vehicle_id)          # Fahrzeug bleibt als Lagerfahrzeug im Bestand
        o.status = "cancelled"
    elif status == "ordered":
        if cur == "arrived":
            _undo_arrival(s, o)
        elif cur == "cancelled" and o.vehicle_id:
            raise BusinessError("Die Bestellung hatte bereits ein Fahrzeug. Bitte eine neue Bestellung anlegen.")
        elif cur == "delivered":
            raise BusinessError("Die Bestellung ist bereits ausgeliefert. Bitte zuerst den Verkauf stornieren.")
        o.status = "ordered"
    elif status == "arrived" and cur == "cancelled":
        if o.vehicle is None or o.vehicle.status not in ("stock", "showroom"):
            raise BusinessError("Zu dieser Bestellung ist kein verfügbares Fahrzeug vorhanden")
        if o.customer_id:
            reserve(s, o.vehicle_id, o.customer_id)
        o.status = "arrived"
    else:
        raise BusinessError("Dieser Statuswechsel ist nicht möglich")
    s.flush()
    audit(s, "status", "vehicle_order", o.number, f"{cur} -> {o.status}")
    return o


def delete_order(s: Session, order_id: int) -> str:
    o = _get(s, m.VehicleOrder, order_id, "Bestellung")
    if o.status == "delivered":
        raise BusinessError("Die Bestellung ist bereits ausgeliefert. Bitte zuerst den Verkauf löschen.")
    if o.status == "arrived":
        if o.vehicle is not None and o.vehicle.source == "order":
            _undo_arrival(s, o)
        elif o.vehicle is not None and o.vehicle.status == "reserved":
            unreserve(s, o.vehicle_id)
    for offer in s.scalars(select(m.Offer).where(m.Offer.order_id == o.id)):
        offer.order_id = None
        offer.status = "open"
    for sale in s.scalars(select(m.VehicleSale).where(m.VehicleSale.order_id == o.id)):
        sale.order_id = None
    number = o.number
    s.flush()
    s.delete(o)
    s.flush()
    audit(s, "delete", "vehicle_order", number)
    return number


def set_sale_status(s: Session, sale_id: int, status: str):
    sale = _get(s, m.VehicleSale, sale_id, "Verkauf")
    if status == sale.status:
        return sale
    if status == "cancelled":
        return cancel_sale(s, sale_id)
    raise BusinessError("Ein stornierter Verkauf kann nicht wieder aktiviert werden. Bitte einen neuen Verkauf anlegen.")


def delete_sale(s: Session, sale_id: int) -> str:
    """Löscht einen Verkauf. Ein abgeschlossener Verkauf wird vorher storniert (Gegenbuchung)."""
    sale = _get(s, m.VehicleSale, sale_id, "Verkauf")
    if sale.status == "done":
        cancel_sale(s, sale_id)
    number = sale.number
    s.delete(sale)
    s.flush()
    audit(s, "delete", "vehicle_sale", number)
    return number

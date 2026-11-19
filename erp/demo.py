"""Demodaten zum Ausprobieren (``python -m erp --demo-data``)."""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .services import BusinessError, auth, context, documents, finance, inspection, inventory, masterdata


def load_demo_data(s: Session) -> None:
    if s.scalar(select(m.Customer).limit(1)) is not None:
        raise BusinessError("Es existieren bereits Kunden – Demodaten werden nicht geladen.")
    rnd = random.Random(42)
    customers = [masterdata.save(s, "customer", None, d) for d in [
        {"name": "Müller Maschinenbau GmbH", "street": "Industriestr. 5", "zip": "70173", "city": "Stuttgart",
         "email": "einkauf@mueller-mb.de", "payment_terms_days": 30, "credit_limit": "50000"},
        {"name": "Schneider Handels AG", "street": "Hafenweg 12", "zip": "20457", "city": "Hamburg",
         "email": "bestellung@schneider-ag.de", "payment_terms_days": 14},
        {"name": "Bäckerei Fischer", "street": "Marktplatz 3", "zip": "80331", "city": "München",
         "payment_terms_days": 7},
        {"name": "Weber Elektrotechnik KG", "street": "Am Bahnhof 8", "zip": "50667", "city": "Köln",
         "payment_terms_days": 30},
        {"name": "Alpen Bau GmbH", "street": "Bergstr. 1", "zip": "6020", "city": "Innsbruck",
         "country": "AT", "payment_terms_days": 21},
    ]]
    suppliers = [masterdata.save(s, "supplier", None, d) for d in [
        {"name": "Stahlwerk Nord GmbH", "zip": "28195", "city": "Bremen"},
        {"name": "Elektro Großhandel Süd", "zip": "90402", "city": "Nürnberg"},
        {"name": "Büro & Co. KG", "zip": "10115", "city": "Berlin"},
    ]]
    products = [masterdata.save(s, "product", None, d) for d in [
        {"name": "Stahlträger HEB 200", "unit": "m", "sales_price": "89", "purchase_price": "52",
         "min_stock": "50", "default_supplier_id": suppliers[0].id},
        {"name": "Schraube M8x40 verzinkt", "unit": "Pkt", "sales_price": "12.5", "purchase_price": "4.2",
         "min_stock": "100", "default_supplier_id": suppliers[0].id},
        {"name": "Kabel NYM-J 3x1,5", "unit": "m", "sales_price": "1.9", "purchase_price": "0.85",
         "min_stock": "500", "default_supplier_id": suppliers[1].id},
        {"name": "LED-Panel 60x60", "sales_price": "79", "purchase_price": "38",
         "min_stock": "20", "default_supplier_id": suppliers[1].id},
        {"name": "Kopierpapier A4", "unit": "Pkt", "sales_price": "5.9", "purchase_price": "3.1",
         "tax_rate": "19", "min_stock": "40", "default_supplier_id": suppliers[2].id},
        {"name": "Fachbuch Statik", "sales_price": "49", "purchase_price": "32", "tax_rate": "7",
         "min_stock": "5", "default_supplier_id": suppliers[2].id},
        {"name": "Montagestunde", "unit": "Std", "sales_price": "68", "stock_managed": False},
    ]]
    wh = s.scalars(select(m.Warehouse)).first()
    masterdata.save(s, "warehouse", None, {"code": "AL", "name": "Außenlager"})
    for p, qty in zip(products[:6], [400, 600, 2400, 150, 120, 60]):
        inventory.book(s, p.id, wh.id, qty, "receipt", "Anfangsbestand")
    finance.post(s, "Eröffnungsbilanz Bank", [("1200", "25000", 0), ("0800", 0, "25000")])

    today = dt.date.today()
    for n in range(14):
        cust = rnd.choice(customers)
        items = [{"product_id": p.id, "quantity": rnd.randint(1, 20)}
                 for p in rnd.sample(products, rnd.randint(1, 3))]
        date = today - dt.timedelta(days=rnd.randint(0, 300))
        so = documents.save_sales_order(s, None, {"customer_id": cust.id, "order_date": date}, items)
        stage = n % 5
        if stage >= 1:
            documents.confirm_sales_order(s, so.id)
        if stage >= 2:
            documents.deliver_sales_order(s, so.id)
        if stage >= 3:
            inv = documents.invoice_sales_order(s, so.id, date)
            if stage == 4:
                finance.register_payment(s, inv.id, inv.gross_amount, date + dt.timedelta(days=10))

    po = documents.save_purchase_order(s, None, {"supplier_id": suppliers[1].id},
                                       [{"product_id": products[3].id, "quantity": 40}])
    documents.place_purchase_order(s, po.id)
    finance.post(s, "Miete Büro", [("4210", "1800", 0), ("1200", 0, "1800")])

    auth.save_user(s, None, "vertrieb", "Vera Vertrieb", "sales", True, "vertrieb")
    auth.save_user(s, None, "lager", "Lars Lager", "warehouse", True, "lager")
    auth.save_user(s, None, "buchhaltung", "Bea Buchhaltung", "accounting", True, "buchhaltung")

    load_inspection_demo(s)


def load_inspection_demo(s: Session) -> None:
    """Beispiel-Abteilung für die Prüfplanung DGUV V3 mit zwei Prüfern."""
    p1 = auth.save_user(s, None, "pruefer1", "Paul Prüfer", "inspector", True, "pruefer1")
    p2 = auth.save_user(s, None, "pruefer2", "Petra Prüferin", "inspector", True, "pruefer2")
    plan = inspection.save_floor_plan(s, None, {"name": "Verwaltung 1. OG", "location": "Gebäude A",
                                                "width_m": 36, "height_m": 16,
                                                "notes": "Schlüssel beim Empfang, Ansprechpartner Hr. Kaiser"})
    north = inspection.add_row(s, plan.id, "Büro 1.", 1, 6, 0, 0, 6, 6, inspector_id=p1.id)
    south = inspection.add_row(s, plan.id, "Büro 1.", 11, 4, 0, 10, 6, 6, inspector_id=p2.id)
    inspection.add_area(s, plan.id, "Flur", 0, 6, 36, 4, expected_devices=4)
    inspection.add_area(s, plan.id, "Besprechung", 24, 10, 8, 6, inspector_id=p2.id, expected_devices=6)
    server = inspection.add_area(s, plan.id, "Serverraum", 32, 10, 4, 6, expected_devices=25,
                                 notes="Nur nach Absprache mit der IT (Abschaltung!)")
    for a in north + south:
        a.expected_devices = 8
    today = dt.date.today()
    saved = context.username, context.role, context.user_id
    try:
        context.username, context.role, context.user_id = p1.username, p1.role, p1.id
        inspection.set_status(s, north[0].id, "done")
        inspection.set_status(s, north[1].id, "done")
        inspection.set_status(s, north[2].id, "partial", progress=60, note="Kaffeeküche noch offen")
        inspection.set_status(s, north[3].id, "appointment", date=today + dt.timedelta(days=3),
                              note="Mitarbeiter im Urlaub")
        context.username, context.role, context.user_id = p2.username, p2.role, p2.id
        inspection.set_status(s, south[0].id, "done")
        inspection.set_status(s, south[1].id, "clarify", note="Raum verschlossen, Schlüssel fehlt")
        inspection.set_status(s, south[2].id, "appointment", date=today - dt.timedelta(days=2))
    finally:
        context.username, context.role, context.user_id = saved
    inspection.assign(s, [server.id], None)

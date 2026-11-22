"""Tests des Autohaus-Moduls (Katalog, Bestand, Ausstellung, Bestellung, Verkauf, Ankauf)."""
import datetime as dt
from decimal import Decimal

import pytest

from erp import db, models as m
from erp.services import BusinessError, dealer, finance, masterdata, set_setting


@pytest.fixture()
def s(db_url):
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as session:
        set_setting(session, "vehicle_vat_rate", "19")   # Steuerlogik weiterhin prüfen (Standard ist 0)
        yield session


def _bal(s):
    return {a.number: saldo for a, _, _, saldo in finance.balances(s)}


def _base(s):
    model = dealer.save_model(s, None, {"make": "Testa", "model": "Roadster", "category": "Sportwagen",
                                        "purchase_price": "80.000", "sales_price": "119.000"})
    cust = masterdata.save(s, "customer", None, {"name": "Testkunde"})
    return model, cust


def test_seed_has_dealer_ranges_and_accounts(s):
    assert finance.account(s, "3200") and finance.account(s, "8190")
    model, _ = _base(s)
    assert model.number == "MD-0001"
    assert model.margin == Decimal("39000")


def test_vehicle_from_catalog_and_showroom(s):
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"model_id": model.id, "vin": "WTEST123", "color": "Rot"})
    assert (v.make, v.model, v.list_price, v.purchase_price) == ("Testa", "Roadster", Decimal("119000"),
                                                                 Decimal("80000"))
    assert v.number == "FZ-000001" and v.status == "stock"
    with pytest.raises(BusinessError):   # FIN doppelt
        dealer.save_vehicle(s, None, {"model_id": model.id, "vin": "wtest123"})
    dealer.to_showroom(s, v.id, "A1")
    assert v.status == "showroom"
    v2 = dealer.save_vehicle(s, None, {"model_id": model.id})
    with pytest.raises(BusinessError):   # Stellplatz belegt
        dealer.to_showroom(s, v2.id, "a1")
    dealer.reserve(s, v.id, cust.id)
    assert v.status == "reserved"
    dealer.unreserve(s, v.id)
    assert v.status == "showroom"        # zurück in die Ausstellung
    dealer.from_showroom(s, v.id)
    assert v.status == "stock" and v.location == ""


def test_regular_sale_books_revenue_and_vat(s):
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"model_id": model.id})
    sale = dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id, "payment_account": "1200"})
    assert sale.price == Decimal("119000") and sale.margin == Decimal("39000")
    assert v.status == "sold"
    bal = _bal(s)
    assert bal["1200"] == Decimal("119000")
    assert bal["8400"] == Decimal("-100000.00")
    assert bal["1776"] == Decimal("-19000.00")
    with pytest.raises(BusinessError):   # nicht zweimal verkaufen
        dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id})
    dealer.cancel_sale(s, sale.id)
    assert v.status == "stock" and sale.status == "cancelled"
    assert all(v == 0 for v in _bal(s).values())


def test_reserved_vehicle_only_for_that_customer(s):
    model, cust = _base(s)
    other = masterdata.save(s, "customer", None, {"name": "Andere"})
    v = dealer.save_vehicle(s, None, {"model_id": model.id})
    dealer.reserve(s, v.id, cust.id)
    with pytest.raises(BusinessError):
        dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": other.id})
    dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id, "price": "115000"})


def test_used_purchase_and_margin_taxed_sale(s):
    _, seller = _base(s)
    buyer = masterdata.save(s, "customer", None, {"name": "Käufer"})
    p = dealer.purchase_used(s, {"customer_id": seller.id, "make": "Alt", "model": "Kombi",
                                 "mileage": "120000", "price": "10000", "list_price": "12380",
                                 "payment_account": "1000"})
    v = p.vehicle
    assert v.condition == "used" and v.margin_taxation and v.source == "used_purchase"
    assert _bal(s)["1000"] == Decimal("-10000") and _bal(s)["3200"] == Decimal("10000")
    sale = dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": buyer.id, "payment_account": "1000"})
    # Marge 2.380 brutto -> 380 USt
    assert sale.tax_amount == Decimal("380.00")
    bal = _bal(s)
    assert bal["8190"] == Decimal("-12000.00") and bal["1776"] == Decimal("-380.00")
    with pytest.raises(BusinessError):   # verkauftes Fahrzeug -> Ankauf nicht stornierbar
        dealer.cancel_purchase(s, p.id)


def test_sale_with_trade_in(s):
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"model_id": model.id})
    sale = dealer.sell_vehicle(s, {
        "vehicle_id": v.id, "customer_id": cust.id,
        "trade_in": {"make": "Alt", "model": "Coupé", "plate": "LS-123", "trade_in_value": "20.000"}})
    tv = sale.trade_in_vehicle
    assert tv.status == "stock" and tv.source == "trade_in" and tv.purchase_price == Decimal("20000")
    assert sale.payment_amount == Decimal("99000")
    bal = _bal(s)
    assert bal["1200"] == Decimal("99000") and bal["3200"] == Decimal("20000")
    dealer.cancel_sale(s, sale.id)
    assert tv.status == "removed" and v.status == "stock"


def test_customer_order_flow(s):
    model, cust = _base(s)
    sup = masterdata.save(s, "supplier", None, {"name": "Werk"})
    o = dealer.save_order(s, None, {"model_id": model.id, "supplier_id": sup.id, "customer_id": cust.id,
                                    "color": "Schwarz", "deposit": "5000"})
    assert o.status == "ordered" and o.sales_price == Decimal("119000")
    v = dealer.receive_order(s, o.id, {"vin": "W0RK1"})
    assert o.status == "arrived" and v.status == "reserved" and v.reserved_for_id == cust.id
    assert _bal(s)["1600"] == Decimal("-80000")
    with pytest.raises(BusinessError):
        dealer.cancel_order(s, o.id)
    dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id})
    assert o.status == "delivered"
    hist = dealer.customer_history(s, cust.id)
    assert {h[1].split(" ")[0] for h in hist} == {"Kauf", "Bestellung"}
    for fn in (dealer.sale_contract_html, ):
        assert "Kaufvertrag" in fn(s, s.query(m.VehicleSale).first().id)
    assert "Fahrzeugbestellung" in dealer.order_html(s, o.id)


def test_zero_vat_setting(s):
    set_setting(s, "vehicle_vat_rate", "0")
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"model_id": model.id})
    sale = dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id})
    assert sale.tax_amount == 0 and _bal(s)["8400"] == Decimal("-119000")


def test_csv_import(s):
    csv_text = ("Hersteller;Fahrzeug;Klasse;Einkaufspreis;Verkaufspreis\n"
                "Testa;Roadster;Sport;$80.000;$ 125.000\n"
                "Nova;Van;Van;20.000,50;30.000\n"
                ";Bike;Motorrad;1000;2000\n")
    _base(s)
    created, updated = dealer.import_models_csv(s, csv_text)
    assert (created, updated) == (2, 1)
    models = {x.name: x for x in dealer.search_models(s)}
    assert models["Testa Roadster"].sales_price == Decimal("125000")
    assert models["Nova Van"].purchase_price == Decimal("20000.50")
    with pytest.raises(BusinessError):
        dealer.import_models_csv(s, "foo,bar\n1,2\n")


def test_kpis(s):
    model, cust = _base(s)
    v1 = dealer.save_vehicle(s, None, {"model_id": model.id})
    dealer.save_vehicle(s, None, {"model_id": model.id})
    dealer.to_showroom(s, v1.id, "S1")
    dealer.save_order(s, None, {"model_id": model.id, "expected_date": dt.date.today() - dt.timedelta(days=1)})
    k = dealer.kpis(s)
    assert k["stock_count"] == 2 and k["showroom_count"] == 1 and k["overdue_orders"] == 1
    dealer.sell_vehicle(s, {"vehicle_id": v1.id, "customer_id": cust.id})
    k = dealer.kpis(s)
    assert k["sales_month_count"] == 1 and k["margin_month"] == Decimal("39000")
    assert dealer.monthly_sales(s)[-1][1] == Decimal("119000")


# ---------------------------------------------------------------- Angebot -> Bestellung -> Verkauf

def test_offer_order_sale_flow(s):
    set_setting(s, "vehicle_vat_rate", "0")
    model, cust = _base(s)
    offer = dealer.save_offer(s, None, {"customer_id": cust.id, "model_id": model.id, "color": "Gold"})
    assert offer.number == "AG-000001" and offer.price == Decimal("119000") and offer.status == "open"
    order = dealer.set_offer_status(s, offer.id, "ordered", {"deposit": "20.000"})
    assert offer.status == "ordered" and order.deposit == Decimal("20000") and order.status == "ordered"
    assert order.sales_price == Decimal("119000") and order.customer_id == cust.id
    with pytest.raises(BusinessError):
        dealer.set_offer_status(s, offer.id, "declined")
    v = dealer.set_order_status(s, order.id, "arrived", {"vin": "XYZ"})
    assert v.status == "reserved" and v.reserved_for_id == cust.id
    sale = dealer.set_order_status(s, order.id, "delivered", {"payment_account": "1000"})
    assert order.status == "delivered" and sale.order_id == order.id and sale.deposit == Decimal("20000")
    assert sale.payment_amount == Decimal("99000") and v.status == "sold"
    # zurück: Verkauf storniert -> Bestellung wieder eingetroffen, Fahrzeug reserviert
    dealer.set_order_status(s, order.id, "arrived")
    assert sale.status == "cancelled" and order.status == "arrived" and v.status == "reserved"
    # Eingang zurücknehmen -> Fahrzeug ausgebucht, Buchungen ausgeglichen
    dealer.set_order_status(s, order.id, "ordered")
    assert order.status == "ordered" and v.status == "removed" and order.vehicle_id is None
    bal = _bal(s)
    assert bal["1600"] == 0 and bal["3400"] == 0
    dealer.delete_order(s, order.id)
    assert offer.status == "open" and offer.order_id is None
    dealer.delete_offer(s, offer.id)
    assert s.query(m.Offer).count() == 0


def test_offer_for_stock_vehicle(s):
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"make": "Ohne", "model": "Katalog", "list_price": "50.000",
                                      "purchase_price": "40.000"})
    offer = dealer.save_offer(s, None, {"customer_id": cust.id, "vehicle_id": v.id, "price": "48.000"})
    order = dealer.convert_offer(s, offer.id, {"deposit": "48.000"})
    assert order.status == "arrived" and order.vehicle_id == v.id and v.status == "reserved"
    assert order.model.name == "Ohne Katalog"          # Modell wurde im Katalog ergänzt
    with pytest.raises(BusinessError):                  # Bestandsfahrzeug: Eingang nicht zurücknehmbar
        dealer.set_order_status(s, order.id, "ordered")
    sale = dealer.set_order_status(s, order.id, "delivered", {})
    assert sale.price == Decimal("48000") and sale.payment_amount == 0
    with pytest.raises(BusinessError):
        dealer.delete_order(s, order.id)
    dealer.delete_sale(s, sale.id)                      # storniert und löscht
    assert order.status == "arrived" and v.status == "reserved"
    dealer.set_order_status(s, order.id, "cancelled")
    assert v.status == "stock"


def test_delete_completed_sale_reverses_booking(s):
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"model_id": model.id})
    sale = dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id})
    dealer.delete_sale(s, sale.id)
    assert v.status == "stock" and s.query(m.VehicleSale).count() == 0
    assert all(x == 0 for x in _bal(s).values())


def test_add_missing_columns(db_url):
    """Bestehende Datenbanken ohne die neuen Spalten werden beim Start ergänzt."""
    from sqlalchemy import inspect, text
    db.connect(db_url)
    db.init_schema()
    eng = db.engine()
    if eng.dialect.name != "sqlite":
        pytest.skip("Spalten entfernen nur mit SQLite getestet")
    with eng.begin() as c:
        c.execute(text("ALTER TABLE vehicle_sales DROP COLUMN deposit"))
    assert "deposit" not in {c["name"] for c in inspect(eng).get_columns("vehicle_sales")}
    assert db.add_missing_columns(eng) == ["vehicle_sales.deposit"]
    assert "deposit" in {c["name"] for c in inspect(eng).get_columns("vehicle_sales")}


def test_trade_in_from_purchase(s):
    set_setting(s, "vehicle_vat_rate", "0")
    model, cust = _base(s)
    v = dealer.save_vehicle(s, None, {"model_id": model.id})
    ti = dealer.purchase_used(s, {"customer_id": cust.id, "make": "Alt", "model": "Coupé", "plate": "LS-1",
                                  "price": "15.000", "status": "trade_in"})
    assert ti.status == "trade_in" and ti.vehicle.status == "pending" and ti.journal_entry_id is None
    assert ti.vehicle not in dealer.available_vehicles(s)          # noch nicht verkaufbar
    assert [x.id for x in dealer.open_trade_ins(s, cust.id)] == [ti.id]
    sale = dealer.sell_vehicle(s, {"vehicle_id": v.id, "customer_id": cust.id, "trade_in_purchase_id": ti.id})
    assert ti.status == "settled" and ti.sale_id == sale.id and ti.vehicle.status == "stock"
    assert sale.trade_in_value == Decimal("15000") and sale.payment_amount == Decimal("104000")
    bal = _bal(s)
    assert bal["1200"] == Decimal("104000") and bal["3200"] == Decimal("15000")
    with pytest.raises(BusinessError):                             # schon verrechnet
        dealer.cancel_purchase(s, ti.id)
    dealer.cancel_sale(s, sale.id)                                 # Inzahlungnahme wieder offen
    assert ti.status == "trade_in" and ti.sale_id is None and ti.vehicle.status == "pending"
    assert all(x == 0 for x in _bal(s).values())
    dealer.set_purchase_status(s, ti.id, "done", {"payment_account": "1000"})   # doch ausbezahlt
    assert ti.status == "done" and ti.vehicle.status == "stock" and _bal(s)["1000"] == Decimal("-15000")


def test_trade_in_cancel_without_booking(s):
    _, cust = _base(s)
    ti = dealer.purchase_used(s, {"customer_id": cust.id, "make": "A", "model": "B", "price": "1000", "status": "trade_in"})
    dealer.cancel_purchase(s, ti.id)
    assert ti.status == "cancelled" and ti.vehicle.status == "removed"
    assert all(x == 0 for x in _bal(s).values())

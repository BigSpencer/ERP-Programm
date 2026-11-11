import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm.exc import StaleDataError

from erp import db, models as m
from erp.services import BusinessError, auth, documents, finance, inventory, masterdata, next_number


@pytest.fixture()
def s(db_url):
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as session:
        yield session


def _setup(s):
    cust = masterdata.save(s, "customer", None, {"name": "Kunde AG", "payment_terms_days": "30"})
    sup = masterdata.save(s, "supplier", None, {"name": "Lieferant GmbH"})
    prod = masterdata.save(s, "product", None, {
        "name": "Schraube", "sales_price": "2,50", "purchase_price": "1,00",
        "tax_rate": "19", "min_stock": "10", "default_supplier_id": sup.id})
    wh = s.query(m.Warehouse).first()
    return cust, sup, prod, wh


def test_seed_and_login(s):
    user = auth.authenticate(s, "admin", "admin")
    assert user.role == "admin"
    with pytest.raises(BusinessError):
        auth.authenticate(s, "admin", "falsch")
    assert finance.account(s, "1400").name.startswith("Forderungen")


def test_number_ranges(s):
    assert next_number(s, "sales_order") == "AU-000001"
    assert next_number(s, "sales_order") == "AU-000002"
    cust = masterdata.save(s, "customer", None, {"name": "X"})
    assert cust.number == "10000"


def test_sales_flow_posts_stock_and_journal(s):
    cust, sup, prod, wh = _setup(s)
    inventory.book(s, prod.id, wh.id, 100, "receipt", "init")
    so = documents.save_sales_order(s, None, {"customer_id": cust.id},
                                    [{"product_id": prod.id, "quantity": "4"}])
    assert so.net_total == Decimal("10.00")
    assert so.gross_total == Decimal("11.90")
    assert documents.confirm_sales_order(s, so.id) == []
    documents.deliver_sales_order(s, so.id)
    assert inventory.stock_of(s, prod.id) == Decimal(96)
    inv = documents.invoice_sales_order(s, so.id, dt.date(2026, 1, 10))
    assert inv.due_date == dt.date(2026, 2, 9)
    bal = {a.number: saldo for a, _, _, saldo in finance.balances(s)}
    assert bal["1400"] == Decimal("11.90")
    assert bal["8400"] == Decimal("-10.00")
    assert bal["1776"] == Decimal("-1.90")
    finance.register_payment(s, inv.id, "5")
    assert inv.status == "partial"
    finance.register_payment(s, inv.id, "6,90")
    assert inv.status == "paid"
    bal = {a.number: saldo for a, _, _, saldo in finance.balances(s)}
    assert bal["1400"] == 0 and bal["1200"] == Decimal("11.90")


def test_delivery_without_stock_fails(s):
    cust, sup, prod, wh = _setup(s)
    so = documents.save_sales_order(s, None, {"customer_id": cust.id},
                                    [{"product_id": prod.id, "quantity": "4"}])
    warnings = documents.confirm_sales_order(s, so.id)
    assert warnings
    with pytest.raises(BusinessError):
        documents.deliver_sales_order(s, so.id)


def test_credit_limit(s):
    cust, sup, prod, wh = _setup(s)
    masterdata.save(s, "customer", cust.id, {"name": cust.name, "credit_limit": "5"})
    so = documents.save_sales_order(s, None, {"customer_id": cust.id},
                                    [{"product_id": prod.id, "quantity": "4"}])
    with pytest.raises(BusinessError, match="Kreditlimit"):
        documents.confirm_sales_order(s, so.id)


def test_purchase_flow_and_reorder(s):
    cust, sup, prod, wh = _setup(s)
    proposals = documents.create_reorder_proposals(s)
    assert len(proposals) == 1
    po = proposals[0]
    assert po.items[0].quantity == Decimal(20)
    documents.place_purchase_order(s, po.id)
    item = po.items[0]
    documents.receive_purchase_order(s, po.id, {item.id: "5"})
    assert po.status == "partial"
    documents.receive_purchase_order(s, po.id)
    assert po.status == "received"
    assert inventory.stock_of(s, prod.id) == Decimal(20)
    bal = {a.number: saldo for a, _, _, saldo in finance.balances(s)}
    assert bal["3400"] == Decimal("20.00")
    assert bal["1576"] == Decimal("3.80")
    assert bal["1600"] == Decimal("-23.80")


def test_unbalanced_entry_rejected(s):
    with pytest.raises(BusinessError):
        finance.post(s, "x", [("1200", 10, 0), ("8400", 0, 9)])


def test_inventory_and_transfer(s):
    cust, sup, prod, wh = _setup(s)
    wh2 = masterdata.save(s, "warehouse", None, {"code": "AL", "name": "Außenlager"})
    inventory.set_inventory(s, prod.id, wh.id, 50)
    inventory.transfer(s, prod.id, wh.id, wh2.id, 20)
    assert inventory.stock_of(s, prod.id, wh.id) == 30
    assert inventory.stock_of(s, prod.id, wh2.id) == 20
    assert inventory.stock_of(s, prod.id) == 50


def test_optimistic_locking(db_url):
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as s:
        cid = masterdata.save(s, "customer", None, {"name": "A"}).id
    # Zwei Clients laden denselben Datensatz
    with db.session_scope() as s1:
        c1 = s1.get(m.Customer, cid)
        v1 = c1.version
    with db.session_scope() as s2:
        masterdata.save(s2, "customer", cid, {"name": "B"}, version=v1)
    with pytest.raises(BusinessError, match="anderen Benutzer"):
        with db.session_scope() as s1:
            masterdata.save(s1, "customer", cid, {"name": "C"}, version=v1)
    # Auch auf ORM-Ebene wird ein veralteter Stand erkannt
    with pytest.raises(StaleDataError):
        with db.session_scope() as s3, db.session_scope() as s4:
            a = s3.get(m.Customer, cid)
            b = s4.get(m.Customer, cid)
            a.name = "X"
            s3.flush()
            s3.commit()
            b.name = "Y"
            s4.flush()


def test_delete_referenced_deactivates(s):
    cust, sup, prod, wh = _setup(s)
    documents.save_sales_order(s, None, {"customer_id": cust.id},
                               [{"product_id": prod.id, "quantity": "1"}])
    assert masterdata.delete(s, "customer", cust.id).startswith("deaktiviert")
    other = masterdata.save(s, "customer", None, {"name": "Weg"})
    assert masterdata.delete(s, "customer", other.id) == "gelöscht"


def test_last_admin_protected(s):
    admin = auth.authenticate(s, "admin", "admin")
    with pytest.raises(BusinessError):
        auth.save_user(s, admin.id, "admin", "A", "sales", True)


def test_parallel_clients_get_unique_numbers(db_url):
    """Mehrere Arbeitsplätze legen gleichzeitig Aufträge an."""
    import threading
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as s:
        cust, sup, prod, wh = _setup(s)
        cid, pid = cust.id, prod.id
    numbers, errors = [], []

    def worker():
        for _ in range(5):
            try:
                with db.session_scope() as s:
                    numbers.append(documents.save_sales_order(
                        s, None, {"customer_id": cid}, [{"product_id": pid, "quantity": 1}]).number)
            except Exception as exc:  # SQLite kann bei Parallelität "database is locked" melden
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(numbers) == len(set(numbers))
    if db_url.startswith("postgresql"):
        assert not errors and len(numbers) == 20

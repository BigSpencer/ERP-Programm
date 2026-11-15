"""Rauchtest der Oberfläche (läuft headless mit QT_QPA_PLATFORM=offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from erp import db  # noqa: E402
from erp.demo import load_demo_data  # noqa: E402
from erp.services import auth, set_user  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_all_pages_render(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_CONFIG", str(tmp_path / "config.ini"))
    db.connect(f"sqlite:///{tmp_path / 'ui.sqlite'}")
    db.init_schema()
    with db.session_scope() as s:
        load_demo_data(s)
        user = auth.authenticate(s, "admin", "admin")
    set_user(user)
    # Fehlerdialoge würden den Test blockieren -> als Fehlschlag werten
    from PySide6.QtWidgets import QMessageBox
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: pytest.fail(str(a[1:]))))

    from erp.ui.app import MainWindow
    from erp.ui.documents import DocumentDialog
    win = MainWindow(user)
    win.show()
    for i in range(win.nav.count()):
        if win.nav.item(i).data(0x0100):
            win.nav.setCurrentRow(i)
            page = win.stack.currentWidget()
            if hasattr(page, "tabs"):
                for t in range(page.tabs.count()):
                    page.tabs.setCurrentIndex(t)
            app.processEvents()
    sales = win.pages["sales"]
    sales.refresh()
    assert sales.table.rowCount() == 14
    dlg = DocumentDialog(win, "sales", sales.table.item(0, 0).data(0x0100))
    assert dlg.loaded and dlg.table.rowCount() >= 1

    restricted = MainWindow(type(user)(username="v", role="sales", full_name="V"))
    assert "finance" not in restricted.pages and "sales" in restricted.pages


def test_dealer_pages_with_data(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_CONFIG", str(tmp_path / "config.ini"))
    db.connect(f"sqlite:///{tmp_path / 'dealer.sqlite'}")
    db.init_schema()
    from erp.services import dealer, masterdata
    with db.session_scope() as s:
        user = auth.authenticate(s, "admin", "admin")
        model = dealer.save_model(s, None, {"make": "Testa", "model": "GT", "category": "Sportwagen",
                                            "purchase_price": "50.000", "sales_price": "70.000"})
        cust = masterdata.save(s, "customer", None, {"name": "Testkunde"})
        v1 = dealer.save_vehicle(s, None, {"model_id": model.id, "plate": "PDM-1"})
        v2 = dealer.save_vehicle(s, None, {"model_id": model.id})
        dealer.to_showroom(s, v1.id, "A1")
        dealer.save_order(s, None, {"model_id": model.id, "customer_id": cust.id})
        sale = dealer.sell_vehicle(s, {"vehicle_id": v2.id, "customer_id": cust.id,
                                       "trade_in": {"make": "Alt", "model": "Wagen", "trade_in_value": "5000"}})
        dealer.purchase_used(s, {"customer_id": cust.id, "make": "Gebraucht", "model": "Kombi", "price": "3000"})
        sale_id, cust_id = sale.id, cust.id
    set_user(user)
    from PySide6.QtWidgets import QMessageBox
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: pytest.fail(str(a[1:]))))

    from erp.ui.app import MainWindow
    from erp.ui.dealer import CustomerHistoryDialog
    win = MainWindow(user)
    assert "Premium Deluxe Motorsport" in win.windowTitle()
    expected = {"vehicles": 3, "showroom": 1, "vehicle_orders": 1, "used_purchases": 1, "vehicle_models": 1}
    for key, rows in expected.items():
        page = win.pages[key]
        page.refresh()
        assert page.table.rowCount() == rows, key
    sales = win.pages["vehicle_sales"]
    sales.tabs.setCurrentIndex(0)
    sales.refresh()
    assert sales.tabs.currentWidget().table.rowCount() == 1
    sales.tabs.setCurrentIndex(1)
    assert sales.tabs.currentWidget().by_person.rowCount() == 1
    cockpit = win.pages["cockpit"]
    cockpit.refresh()
    assert cockpit.tiles["stock_count"].value.text() == "3"
    dlg = CustomerHistoryDialog(win, cust_id)
    assert dlg.findChild(type(sales.tabs.currentWidget().by_person)).rowCount() == 4
    with db.session_scope() as s:
        assert "Kaufvertrag" in dealer.sale_contract_html(s, sale_id)

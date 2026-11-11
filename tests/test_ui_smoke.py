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

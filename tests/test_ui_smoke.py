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


def test_inspection_ui(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_CONFIG", str(tmp_path / "config.ini"))
    db.connect(f"sqlite:///{tmp_path / 'ins.sqlite'}")
    db.init_schema()
    with db.session_scope() as s:
        from erp.demo import load_inspection_demo
        load_inspection_demo(s)
        user = auth.authenticate(s, "pruefer1", "pruefer1")
    set_user(user)
    from PySide6.QtWidgets import QMessageBox
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: pytest.fail(str(a[1:]))))

    from erp.ui.app import MainWindow
    win = MainWindow(user)
    assert list(win.pages) == ["inspection"]          # Prüfer sieht nur sein Modul
    win.show()
    page = win.pages["inspection"].tabs.widget(0)
    page.refresh()
    assert len(page.items) == 13
    # Status per Panel setzen
    item = next(i for i in page.items.values() if i.data_["name"] == "Büro 1.5")
    item.setSelected(True)
    page.status_action("done")
    assert page.items[item.area_id].data_["status"] == "done"
    # Vorbereitung: Bereich verschieben und Bericht erzeugen
    page.mode_prepare.setChecked(True)
    it = page.items[item.area_id]
    it.setPos(it.pos().x() + 10, it.pos().y())       # wird auf das Raster gezogen
    assert it.pos().x() % 20 == 0
    html, filename, res = page.report()
    assert "Büro 1.5" in html and not res["plan.png"].isNull()
    lst = win.pages["inspection"].tabs.widget(1)
    win.pages["inspection"].tabs.setCurrentIndex(1)
    assert lst.table.rowCount() == 6                  # nur eigene Bereiche voreingestellt
    set_user(None)


def test_draw_move_resize_with_mouse(app, tmp_path, monkeypatch):
    """Vorbereitung mit echter Mausbedienung: Raum aufziehen, verschieben, vergrößern."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from erp.services import inspection as ins
    from erp.ui.inspection import PX, FloorPlanPage

    db.connect(f"sqlite:///{tmp_path / 'draw.sqlite'}")
    db.init_schema()
    with db.session_scope() as s:
        ins.save_floor_plan(s, None, {"name": "Test", "width_m": 20, "height_m": 10})
        set_user(auth.authenticate(s, "admin", "admin"))
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: pytest.fail(str(a[1:]))))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Labor", True)))
    page = FloorPlanPage()
    page.resize(1200, 700)
    page.show()
    page.refresh()
    page.mode_prepare.setChecked(True)
    page.view.resetTransform()
    page.view.centerOn(0, 0)
    vp = page.view.viewport()

    def at(x_m, y_m):
        return page.view.mapFromScene(x_m * PX, y_m * PX)

    def drag(a, b):
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
        for i in range(1, 6):
            QTest.mouseMove(vp, a + (b - a) * i / 5)
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
        for _ in range(3):
            app.processEvents()

    drag(at(1, 1), at(5.1, 4))                       # aufziehen 4 × 3 m
    assert len(page.items) == 1
    d = next(iter(page.items.values())).data_
    assert (d["name"], d["x"], d["y"], d["w"], d["h"]) == ("Labor", 1, 1, 4, 3)
    drag(at(2, 2), at(4, 3))                         # verschieben um 2 m / 1 m
    d = next(iter(page.items.values())).data_
    assert (d["x"], d["y"]) == (3, 2)
    next(iter(page.items.values())).setSelected(True)
    drag(at(6.9, 4.9), at(9, 6))                     # Ecke ziehen = Größe ändern
    d = next(iter(page.items.values())).data_
    assert (d["w"], d["h"]) == (6, 4)
    set_user(None)

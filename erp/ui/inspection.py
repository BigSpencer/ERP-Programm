"""Prüfplanung DGUV V3: Grundriss-Editor, Prüfmodus und Bereichsliste.

Bedienung
=========
*Vorbereitung*: Im leeren Bereich des Grundrisses mit gedrückter Maustaste ein
Rechteck aufziehen → Bezeichnung eingeben. Bereiche lassen sich verschieben,
an der rechten unteren Ecke in der Größe ändern, doppelklicken = Eigenschaften.
Alles rastet im 0,5-m-Raster ein.

*Prüfen*: Bereich anklicken und rechts den Status setzen (oder Rechtsklick).
Die Ansicht aktualisiert sich regelmäßig, sodass Änderungen anderer Prüfer
sichtbar werden.
"""
from __future__ import annotations

import datetime as dt
import html as _html

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSlider, QSpinBox, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import models as m
from ..services import context, dec, get_setting, set_setting
from ..services import inspection as ins
from .widgets import (
    DateField, Field, FormDialog, ListPage, confirm, dcell, fmt_date, output_menu_button, run,
)

PX = 40.0               # Bildpunkte je Meter in der Szene
HANDLE = 12.0           # Größe des Anfassers zum Größe-Ändern (px)
STATUS_ICONS = {"open": "○", "partial": "◐", "appointment": "📅", "clarify": "?", "done": "✔"}


def _user_label(u) -> str:
    return (u.full_name or u.username) if u else ""


def _status_text(a: dict) -> str:
    text = m.AREA_STATUS.get(a["status"], a["status"])
    if a["status"] == "partial":
        text += f" ({a['progress']} %)"
    elif a["status"] == "appointment" and a["date"]:
        text += f" {fmt_date(a['date'])}"
    return text


def _summary_text(area_list: list[dict], with_mine: bool = False) -> str:
    counts = {k: 0 for k in m.AREA_STATUS}
    for a in area_list:
        counts[a["status"]] += 1
    total = len(area_list)
    pct = round(sum(a["progress"] for a in area_list) / total) if total else 0
    parts = [f"{total} Bereiche", f"Fortschritt {pct} %"] + [
        f"{STATUS_ICONS[k]} {m.AREA_STATUS[k]}: {v}" for k, v in counts.items() if v]
    if with_mine and context.user_id:
        mine = sum(1 for a in area_list if a["inspector_id"] == context.user_id and a["status"] != "done")
        parts.append(f"meine offenen: {mine}")
    overdue = sum(1 for a in area_list if a["overdue"])
    if overdue:
        parts.append(f"⚠ {overdue} Termin(e) überfällig")
    return "   ·   ".join(parts)


def _area_dict(a: m.InspectionArea) -> dict:
    """Bereich als einfache Daten (unabhängig von der Datenbanksitzung)."""
    return {"id": a.id, "version": a.version, "name": a.name, "x": a.x, "y": a.y, "w": a.w, "h": a.h,
            "status": a.status, "progress": a.progress_pct, "date": a.appointment_date,
            "note": a.status_note, "notes": a.notes, "devices": a.expected_devices,
            "inspector_id": a.inspector_id, "inspector": _user_label(a.inspector),
            "status_by": a.status_by, "status_at": a.status_at, "overdue": a.overdue,
            "plan": a.floor_plan.name if a.floor_plan else ""}


# --------------------------------------------------------------------------
# Grafische Darstellung
# --------------------------------------------------------------------------

class AreaItem(QGraphicsRectItem):
    """Ein Prüfbereich als Rechteck auf dem Grundriss."""

    def __init__(self, data: dict, page: "FloorPlanPage"):
        super().__init__(0, 0, data["w"] * PX, data["h"] * PX)
        self.data_ = data
        self.page = page
        self._resizing = False
        self._press_geom = None
        self.setPos(data["x"] * PX, data["y"] * PX)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self.set_edit_mode(page.edit_mode)

    @property
    def area_id(self) -> int:
        return self.data_["id"]

    def set_edit_mode(self, edit: bool):
        flags = QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        if edit:
            flags |= (QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlags(flags)
        self.setCursor(Qt.CursorShape.SizeAllCursor if edit else Qt.CursorShape.PointingHandCursor)

    def geometry_m(self) -> tuple[float, float, float, float]:
        r = self.rect()
        return (self.pos().x() / PX, self.pos().y() / PX, r.width() / PX, r.height() / PX)

    # -- Darstellung ------------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None):
        d = self.data_
        fill, border = m.AREA_COLORS.get(d["status"], m.AREA_COLORS["open"])
        dimmed = self.page.only_mine and d["inspector_id"] not in (None, context.user_id)
        painter.setOpacity(0.35 if dimmed else 1.0)
        r = self.rect()
        pen = QPen(QColor(border), 2)
        if d["overdue"]:
            pen = QPen(QColor("#d0271d"), 3, Qt.PenStyle.DashLine)
        if self.isSelected():
            pen = QPen(QColor("#1d2d3e"), 4)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(fill)))
        painter.drawRect(r.adjusted(1, 1, -1, -1))
        if d["status"] == "partial" and d["progress"]:
            bar = QRectF(r.left() + 3, r.bottom() - 7, (r.width() - 6) * d["progress"] / 100, 4)
            painter.fillRect(bar, QColor(border))
        # Beschriftung
        painter.setClipRect(r.adjusted(3, 2, -3, -2))
        painter.setPen(QColor("#1d2d3e"))
        f = QFont()
        f.setPointSizeF(max(6.5, min(12.0, r.height() / 8, r.width() / 9)))
        f.setBold(True)
        painter.setFont(f)
        text_rect = r.adjusted(5, 3, -5, -10 if d["status"] == "partial" else -3)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                         | Qt.TextFlag.TextWordWrap, f"{STATUS_ICONS.get(d['status'], '')} {d['name']}")
        f.setBold(False)
        f.setPointSizeF(max(6.0, f.pointSizeF() - 1))
        painter.setFont(f)
        lines = [_status_text(d)] if d["status"] != "open" else []
        if d["inspector"]:
            lines.append(d["inspector"])
        painter.setPen(QColor("#4a5866"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
                         | Qt.TextFlag.TextWordWrap, "\n".join(lines))
        painter.setClipping(False)
        if self.page.edit_mode and self.isSelected():
            painter.setOpacity(1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1d2d3e"))
            painter.drawRect(QRectF(r.right() - HANDLE, r.bottom() - HANDLE, HANDLE, HANDLE))

    # -- Interaktion ------------------------------------------------------
    def _in_handle(self, pos: QPointF) -> bool:
        r = self.rect()
        return pos.x() >= r.right() - HANDLE and pos.y() >= r.bottom() - HANDLE

    def hoverMoveEvent(self, event):
        if self.page.edit_mode:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._in_handle(event.pos())
                           else Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            plan_w, plan_h = self.page.plan_size
            r = self.rect()
            x = ins.snap(value.x() / PX) * PX
            y = ins.snap(value.y() / PX) * PX
            x = min(max(0.0, x), max(0.0, plan_w * PX - r.width()))
            y = min(max(0.0, y), max(0.0, plan_h * PX - r.height()))
            return QPointF(x, y)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self._press_geom = self.geometry_m()
        if (self.page.edit_mode and event.button() == Qt.MouseButton.LeftButton
                and self._in_handle(event.pos())):
            self._resizing = True
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            plan_w, plan_h = self.page.plan_size
            w = max(ins.MIN_SIZE, ins.snap(event.pos().x() / PX))
            h = max(ins.MIN_SIZE, ins.snap(event.pos().y() / PX))
            w = min(w, plan_w - self.pos().x() / PX)
            h = min(h, plan_h - self.pos().y() / PX)
            self.prepareGeometryChange()
            self.setRect(0, 0, w * PX, h * PX)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_resizing, self._resizing = self._resizing, False
        if not was_resizing:
            super().mouseReleaseEvent(event)
        if self.page.edit_mode and self._press_geom and self.geometry_m() != self._press_geom:
            QTimer.singleShot(0, lambda: self.page.save_geometry(self))
        self._press_geom = None

    def mouseDoubleClickEvent(self, event):
        QTimer.singleShot(0, lambda: self.page.open_area(self.area_id))


class PlanScene(QGraphicsScene):
    """Zeichenfläche mit Raster; im Vorbereitungsmodus werden neue Bereiche aufgezogen."""

    def __init__(self, page: "FloorPlanPage"):
        super().__init__()
        self.page = page
        self._start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor("#e9ecef"))
        w, h = self.page.plan_size
        if not w:
            return
        plan = QRectF(0, 0, w * PX, h * PX)
        painter.fillRect(plan, QColor("white"))
        thin = QPen(QColor("#eef0f3"), 0)
        thick = QPen(QColor("#d5dae0"), 0)
        x = 0.0
        while x <= w + 1e-6:
            painter.setPen(thick if abs(x - round(x)) < 1e-6 else thin)
            painter.drawLine(QPointF(x * PX, 0), QPointF(x * PX, h * PX))
            x += ins.GRID
        y = 0.0
        while y <= h + 1e-6:
            painter.setPen(thick if abs(y - round(y)) < 1e-6 else thin)
            painter.drawLine(QPointF(0, y * PX), QPointF(w * PX, y * PX))
            y += ins.GRID
        painter.setPen(QPen(QColor("#1d2d3e"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(plan)

    def _snap_point(self, p: QPointF) -> QPointF:
        w, h = self.page.plan_size
        return QPointF(min(max(0, ins.snap(p.x() / PX)), w) * PX, min(max(0, ins.snap(p.y() / PX)), h) * PX)

    def mousePressEvent(self, event):
        item = self.itemAt(event.scenePos(), self.views()[0].transform()) if self.views() else None
        if (self.page.edit_mode and self.page.plan_id and event.button() == Qt.MouseButton.LeftButton
                and not isinstance(item, AreaItem)):
            self.clearSelection()
            self._start = self._snap_point(event.scenePos())
            self._rubber = self.addRect(QRectF(self._start, self._start),
                                        QPen(QColor("#0a6ed1"), 2, Qt.PenStyle.DashLine),
                                        QBrush(QColor(10, 110, 209, 40)))
            self._rubber.setZValue(5)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber is not None:
            self._rubber.setRect(QRectF(self._start, self._snap_point(event.scenePos())).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rubber is not None:
            r = self._rubber.rect()
            self.removeItem(self._rubber)
            self._rubber = None
            if r.width() >= ins.MIN_SIZE * PX and r.height() >= ins.MIN_SIZE * PX:
                geom = (r.x() / PX, r.y() / PX, r.width() / PX, r.height() / PX)
                QTimer.singleShot(0, lambda: self.page.create_area(*geom))
            return
        super().mouseReleaseEvent(event)


class PlanView(QGraphicsView):
    def __init__(self, scene: PlanScene, page: "FloorPlanPage"):
        super().__init__(scene)
        self.page = page
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(event)

    def zoom(self, factor: float):
        current = self.transform().m11()
        if 0.1 < current * factor < 8:
            self.scale(factor, factor)

    def fit(self):
        w, h = self.page.plan_size
        if w:
            self.fitInView(QRectF(-10, -10, w * PX + 20, h * PX + 20), Qt.AspectRatioMode.KeepAspectRatio)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.page.edit_mode:
            self.page.delete_selected()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if not isinstance(item, AreaItem):
            return
        if not item.isSelected():
            self.scene().clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        self.page.fill_menu(menu)
        menu.exec(event.globalPos())


# --------------------------------------------------------------------------
# Dialoge
# --------------------------------------------------------------------------

class StatusDialog(QDialog):
    """Status eines oder mehrerer Bereiche setzen."""

    def __init__(self, parent, title: str, status: str, current: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        current = current or {}
        self.status = QComboBox()
        for key, label in m.AREA_STATUS.items():
            self.status.addItem(f"{STATUS_ICONS[key]}  {label}", key)
        self.status.setCurrentIndex(max(0, self.status.findData(status)))
        self.date = DateField(current.get("date") or dt.date.today() + dt.timedelta(days=1))
        self.progress = QSpinBox()
        self.progress.setRange(1, 99)
        self.progress.setSuffix(" %")
        self.progress.setValue(current.get("progress") if current.get("status") == "partial" else 50)
        self.note = QPlainTextEdit(current.get("note", "") if current.get("status") == status else "")
        self.note.setFixedHeight(80)
        self.note.setPlaceholderText("z. B. 'Raum verschlossen', 'Serverraum nur nach Absprache', "
                                     "'12 von 30 Geräten geprüft' …")
        form = QFormLayout()
        form.addRow("Status", self.status)
        form.addRow("Termin am", self.date)
        form.addRow("Fortschritt", self.progress)
        form.addRow("Bemerkung", self.note)
        self._form = form
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.status.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self):
        st = self.status.currentData()
        self._form.setRowVisible(self.date, st == "appointment")
        self._form.setRowVisible(self.progress, st == "partial")

    def accept(self):
        st = self.status.currentData()
        if st == "clarify" and not self.note.toPlainText().strip():
            QMessageBox.warning(self, "Bemerkung", "Bitte kurz beschreiben, was geklärt werden muss.")
            return
        super().accept()

    def values(self) -> dict:
        st = self.status.currentData()
        return {"status": st, "date": self.date.value() if st == "appointment" else None,
                "progress": self.progress.value() if st == "partial" else None,
                "note": self.note.toPlainText()}


class RowDialog(QDialog):
    """Mehrere gleich große Räume in einer Reihe anlegen."""

    def __init__(self, parent, plan_size, inspectors):
        super().__init__(parent)
        self.setWindowTitle("Raumreihe anlegen")
        self.prefix = QComboBox()
        self.prefix.setEditable(True)
        self.prefix.addItems(["Büro ", "Raum ", "1.", "EG.", "Labor "])
        self.start = QSpinBox()
        self.start.setRange(0, 9999)
        self.start.setValue(1)
        self.count = QSpinBox()
        self.count.setRange(1, 100)
        self.count.setValue(5)
        self.direction = QComboBox()
        self.direction.addItem("nebeneinander (→)", True)
        self.direction.addItem("untereinander (↓)", False)
        self.inspector = QComboBox()
        self.inspector.addItem("— nicht zugewiesen —", None)
        for uid, label in inspectors:
            self.inspector.addItem(label, uid)

        def spin(v, maximum):
            sp = QSpinBox()
            sp.setRange(0, int(maximum * 2))
            sp.setValue(int(v * 2))
            sp.setSuffix(" × 0,5 m")
            return sp
        self.x, self.y = spin(0, plan_size[0]), spin(0, plan_size[1])
        self.w, self.h = spin(4, plan_size[0]), spin(5, plan_size[1])
        self.w.setMinimum(1)
        self.h.setMinimum(1)
        form = QFormLayout()
        form.addRow("Bezeichnung (Präfix)", self.prefix)
        form.addRow("Erste Nummer", self.start)
        form.addRow("Anzahl Räume", self.count)
        form.addRow("Anordnung", self.direction)
        form.addRow("Start links (X)", self.x)
        form.addRow("Start oben (Y)", self.y)
        form.addRow("Breite je Raum", self.w)
        form.addRow("Tiefe je Raum", self.h)
        form.addRow("Prüfer", self.inspector)
        hint = QLabel("Beispiel: Präfix „Büro 1.“, erste Nummer 1, Anzahl 6 → Büro 1.1 … Büro 1.6")
        hint.setObjectName("hint")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(buttons)

    def values(self) -> dict:
        return {"prefix": self.prefix.currentText(), "start_no": self.start.value(),
                "count": self.count.value(), "horizontal": self.direction.currentData(),
                "x": self.x.value() / 2, "y": self.y.value() / 2,
                "w": self.w.value() / 2, "h": self.h.value() / 2,
                "inspector_id": self.inspector.currentData()}


def _plan_fields():
    return [Field("name", "Bezeichnung (Abteilung / Etage)", required=True),
            Field("location", "Standort / Gebäude"),
            Field("width_m", "Breite (m)", "decimal", required=True),
            Field("height_m", "Tiefe (m)", "decimal", required=True),
            Field("notes", "Hinweise (Ansprechpartner, Schlüssel …)", "multiline")]


def _plan_values(v: dict) -> dict:
    return {**v, "width_m": float(dec(v["width_m"])), "height_m": float(dec(v["height_m"]))}


# --------------------------------------------------------------------------
# Seite: Grundriss
# --------------------------------------------------------------------------

class Legend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for key, label in m.AREA_STATUS.items():
            fill, border = m.AREA_COLORS[key]
            chip = QLabel(f"{STATUS_ICONS[key]} {label}")
            chip.setStyleSheet(f"background:{fill}; border:2px solid {border}; border-radius:3px; "
                               "padding:2px 6px;")
            lay.addWidget(chip)
        overdue = QLabel("Termin überfällig")
        overdue.setStyleSheet("border:2px dashed #d0271d; border-radius:3px; padding:2px 6px;")
        lay.addWidget(overdue)
        lay.addStretch(1)


class FloorPlanPage(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plan_id: int | None = None
        self.plan_version: int | None = None
        self.plan_size: tuple[float, float] = (0.0, 0.0)
        self.plan_name = ""
        self.edit_mode = False
        self.only_mine = False
        self.items: dict[int, AreaItem] = {}
        self._bg_item: QGraphicsPixmapItem | None = None
        self._bg_plan: int | None = None
        self._inspectors: list[tuple[int, str]] = []
        self._loading = False

        lay = QVBoxLayout(self)
        title = QLabel("Prüfplanung DGUV V3 – Grundriss")
        title.setObjectName("pageTitle")
        lay.addWidget(title)

        # Zeile 1: Grundriss wählen / verwalten
        top = QHBoxLayout()
        self.plan_combo = QComboBox()
        self.plan_combo.setMinimumWidth(280)
        self.plan_combo.currentIndexChanged.connect(self._plan_selected)
        top.addWidget(QLabel("Grundriss:"))
        top.addWidget(self.plan_combo)
        for text, slot in [("Neu …", self.new_plan), ("Eigenschaften …", self.edit_plan),
                           ("Kopieren …", self.copy_plan), ("Löschen", self.delete_plan)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        top.addStretch(1)
        self.mode_prepare = QPushButton("✎ Vorbereitung")
        self.mode_check = QPushButton("✔ Prüfen")
        group = QButtonGroup(self)
        for b in (self.mode_prepare, self.mode_check):
            b.setCheckable(True)
            group.addButton(b)
            top.addWidget(b)
        self.mode_check.setChecked(True)
        self.mode_prepare.toggled.connect(self.set_edit_mode)
        lay.addLayout(top)

        # Zeile 2: Werkzeuge (abhängig vom Modus)
        tools = QHBoxLayout()
        self.prep_tools = QWidget()
        pl = QHBoxLayout(self.prep_tools)
        pl.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Rechteck im leeren Bereich aufziehen = neuer Bereich")
        hint.setObjectName("hint")
        for text, slot in [("Raumreihe …", self.add_row), ("Hintergrundbild …", self.load_background),
                           ("Bild entfernen", self.remove_background)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            pl.addWidget(b)
        self.bg_opacity = QSlider(Qt.Orientation.Horizontal)
        self.bg_opacity.setRange(10, 100)
        self.bg_opacity.setValue(45)
        self.bg_opacity.setFixedWidth(90)
        self.bg_opacity.setToolTip("Deckkraft des Hintergrundbilds")
        self.bg_opacity.valueChanged.connect(
            lambda v: self._bg_item and self._bg_item.setOpacity(v / 100))
        pl.addWidget(QLabel("Deckkraft"))
        pl.addWidget(self.bg_opacity)
        pl.addWidget(hint)
        tools.addWidget(self.prep_tools)
        self.check_tools = QWidget()
        cl = QHBoxLayout(self.check_tools)
        cl.setContentsMargins(0, 0, 0, 0)
        self.mine = QCheckBox("Nur meine Bereiche hervorheben")
        self.mine.toggled.connect(self._toggle_mine)
        cl.addWidget(self.mine)
        self.out_btn = output_menu_button(self, self.report, landscape=True)
        self.out_btn.setText("Prüfbericht ▾")
        cl.addWidget(self.out_btn)
        tools.addWidget(self.check_tools)
        tools.addStretch(1)
        for text, slot in [("−", lambda: self.view.zoom(1 / 1.25)), ("+", lambda: self.view.zoom(1.25)),
                           ("Einpassen", lambda: self.view.fit())]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            tools.addWidget(b)
        lay.addLayout(tools)

        # Mitte: Grundriss + Detailbereich
        body = QHBoxLayout()
        self.scene = PlanScene(self)
        self.scene.selectionChanged.connect(self.update_panel)
        self.view = PlanView(self.scene, self)
        body.addWidget(self.view, 1)
        body.addWidget(self._build_panel())
        lay.addLayout(body, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(Legend())
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        bottom.addWidget(self.summary)
        lay.addLayout(bottom)

        self.set_edit_mode(False)
        # Änderungen anderer Prüfer regelmäßig nachladen
        self.timer = QTimer(self, interval=30_000)
        self.timer.timeout.connect(self._auto_refresh)
        self.timer.start()

    # -- Detailbereich -----------------------------------------------------
    def _build_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("pnl")
        panel.setFixedWidth(310)
        lay = QVBoxLayout(panel)
        self.p_title = QLabel()
        self.p_title.setObjectName("section")
        self.p_title.setWordWrap(True)
        self.p_info = QLabel()
        self.p_info.setWordWrap(True)
        self.p_info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.p_title)
        lay.addWidget(self.p_info)

        self.status_buttons = QWidget()
        sl = QVBoxLayout(self.status_buttons)
        sl.setContentsMargins(0, 4, 0, 4)
        self.btn = {}
        for key, text in [("done", "✔  Erledigt"), ("partial", "◐  Teilweise erledigt …"),
                          ("appointment", "📅  Termin vereinbaren …"), ("clarify", "?  In Klärung …"),
                          ("open", "○  Zurücksetzen (offen)")]:
            b = QPushButton(text)
            fill, border = m.AREA_COLORS[key]
            b.setStyleSheet(f"QPushButton {{ background:{fill}; border:1px solid {border}; color:#1d2d3e; "
                            "text-align:left; padding:7px 10px; }")
            b.clicked.connect(lambda _=False, k=key: self.status_action(k))
            sl.addWidget(b)
            self.btn[key] = b
        self.claim_btn = QPushButton("Bereich übernehmen")
        self.claim_btn.clicked.connect(self.claim)
        sl.addWidget(self.claim_btn)
        lay.addWidget(self.status_buttons)

        self.edit_buttons = QWidget()
        el = QVBoxLayout(self.edit_buttons)
        el.setContentsMargins(0, 4, 0, 4)
        for text, slot in [("Eigenschaften …", lambda: self.open_area()),
                           ("Prüfer zuweisen …", self.assign_selected),
                           ("Duplizieren", self.duplicate_selected),
                           ("Löschen", self.delete_selected)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            el.addWidget(b)
        lay.addWidget(self.edit_buttons)

        self.p_hist_label = QLabel("Verlauf")
        self.p_hist_label.setObjectName("section")
        self.p_hist = QListWidget()
        lay.addWidget(self.p_hist_label)
        lay.addWidget(self.p_hist, 1)
        return panel

    def selected(self) -> list[AreaItem]:
        return [i for i in self.scene.selectedItems() if isinstance(i, AreaItem)]

    def update_panel(self):
        try:
            sel = self.selected()
        except RuntimeError:        # Szene wird gerade beim Schließen abgebaut
            return
        self.status_buttons.setVisible(bool(sel) and not self.edit_mode)
        self.edit_buttons.setVisible(bool(sel) and self.edit_mode)
        self.p_hist.clear()
        self.p_hist.setVisible(len(sel) == 1)
        self.p_hist_label.setVisible(len(sel) == 1)
        if not sel:
            self.p_title.setText("Kein Bereich ausgewählt")
            self.p_info.setText(
                "<span style='color:#6a7785'>"
                + ("Rechteck im leeren Bereich aufziehen, um einen Raum / Prüfbereich anzulegen. "
                   "Bereiche verschieben, an der Ecke unten rechts die Größe ändern, "
                   "Doppelklick = Eigenschaften, Entf = löschen.<br><br>"
                   "Tipp: Mit <b>Raumreihe</b> legen Sie viele gleiche Büros auf einmal an. "
                   "Ein eingescannter Plan kann als <b>Hintergrundbild</b> hinterlegt werden."
                   if self.edit_mode else
                   "Bereich anklicken, um den Status zu setzen (Strg+Klick für mehrere). "
                   "Rechtsklick öffnet das Schnellmenü.") + "</span>")
            return
        if len(sel) > 1:
            self.p_title.setText(f"{len(sel)} Bereiche ausgewählt")
            self.p_info.setText(", ".join(_html.escape(i.data_["name"]) for i in sel))
            self.claim_btn.setVisible(False)
            return
        d = sel[0].data_
        self.p_title.setText(d["name"])
        rows = [("Status", _status_text(d) + (" – <b style='color:#d0271d'>überfällig</b>" if d["overdue"] else "")),
                ("Prüfer", _html.escape(d["inspector"]) or "<i>nicht zugewiesen</i>"),
                ("Größe", f"{d['w']:g} × {d['h']:g} m")]
        if d["devices"]:
            rows.append(("Prüflinge (ca.)", str(d["devices"])))
        if d["note"]:
            rows.append(("Bemerkung", _html.escape(d["note"]).replace("\n", "<br>")))
        if d["notes"]:
            rows.append(("Hinweise", _html.escape(d["notes"]).replace("\n", "<br>")))
        if d["status_by"]:
            when = d["status_at"].strftime("%d.%m.%Y %H:%M") if d["status_at"] else ""
            rows.append(("Geändert", f"{_html.escape(d['status_by'])} {when}"))
        self.p_info.setText("<table cellspacing=2>" + "".join(
            f"<tr><td style='color:#6a7785;padding-right:8px'>{k}</td><td>{v}</td></tr>" for k, v in rows)
            + "</table>")
        self.claim_btn.setVisible(d["inspector_id"] is None and context.user_id is not None)
        hist = run(self, lambda s: [(h.timestamp, h.username, h.status, h.progress_pct, h.appointment_date, h.note)
                                    for h in ins.history(s, d["id"])])
        for ts, user, st, pct, date, note in (hist if isinstance(hist, list) else []):
            text = f"{ts:%d.%m.%Y %H:%M}  {user}\n{STATUS_ICONS.get(st, '')} {m.AREA_STATUS.get(st, st)}"
            if st == "partial":
                text += f" ({pct} %)"
            if date:
                text += f" am {fmt_date(date)}"
            if note:
                text += f"\n{note}"
            self.p_hist.addItem(text)

    def fill_menu(self, menu: QMenu):
        if self.edit_mode:
            menu.addAction("Eigenschaften …", lambda: self.open_area())
            menu.addAction("Prüfer zuweisen …", self.assign_selected)
            menu.addAction("Duplizieren", self.duplicate_selected)
            menu.addSeparator()
            menu.addAction("Löschen", self.delete_selected)
        else:
            for key, label in m.AREA_STATUS.items():
                menu.addAction(f"{STATUS_ICONS[key]}  {label}" + (" …" if key in ("partial", "appointment", "clarify") else ""),
                               lambda k=key: self.status_action(k))

    # -- Laden --------------------------------------------------------------
    def refresh(self):
        def load(s):
            plans = [(p.id, f"{p.name}" + (f" – {p.location}" if p.location else ""))
                     for p in ins.floor_plans(s)]
            insp = [(u.id, _user_label(u)) for u in ins.inspectors(s)]
            return plans, insp
        res = run(self, load)
        if not isinstance(res, tuple):
            return
        plans, self._inspectors = res
        self._loading = True
        current = self.plan_id
        self.plan_combo.clear()
        for pid, label in plans:
            self.plan_combo.addItem(label, pid)
        idx = self.plan_combo.findData(current)
        if idx < 0 and plans:
            last = run(self, lambda s: get_setting(s, f"inspection_last_plan_{context.username}", ""))
            idx = max(0, self.plan_combo.findData(int(last)) if isinstance(last, str) and last.isdigit() else 0)
        self.plan_combo.setCurrentIndex(idx)
        self._loading = False
        self.load_plan(self.plan_combo.currentData(), fit=current != self.plan_combo.currentData())

    def _plan_selected(self, *_):
        if not self._loading:
            self.load_plan(self.plan_combo.currentData(), fit=True)

    def load_plan(self, plan_id: int | None, fit: bool = False):
        selected_ids = {i.area_id for i in self.selected()}

        def load(s):
            if not plan_id:
                return None
            plan = s.get(m.FloorPlan, plan_id)
            if plan is None:
                return None
            bg = ins.background(s, plan_id) if plan.has_background and self._bg_plan != (plan_id, plan.version) else None
            return ({"id": plan.id, "version": plan.version, "name": plan.name, "w": plan.width_m,
                     "h": plan.height_m, "has_bg": plan.has_background},
                    [_area_dict(a) for a in ins.areas(s, plan_id)], bg)
        res = run(self, load)
        self.scene.blockSignals(True)
        for item in self.items.values():
            self.scene.removeItem(item)
        self.items = {}
        if not isinstance(res, tuple):
            self.plan_id, self.plan_size, self.plan_name = None, (0.0, 0.0), ""
            self._set_background(None, None)
            self.scene.setSceneRect(QRectF(0, 0, 800, 400))
            self.scene.blockSignals(False)
            self.summary.setText("Noch kein Grundriss vorhanden – mit „Neu …“ anlegen.")
            self.scene.update()
            self.update_panel()
            return
        plan, area_list, bg = res
        changed_plan = plan["id"] != self.plan_id
        self.plan_id, self.plan_version, self.plan_name = plan["id"], plan["version"], plan["name"]
        self.plan_size = (plan["w"], plan["h"])
        if changed_plan:
            run(self, lambda s: set_setting(s, f"inspection_last_plan_{context.username}", str(plan["id"])))
        if not plan["has_bg"]:
            self._set_background(None, None)
        elif bg is not None:
            self._set_background(bg, (plan["id"], plan["version"]))
        margin = 2 * PX
        self.scene.setSceneRect(QRectF(-margin, -margin, plan["w"] * PX + 2 * margin, plan["h"] * PX + 2 * margin))
        for d in area_list:
            item = AreaItem(d, self)
            self.scene.addItem(item)
            item.setSelected(d["id"] in selected_ids)
            self.items[d["id"]] = item
        self.scene.blockSignals(False)
        self.scene.update()
        self._update_summary(area_list)
        self.update_panel()
        if fit or changed_plan:
            QTimer.singleShot(0, self.view.fit)

    def _set_background(self, data: bytes | None, key):
        if self._bg_item is not None and (data is not None or key is None):
            self.scene.removeItem(self._bg_item)
            self._bg_item = None
            self._bg_plan = None
        if data is None:
            return
        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        pix = pix.scaledToWidth(int(self.plan_size[0] * PX), Qt.TransformationMode.SmoothTransformation)
        self._bg_item = self.scene.addPixmap(pix)
        self._bg_item.setZValue(-10)
        self._bg_item.setOpacity(self.bg_opacity.value() / 100)
        self._bg_plan = key

    def _update_summary(self, area_list: list[dict]):
        self.summary.setText(_summary_text(area_list, with_mine=True))

    def _auto_refresh(self):
        if (self.isVisible() and not self.edit_mode and QApplication.activeModalWidget() is None
                and QApplication.mouseButtons() == Qt.MouseButton.NoButton and self.plan_id):
            self.load_plan(self.plan_id)

    # -- Modus -------------------------------------------------------------
    def set_edit_mode(self, edit: bool):
        self.edit_mode = edit
        self.prep_tools.setVisible(edit)
        self.check_tools.setVisible(not edit)
        for item in self.items.values():
            item.set_edit_mode(edit)
        self.scene.update()
        self.update_panel()
        if not edit and self.plan_id:
            self.load_plan(self.plan_id)

    def _toggle_mine(self, on: bool):
        self.only_mine = on
        self.scene.update()

    # -- Grundrisse verwalten ----------------------------------------------
    def new_plan(self):
        dlg = FormDialog(self, "Neuer Grundriss", _plan_fields(), {"width_m": 40, "height_m": 20})
        if dlg.exec():
            v = _plan_values(dlg.values())
            plan = run(self, lambda s: ins.save_floor_plan(s, None, v))
            if plan:
                self.plan_id = plan.id
                self.refresh()
                self.mode_prepare.setChecked(True)

    def edit_plan(self):
        if not self.plan_id:
            return
        values = run(self, lambda s: (lambda p: {"name": p.name, "location": p.location, "width_m": p.width_m,
                                                 "height_m": p.height_m, "notes": p.notes, "v": p.version})(
            s.get(m.FloorPlan, self.plan_id)))
        if not isinstance(values, dict):
            return
        dlg = FormDialog(self, "Grundriss – Eigenschaften", _plan_fields(), values)
        if dlg.exec():
            v = _plan_values(dlg.values())
            if run(self, lambda s: ins.save_floor_plan(s, self.plan_id, v, values["v"])):
                self._bg_plan = None
            self.refresh()

    def copy_plan(self):
        if not self.plan_id:
            return
        name, ok = QInputDialog.getText(self, "Grundriss kopieren",
                                        "Name der Kopie (alle Bereiche werden auf „offen“ gesetzt, "
                                        "z. B. für die Wiederholungsprüfung):",
                                        text=f"{self.plan_name} {dt.date.today().year + 1}")
        if ok and name.strip():
            plan = run(self, lambda s: ins.copy_floor_plan(s, self.plan_id, name.strip()))
            if plan:
                self.plan_id = plan.id
                self.refresh()

    def delete_plan(self):
        if self.plan_id and confirm(self, f"Grundriss „{self.plan_name}“ mit allen Bereichen und dem "
                                          "Statusverlauf endgültig löschen?"):
            if run(self, lambda s: ins.delete_floor_plan(s, self.plan_id)):
                self.plan_id = None
                self.refresh()

    def load_background(self):
        if not self.plan_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Plan / Fluchtwegplan als Bild laden", "",
                                              "Bilder (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        with open(path, "rb") as fh:
            data = fh.read()
        img = QImage.fromData(data)
        if img.isNull():
            QMessageBox.warning(self, "Bild", "Die Datei konnte nicht als Bild gelesen werden.")
            return
        # Tiefe passend zum Seitenverhältnis des Bildes vorschlagen
        w = self.plan_size[0]
        h = ins.snap(w * img.height() / img.width())
        if abs(h - self.plan_size[1]) >= ins.GRID and QMessageBox.question(
                self, "Seitenverhältnis",
                f"Das Bild hat ein anderes Seitenverhältnis als der Grundriss.\n"
                f"Tiefe des Grundrisses von {self.plan_size[1]:g} m auf {h:g} m anpassen?") == QMessageBox.StandardButton.Yes:
            run(self, lambda s: ins.save_floor_plan(s, self.plan_id, {
                **(lambda p: {"name": p.name, "location": p.location, "notes": p.notes})(s.get(m.FloorPlan, self.plan_id)),
                "width_m": w, "height_m": max(2.0, h)}))
        if run(self, lambda s: ins.set_background(s, self.plan_id, data)):
            self._bg_plan = None
            self.load_plan(self.plan_id, fit=True)

    def remove_background(self):
        if self.plan_id and run(self, lambda s: ins.set_background(s, self.plan_id, None)):
            self.load_plan(self.plan_id)

    # -- Bereiche bearbeiten -------------------------------------------------
    def _next_name(self) -> str:
        return f"Raum {len(self.items) + 1}"

    def create_area(self, x, y, w, h):
        name, ok = QInputDialog.getText(self, "Neuer Prüfbereich",
                                        f"Bezeichnung ({w:g} × {h:g} m):", text=self._next_name())
        if ok and name.strip():
            area = run(self, lambda s: ins.add_area(s, self.plan_id, name, x, y, w, h))
            self.load_plan(self.plan_id)
            if area and area.id in self.items:
                self.items[area.id].setSelected(True)

    def save_geometry(self, item: AreaItem):
        x, y, w, h = item.geometry_m()
        run(self, lambda s: ins.move_area(s, item.area_id, x, y, w, h, item.data_["version"]))
        self.load_plan(self.plan_id)

    def add_row(self):
        if not self.plan_id:
            return
        dlg = RowDialog(self, self.plan_size, self._inspectors)
        if dlg.exec():
            v = dlg.values()
            if run(self, lambda s: ins.add_row(s, self.plan_id, **v)):
                self.load_plan(self.plan_id)

    def open_area(self, area_id: int | None = None):
        if area_id is None:
            sel = self.selected()
            if len(sel) != 1:
                return
            area_id = sel[0].area_id
        if not self.edit_mode:
            item = self.items.get(area_id)
            if item:
                self.scene.clearSelection()
                item.setSelected(True)
                self.status_action(item.data_["status"] if item.data_["status"] != "open" else "done", ask=True)
            return
        d = self.items[area_id].data_
        dlg = FormDialog(self, "Prüfbereich – Eigenschaften", [
            Field("name", "Bezeichnung", required=True),
            Field("inspector_id", "Prüfer", "choice", self._inspectors),
            Field("expected_devices", "Prüflinge (ca. Anzahl)", "int"),
            Field("notes", "Hinweise (Zugang, Ansprechpartner …)", "multiline"),
        ], {"name": d["name"], "inspector_id": d["inspector_id"], "expected_devices": d["devices"],
            "notes": d["notes"]})
        if dlg.exec():
            v = dlg.values()
            run(self, lambda s: ins.update_area(s, area_id, v, d["version"]))
            self.load_plan(self.plan_id)

    def assign_selected(self):
        sel = self.selected()
        if not sel:
            return
        options = ["— nicht zugewiesen —"] + [label for _, label in self._inspectors]
        choice, ok = QInputDialog.getItem(self, "Prüfer zuweisen",
                                          f"Prüfer für {len(sel)} Bereich(e):", options, 0, False)
        if ok:
            idx = options.index(choice)
            uid = self._inspectors[idx - 1][0] if idx else None
            run(self, lambda s: ins.assign(s, [i.area_id for i in sel], uid))
            self.load_plan(self.plan_id)

    def duplicate_selected(self):
        sel = self.selected()

        def dup(s):
            for i in sel:
                d = i.data_
                x = d["x"] + d["w"] if d["x"] + 2 * d["w"] <= self.plan_size[0] else d["x"]
                y = d["y"] if x != d["x"] else min(d["y"] + d["h"], self.plan_size[1] - d["h"])
                ins.add_area(s, self.plan_id, d["name"] + " (Kopie)", x, y, d["w"], d["h"],
                             d["inspector_id"], d["devices"], d["notes"])
        if sel and run(self, dup):
            self.load_plan(self.plan_id)

    def delete_selected(self):
        sel = self.selected()
        if sel and confirm(self, f"{len(sel)} Bereich(e) löschen?\n"
                                 + ", ".join(i.data_["name"] for i in sel[:10])):
            run(self, lambda s: [ins.delete_area(s, i.area_id) for i in sel])
            self.load_plan(self.plan_id)

    # -- Status --------------------------------------------------------------
    def status_action(self, status: str, ask: bool | None = None):
        sel = self.selected()
        if not sel:
            return
        if ask is None:
            ask = status in ("partial", "appointment", "clarify")
        values = {"status": status, "date": None, "progress": None, "note": ""}
        if ask:
            title = sel[0].data_["name"] if len(sel) == 1 else f"{len(sel)} Bereiche"
            dlg = StatusDialog(self, f"Status – {title}", status, sel[0].data_ if len(sel) == 1 else None)
            if not dlg.exec():
                return
            values = dlg.values()
        targets = [(i.area_id, i.data_["version"]) for i in sel]

        def apply(s):
            for aid, version in targets:
                ins.set_status(s, aid, values["status"], date=values["date"], note=values["note"],
                               progress=values["progress"], version=version)
        run(self, apply)
        self.load_plan(self.plan_id)

    def claim(self):
        sel = self.selected()
        if len(sel) == 1:
            run(self, lambda s: ins.claim(s, sel[0].area_id, sel[0].data_["version"]))
            self.load_plan(self.plan_id)

    # -- Bericht -------------------------------------------------------------
    def plan_image(self, max_width: int = 1600) -> QImage | None:
        w, h = self.plan_size
        if not w:
            return None
        src = QRectF(-4, -4, w * PX + 8, h * PX + 8)
        scale = min(2.0, max_width / src.width())
        img = QImage(int(src.width() * scale), int(src.height() * scale), QImage.Format.Format_ARGB32)
        img.fill(QColor("white"))
        sel = self.selected()
        self.scene.clearSelection()
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene.render(p, QRectF(0, 0, img.width(), img.height()), src)
        p.end()
        for i in sel:
            i.setSelected(True)
        return img

    def report(self):
        if not self.plan_id:
            QMessageBox.information(self, "Prüfbericht", "Bitte zuerst einen Grundriss wählen.")
            return None
        res = run(self, lambda s: ([_area_dict(a) for a in ins.areas(s, self.plan_id)],
                                   s.get(m.FloorPlan, self.plan_id).location,
                                   get_setting(s, "company_name")))
        if not isinstance(res, tuple):
            return None
        area_list, location, company = res
        img = self.plan_image()
        html = report_html(self.plan_name, location, company, area_list, with_image=img is not None)
        safe = "".join(c if c.isalnum() else "_" for c in self.plan_name)
        return html, f"Pruefstand_{safe}_{dt.date.today():%Y-%m-%d}.pdf", ({"plan.png": img} if img else {})


def report_html(plan_name: str, location: str, company: str, area_list: list[dict], with_image=False) -> str:
    e = _html.escape
    counts = {k: 0 for k in m.AREA_STATUS}
    for a in area_list:
        counts[a["status"]] += 1
    total = len(area_list)
    pct = round(sum(a["progress"] for a in area_list) / total) if total else 0
    rows = "".join(
        f"<tr><td>{e(a['name'])}</td>"
        f"<td style='background:{m.AREA_COLORS[a['status']][0]}'>{e(_status_text(a))}"
        f"{' – überfällig' if a['overdue'] else ''}</td>"
        f"<td>{e(a['inspector'])}</td><td align=right>{a['devices'] or ''}</td>"
        f"<td>{e(a['note'])}</td><td>{e(a['status_by'])} "
        f"{a['status_at'].strftime('%d.%m.%Y') if a['status_at'] else ''}</td></tr>"
        for a in sorted(area_list, key=lambda a: ins.natural_key(a["name"])))
    chips = " &nbsp; ".join(f"<span style='background:{m.AREA_COLORS[k][0]}'>&nbsp;{v}&nbsp;</span>"
                            f"&nbsp;{m.AREA_STATUS[k]}" for k, v in counts.items())
    return f"""
<html><body style="font-family:Arial; font-size:9pt">
<p style="color:#6a7785">{e(company)}</p>
<h2 style="color:#1d2d3e">Prüfstand DGUV V3 – {e(plan_name)}</h2>
<p>{e(location)} &nbsp;·&nbsp; Stand: {dt.datetime.now():%d.%m.%Y %H:%M} &nbsp;·&nbsp;
{total} Bereiche &nbsp;·&nbsp; Gesamtfortschritt <b>{pct} %</b></p>
<p>{chips}</p>
{'<p><img src="plan.png" width="900"></p>' if with_image else ''}
<table border="1" cellspacing="0" cellpadding="4" width="100%" style="border-collapse:collapse">
<tr style="background:#eef1f4"><th align=left>Bereich</th><th align=left>Status</th>
<th align=left>Prüfer</th><th>Prüflinge</th><th align=left>Bemerkung</th><th align=left>Geändert</th></tr>
{rows}
</table>
</body></html>"""


# --------------------------------------------------------------------------
# Seite: Bereichsliste
# --------------------------------------------------------------------------

class AreaListPage(ListPage):
    title = "Prüfplanung DGUV V3 – Bereichsliste"
    columns = ["Bereich", "Grundriss", "Status", "Fortschritt", "Termin", "Prüfer", "Prüflinge",
               "Geändert von", "Geändert am", "Bemerkung"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_button("✔ Erledigt", lambda: self._status("done"), primary=True)
        self.add_button("Status ändern …", lambda: self._status(None))
        self.add_button("Übernehmen", self._claim)
        self._rows: dict[int, dict] = {}

    def extra_filters(self):
        self.f_plan = QComboBox()
        self.f_who = QComboBox()
        self.f_status = QComboBox()
        self.f_status.addItem("Alle Status", None)
        for k, v in m.AREA_STATUS.items():
            self.f_status.addItem(f"{STATUS_ICONS[k]} {v}", k)
        self._filters_loaded = False
        for w in (self.f_plan, self.f_who, self.f_status):
            w.currentIndexChanged.connect(lambda *_: not self._filters_loaded or self.refresh())
            self.toolbar.addWidget(w)

    def _load_filters(self, s):
        self.f_plan.blockSignals(True)
        self.f_who.blockSignals(True)
        self.f_plan.addItem("Alle Grundrisse", None)
        for p in ins.floor_plans(s):
            self.f_plan.addItem(p.name, p.id)
        self.f_who.addItem("Alle Prüfer", None)
        if context.user_id:
            self.f_who.addItem("Meine Bereiche", context.user_id)
        self.f_who.addItem("Nicht zugewiesen", -1)
        for u in ins.inspectors(s):
            if u.id != context.user_id:
                self.f_who.addItem(_user_label(u), u.id)
        if context.role == "inspector":
            self.f_who.setCurrentIndex(1)
        self.f_plan.blockSignals(False)
        self.f_who.blockSignals(False)
        self._filters_loaded = True

    def load(self, s, text):
        if not self._filters_loaded:
            self._load_filters(s)
        rows = []
        self._rows = {}
        for a in ins.areas(s, self.f_plan.currentData(), self.f_who.currentData(),
                           self.f_status.currentData(), text):
            d = _area_dict(a)
            self._rows[a.id] = d
            rows.append((a.id, [d["name"], d["plan"], f"{STATUS_ICONS[d['status']]} {m.AREA_STATUS[d['status']]}"
                                + (" ⚠ überfällig" if d["overdue"] else ""),
                                (f"{d['progress']} %", d["progress"]), dcell(d["date"]), d["inspector"],
                                (str(d["devices"] or ""), d["devices"]), d["status_by"],
                                (d["status_at"].strftime("%d.%m.%Y %H:%M") if d["status_at"] else "",
                                 d["status_at"].timestamp() if d["status_at"] else 0), d["note"]]))
        return rows

    def refresh(self):
        super().refresh()
        for r in range(self.table.rowCount()):
            aid = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            d = self._rows.get(aid)
            if d:
                item: QTableWidgetItem = self.table.item(r, 2)
                item.setBackground(QColor(m.AREA_COLORS[d["status"]][0]))
                if d["overdue"]:
                    item.setForeground(QColor("#d0271d"))
        self.status.setText(_summary_text(list(self._rows.values())))

    def on_double_click(self, *_):
        self._status(None)

    def _status(self, status):
        aid = self.require_selection()
        if aid is None:
            return
        d = self._rows[aid]
        values = {"status": status, "date": None, "progress": None, "note": ""}
        if status is None:
            dlg = StatusDialog(self, f"Status – {d['name']}", d["status"], d)
            if not dlg.exec():
                return
            values = dlg.values()
        run(self, lambda s: ins.set_status(s, aid, values["status"], date=values["date"], note=values["note"],
                                           progress=values["progress"], version=d["version"]))
        self.refresh()

    def _claim(self):
        aid = self.require_selection()
        if aid is not None:
            run(self, lambda s: ins.claim(s, aid, self._rows[aid]["version"]))
            self.refresh()


def inspection_module():
    from .modules import TabsPage
    return TabsPage([("Grundriss", FloorPlanPage()), ("Bereichsliste", AreaListPage())])

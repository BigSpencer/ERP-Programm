"""Wiederverwendbare UI-Bausteine: Tabellenseiten, Formulardialoge, Fehlerbehandlung."""
from __future__ import annotations

import datetime as dt
import traceback
from decimal import Decimal
from typing import Any, Callable

from PySide6.QtCore import QDate, Qt, QTimer, QUrl
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from .. import db
from ..services import BusinessError
from ..services.reports import money

NULL_DATE = QDate(1900, 1, 1)


# --------------------------------------------------------------------------
# Formatierung
# --------------------------------------------------------------------------

def fmt_money(v) -> str:
    return money(v)


def fmt_qty(v) -> str:
    d = Decimal(v or 0)
    text = f"{d:,.3f}".rstrip("0").rstrip(".")
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def to_qdate(d: dt.date | None) -> QDate:
    return QDate(d.year, d.month, d.day) if d else NULL_DATE


def from_qdate(q: QDate) -> dt.date | None:
    if q == NULL_DATE:
        return None
    return dt.date(q.year(), q.month(), q.day())


# --------------------------------------------------------------------------
# Fehlerbehandlung
# --------------------------------------------------------------------------

def run(parent: QWidget | None, fn: Callable, *args, success: str | None = None) -> Any:
    """Führt ``fn(session, *args)`` in einer Transaktion aus und zeigt Fehler an.

    Gibt das Ergebnis zurück oder ``None`` bei Fehlern.
    """
    try:
        with db.session_scope() as s:
            result = fn(s, *args)
        if success:
            QMessageBox.information(parent, "Erfolg", success)
        return result if result is not None else True
    except BusinessError as exc:
        QMessageBox.warning(parent, "Hinweis", str(exc))
    except StaleDataError:
        QMessageBox.warning(parent, "Konflikt",
                            "Der Datensatz wurde zwischenzeitlich von einem anderen Benutzer "
                            "geändert. Bitte aktualisieren und erneut versuchen.")
    except OperationalError as exc:
        QMessageBox.critical(parent, "Datenbank nicht erreichbar",
                             f"Die Verbindung zur Datenbank ist fehlgeschlagen:\n\n{exc.orig}")
    except SQLAlchemyError as exc:
        QMessageBox.critical(parent, "Datenbankfehler", str(exc)[:1500])
    except Exception:  # pragma: no cover - Sicherheitsnetz
        QMessageBox.critical(parent, "Unerwarteter Fehler", traceback.format_exc()[-2000:])
    return None


def confirm(parent, text: str) -> bool:
    return QMessageBox.question(parent, "Bestätigen", text) == QMessageBox.StandardButton.Yes


# --------------------------------------------------------------------------
# Eingabefelder
# --------------------------------------------------------------------------

class SearchCombo(QComboBox):
    """Auswahlfeld mit Suche (z. B. Kunde, Artikel)."""

    def __init__(self, options: list[tuple[Any, str]], allow_empty=False, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer().setCompletionMode(self.completer().CompletionMode.PopupCompletion)
        if allow_empty:
            self.addItem("", None)
        for value, label in options:
            self.addItem(label, value)
        self.setMinimumWidth(220)

    def value(self):
        idx = self.findText(self.currentText())
        return self.itemData(idx) if idx >= 0 else None

    def set_value(self, value):
        idx = self.findData(value)
        self.setCurrentIndex(idx if idx >= 0 else 0)


class DateField(QDateEdit):
    def __init__(self, value: dt.date | None = None, optional=False, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd.MM.yyyy")
        if optional:
            self.setMinimumDate(NULL_DATE)
            self.setSpecialValueText("—")
        self.setDate(to_qdate(value) if (value or optional) else QDate.currentDate())

    def value(self):
        return from_qdate(self.date())


class Field:
    """Beschreibung eines Formularfelds."""

    def __init__(self, key, label, kind="text", options=None, required=False, readonly=False):
        self.key, self.label, self.kind = key, label, kind
        self.options = options or []
        self.required, self.readonly = required, readonly


class FormDialog(QDialog):
    """Generischer Erfassungsdialog auf Basis einer Feldliste."""

    def __init__(self, parent, title: str, fields: list[Field], values: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.fields = fields
        self.widgets: dict[str, QWidget] = {}
        values = values or {}
        form = QFormLayout()
        for f in fields:
            w = self._make(f, values.get(f.key))
            self.widgets[f.key] = w
            form.addRow(f.label + (" *" if f.required else ""), w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _make(self, f: Field, value):
        if f.kind == "multiline":
            w = QPlainTextEdit(value or "")
            w.setFixedHeight(70)
        elif f.kind == "bool":
            w = QCheckBox()
            w.setChecked(True if value is None else bool(value))
        elif f.kind == "int":
            w = QSpinBox()
            w.setRange(0, 10_000)
            w.setValue(int(value or 0))
        elif f.kind == "choice":
            w = SearchCombo(f.options, allow_empty=not f.required)
            if value is not None:
                w.set_value(value)
        elif f.kind == "date":
            w = DateField(value)
        elif f.kind == "password":
            w = QLineEdit()
            w.setEchoMode(QLineEdit.EchoMode.Password)
        else:  # text, money, decimal
            if f.kind == "money" and value is not None:
                value = fmt_money(value)
            elif f.kind == "decimal" and value is not None:
                value = fmt_qty(value)
            w = QLineEdit("" if value is None else str(value))
            if f.kind in ("money", "decimal"):
                w.setAlignment(Qt.AlignmentFlag.AlignRight)
        if f.readonly:
            w.setEnabled(False)
        return w

    def values(self) -> dict:
        out = {}
        for f in self.fields:
            w = self.widgets[f.key]
            if isinstance(w, QPlainTextEdit):
                out[f.key] = w.toPlainText()
            elif isinstance(w, QCheckBox):
                out[f.key] = w.isChecked()
            elif isinstance(w, QSpinBox):
                out[f.key] = w.value()
            elif isinstance(w, (SearchCombo, DateField)):
                out[f.key] = w.value()
            else:
                out[f.key] = w.text()
        return out

    def _accept(self):
        vals = self.values()
        for f in self.fields:
            if f.required and vals.get(f.key) in (None, ""):
                QMessageBox.warning(self, "Pflichtfeld", f"Bitte '{f.label}' ausfüllen.")
                return
        self.accept()


# --------------------------------------------------------------------------
# Tabellen
# --------------------------------------------------------------------------

class _NumItem(QTableWidgetItem):
    """Tabellenzelle, die numerisch statt alphabetisch sortiert."""

    def __lt__(self, other):
        a = self.data(Qt.ItemDataRole.UserRole + 1)
        b = other.data(Qt.ItemDataRole.UserRole + 1)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


class DataTable(QTableWidget):
    def __init__(self, columns: list[str], parent=None):
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)

    def fill(self, rows: list[tuple[Any, list]]):
        """rows = [(id, [Zellwerte…])]. Zahlen werden rechtsbündig dargestellt."""
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for r, (key, cells) in enumerate(rows):
            for c, value in enumerate(cells):
                item = _NumItem() if isinstance(value, tuple) else QTableWidgetItem()
                if isinstance(value, tuple):          # (Anzeige, Sortierwert)
                    text, sort = value
                    item.setData(Qt.ItemDataRole.DisplayRole, text)
                    item.setData(Qt.ItemDataRole.UserRole + 1, sort)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setData(Qt.ItemDataRole.DisplayRole, "" if value is None else str(value))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, key)
                self.setItem(r, c, item)
        self.setSortingEnabled(True)
        self.resizeColumnsToContents()
        for c in range(self.columnCount()):
            if self.columnWidth(c) > 320:
                self.setColumnWidth(c, 320)

    def selected_id(self):
        row = self.currentRow()
        if row < 0 or self.item(row, 0) is None:
            return None
        return self.item(row, 0).data(Qt.ItemDataRole.UserRole)


def num(value, kind="money"):
    """Zelle für Zahlen: (Anzeige, Sortierwert)."""
    return (fmt_money(value) if kind == "money" else fmt_qty(value), float(value or 0))


class ListPage(QWidget):
    """Basisklasse: Suchfeld + Aktionsleiste + Tabelle."""

    title = ""
    columns: list[str] = []

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        head = QLabel(self.title)
        head.setObjectName("pageTitle")
        lay.addWidget(head)
        self.toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen …")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        self._search_timer = QTimer(self, singleShot=True, interval=300)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.toolbar.addWidget(self.search)
        self.extra_filters()
        self.toolbar.addStretch(1)
        lay.addLayout(self.toolbar)
        self.table = DataTable(self.columns)
        self.table.doubleClicked.connect(self.on_double_click)
        lay.addWidget(self.table)
        self.status = QLabel()
        self.status.setObjectName("hint")
        lay.addWidget(self.status)

    def extra_filters(self):
        pass

    def add_button(self, text: str, slot, primary=False) -> QPushButton:
        btn = QPushButton(text)
        if primary:
            btn.setObjectName("primary")
        btn.clicked.connect(slot)
        self.toolbar.addWidget(btn)
        return btn

    def refresh(self):
        rows = run(self, self.load, self.search.text().strip())
        if rows is not None and rows is not True:
            self.table.fill(rows)
            self.status.setText(f"{len(rows)} Einträge")
        elif rows is True:
            self.table.fill([])
            self.status.setText("0 Einträge")

    def load(self, s, text: str) -> list:
        return []

    def on_double_click(self, *_):
        pass

    def require_selection(self):
        sel = self.table.selected_id()
        if sel is None:
            QMessageBox.information(self, "Auswahl", "Bitte zuerst einen Eintrag auswählen.")
        return sel


# --------------------------------------------------------------------------
# Belegausgabe
# --------------------------------------------------------------------------

def _document(html: str, resources: dict | None = None) -> QTextDocument:
    """HTML-Dokument; ``resources`` = {Name: QImage} für <img src="Name">."""
    doc = QTextDocument()
    for name, image in (resources or {}).items():
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
    doc.setHtml(html)
    return doc


def export_pdf(parent, html: str, filename: str, resources: dict | None = None,
               landscape: bool = False) -> None:
    from PySide6.QtPrintSupport import QPrinter

    path, _ = QFileDialog.getSaveFileName(parent, "Als PDF speichern", filename, "PDF (*.pdf)")
    if not path:
        return
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    orientation = QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait
    printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), orientation,
                                      printer.pageLayout().margins()))
    _document(html, resources).print_(printer)
    QMessageBox.information(parent, "PDF erstellt", f"Gespeichert unter:\n{path}")


def print_html(parent, html: str, resources: dict | None = None) -> None:
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if QPrintDialog(printer, parent).exec() == QDialog.DialogCode.Accepted:
        _document(html, resources).print_(printer)


def output_menu_button(parent, get_html: Callable[[], tuple | None], landscape: bool = False) -> QToolButton:
    """Schaltfläche 'Ausgabe' mit Drucken / PDF.

    ``get_html`` liefert (html, dateiname) oder (html, dateiname, {bildname: QImage})."""
    from PySide6.QtWidgets import QMenu

    btn = QToolButton(parent)
    btn.setText("Ausgabe ▾")
    btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(btn)

    def _pdf():
        res = get_html()
        if res:
            export_pdf(parent, res[0], res[1], res[2] if len(res) > 2 else None, landscape)

    def _print():
        res = get_html()
        if res:
            print_html(parent, res[0], res[2] if len(res) > 2 else None)

    menu.addAction("Als PDF speichern …", _pdf)
    menu.addAction("Drucken …", _print)
    btn.setMenu(menu)
    return btn


def dcell(d):
    """Zelle für Datumswerte mit korrekter Sortierung."""
    return (fmt_date(d), d.toordinal() if d else 0)

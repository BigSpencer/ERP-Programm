"""Hauptanwendung: Verbindung, Anmeldung, Hauptfenster mit Modulnavigation."""
from __future__ import annotations

import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QSize, Qt, QTimer, QTranslator
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, config, db
from .. import models as m
from ..services import BusinessError, auth, get_setting, set_user
from .dealer import (
    CockpitPage, ShowroomPage, UsedPurchasesPage, VehicleModelsPage, VehicleOrdersPage, VehiclesPage,
    sales_module as vehicle_sales_module,
)
from .documents import InvoicesPage, PurchaseOrdersPage, SalesOrdersPage
from .masterdata import CustomersPage, ProductsPage, SuppliersPage
from .modules import DashboardPage, admin_module, finance_module, inventory_module
from .style import apply_theme, spaced
from .widgets import FormDialog, Field, run


# --------------------------------------------------------------------------
# Verbindung
# --------------------------------------------------------------------------

class ConnectionDialog(QDialog):
    """Einrichtung der Verbindung zur gemeinsamen Datenbank."""

    def __init__(self, cfg: config.DatabaseConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} – Datenbankverbindung")
        self.setMinimumWidth(480)
        self.cfg = cfg
        info = QLabel("Alle Arbeitsplätze greifen auf dieselbe Datenbank zu. Empfohlen ist ein "
                      "zentraler <b>PostgreSQL</b>-Server. SQLite eignet sich nur für "
                      "Einzelplatz/Test.")
        info.setWordWrap(True)
        self.backend = QComboBox()
        self.backend.addItem("PostgreSQL (empfohlen, Mehrbenutzer)", "postgresql")
        self.backend.addItem("MySQL / MariaDB", "mysql")
        self.backend.addItem("SQLite-Datei (Einzelplatz)", "sqlite")
        self.backend.setCurrentIndex(max(0, self.backend.findData(cfg.backend)))
        self.host = QLineEdit(cfg.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(cfg.port)
        self.database = QLineEdit(cfg.database)
        self.user = QLineEdit(cfg.user)
        self.password = QLineEdit(cfg.password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.path = QLineEdit(cfg.sqlite_path)
        self.path.setPlaceholderText(str(config.config_dir() / "erp.sqlite"))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        prow = QHBoxLayout()
        prow.addWidget(self.path)
        prow.addWidget(browse)
        self.form = QFormLayout()
        self.form.addRow("Datenbanktyp", self.backend)
        self.form.addRow("Server", self.host)
        self.form.addRow("Port", self.port)
        self.form.addRow("Datenbank", self.database)
        self.form.addRow("Benutzer", self.user)
        self.form.addRow("Passwort", self.password)
        self.form.addRow("Datei", prow)
        test = QPushButton("Verbindung testen")
        test.clicked.connect(self._test)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.addButton(test, QDialogButtonBox.ButtonRole.ActionRole)
        box.accepted.connect(self._ok)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(self.form)
        lay.addWidget(box)
        self.backend.currentIndexChanged.connect(self._toggle)
        self._toggle()

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "SQLite-Datei", self.path.text() or "erp.sqlite",
                                              "SQLite (*.sqlite *.db)",
                                              options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.path.setText(path)

    def _toggle(self):
        sqlite = self.backend.currentData() == "sqlite"
        for w in (self.host, self.port, self.database, self.user, self.password):
            w.setEnabled(not sqlite)
        self.path.setEnabled(sqlite)
        if not sqlite:
            default = 5432 if self.backend.currentData() == "postgresql" else 3306
            if self.port.value() in (5432, 3306):
                self.port.setValue(default)

    def result_config(self) -> config.DatabaseConfig:
        return config.DatabaseConfig(
            backend=self.backend.currentData(), host=self.host.text().strip(), port=self.port.value(),
            database=self.database.text().strip(), user=self.user.text().strip(),
            password=self.password.text(), sqlite_path=self.path.text().strip())

    def _try(self) -> bool:
        cfg = self.result_config()
        if cfg.backend == "sqlite":
            config.config_dir().mkdir(parents=True, exist_ok=True)
        try:
            eng = db.make_engine(cfg.url())
            with eng.connect():
                pass
            eng.dispose()
            return True
        except Exception as exc:  # noqa: BLE001 - Anzeige für den Anwender
            msg = str(getattr(exc, "orig", exc))
            if isinstance(exc, ModuleNotFoundError):
                msg = f"Datenbanktreiber fehlt: {exc.name}"
            QMessageBox.critical(self, "Verbindung fehlgeschlagen", msg[:1500])
            return False

    def _test(self):
        if self._try():
            QMessageBox.information(self, "Verbindung", "Verbindung erfolgreich.")

    def _ok(self):
        if self._try():
            self.cfg = self.result_config()
            config.save(self.cfg)
            self.accept()


def establish_connection(parent=None, force_dialog=False) -> bool:
    cfg, exists = config.load()
    while True:
        if force_dialog or not exists:
            dlg = ConnectionDialog(cfg, parent)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return False
            cfg = dlg.cfg
        try:
            if cfg.backend == "sqlite":
                config.config_dir().mkdir(parents=True, exist_ok=True)
            db.connect(cfg.url())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(parent, "Datenbank nicht erreichbar",
                                 f"{cfg.display()}\n\n{getattr(exc, 'orig', exc)}")
            force_dialog = True
            continue
        if not db.is_initialized():
            if QMessageBox.question(
                    parent, "Neue Datenbank",
                    "Die Datenbank ist leer. Soll das ERP-Schema jetzt angelegt werden?\n\n"
                    "Es wird ein Benutzer 'admin' mit Passwort 'admin' erstellt – bitte "
                    "nach der ersten Anmeldung ändern.") != QMessageBox.StandardButton.Yes:
                force_dialog = True
                continue
        # create_all ist idempotent und ergänzt neue Tabellen bei Updates
        try:
            db.init_schema()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(parent, "Schemafehler", str(exc)[:1500])
            return False
        return True


# --------------------------------------------------------------------------
# Marke
# --------------------------------------------------------------------------

def company_name() -> str:
    try:
        with db.session_scope() as s:
            return get_setting(s, "company_name") or APP_NAME
    except Exception:  # noqa: BLE001 - Anzeige ist optional
        return APP_NAME


def brand_widget(name: str, big: bool = False) -> QWidget:
    """Schriftzug: erste Wörter in Gold, letztes Wort darunter (z. B. PREMIUM DELUXE / MOTORSPORT)."""
    words = name.split()
    top, sub = (" ".join(words[:-1]), words[-1]) if len(words) > 1 else (name, "")
    w = QWidget()
    w.setObjectName("brand")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0 if big else 18, 0 if big else 22, 0 if big else 18, 6 if big else 14)
    lay.setSpacing(3)
    title = spaced(QLabel(top), 3.5 if big else 2.5)
    title.setObjectName("brandName")
    if big:
        title.setStyleSheet("font-size: 18pt;")
    lay.addWidget(title)
    if sub:
        line = QHBoxLayout()
        line.setSpacing(8)
        bar = QFrame()
        bar.setObjectName("brandLine")
        bar.setFixedWidth(22)
        line.addWidget(bar, 0, Qt.AlignmentFlag.AlignVCenter)
        s = spaced(QLabel(sub), 5.0 if big else 4.0)
        s.setObjectName("brandSub")
        line.addWidget(s)
        line.addStretch(1)
        lay.addLayout(line)
    return w


# --------------------------------------------------------------------------
# Anmeldung
# --------------------------------------------------------------------------

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} – Anmeldung")
        self.user: m.User | None = None
        self.change_connection = False
        title = brand_widget(company_name(), big=True)
        cfg, _ = config.load()
        sub = QLabel(f"{APP_NAME} · {cfg.display()}")
        sub.setObjectName("hint")
        self.username = QLineEdit()
        self.username.setPlaceholderText("Benutzername")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Passwort")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self._login)
        login = QPushButton("Anmelden")
        login.setObjectName("primary")
        login.setDefault(True)
        login.clicked.connect(self._login)
        conn = QPushButton("Verbindung …")
        conn.clicked.connect(self._conn)
        row = QHBoxLayout()
        row.addWidget(conn)
        row.addStretch(1)
        row.addWidget(login)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 32, 36, 28)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(18)
        lay.addWidget(self.username)
        lay.addWidget(self.password)
        lay.addSpacing(8)
        lay.addLayout(row)
        self.setMinimumWidth(420)

    def _login(self):
        name, pw = self.username.text(), self.password.text()
        user = run(self, lambda s: auth.authenticate(s, name, pw))
        if user:
            self.user = user
            self.accept()

    def _conn(self):
        self.change_connection = True
        self.reject()


# --------------------------------------------------------------------------
# Hauptfenster
# --------------------------------------------------------------------------

MODULES = [
    # key, Bezeichnung, Gruppe, Fabrik
    ("cockpit", "Cockpit", "Autohaus", CockpitPage),
    ("vehicles", "Fahrzeugbestand", "Autohaus", VehiclesPage),
    ("showroom", "Ausstellung", "Autohaus", ShowroomPage),
    ("vehicle_orders", "Bestellungen", "Autohaus", VehicleOrdersPage),
    ("vehicle_sales", "Verkauf", "Autohaus", vehicle_sales_module),
    ("used_purchases", "Gebrauchtwagen-Ankauf", "Autohaus", UsedPurchasesPage),
    ("customers", "Kunden", "Stammdaten", CustomersPage),
    ("vehicle_models", "Fahrzeugkatalog", "Stammdaten", VehicleModelsPage),
    ("suppliers", "Lieferanten", "Stammdaten", SuppliersPage),
    ("products", "Teile / Zubehör", "Stammdaten", ProductsPage),
    ("dashboard", "Übersicht Werkstatt & Teile", "Teile & Service", DashboardPage),
    ("sales", "Kundenaufträge", "Teile & Service", SalesOrdersPage),
    ("invoices", "Rechnungen", "Teile & Service", InvoicesPage),
    ("purchasing", "Teilebestellungen", "Teile & Service", PurchaseOrdersPage),
    ("inventory", "Teilelager", "Teile & Service", inventory_module),
    ("finance", "Finanzbuchhaltung", "Rechnungswesen (FI)", finance_module),
    ("admin", "Administration", "System", admin_module),
]


class MainWindow(QMainWindow):
    def __init__(self, user: m.User):
        super().__init__()
        self.user = user
        company = company_name()
        self.setWindowTitle(f"{company} – {APP_NAME} {__version__} – {user.full_name or user.username}")
        self.resize(1360, 820)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(240)
        self.nav.setIconSize(QSize(0, 0))
        self.stack = QStackedWidget()
        self.pages = {}
        group = None
        for key, label, grp, factory in MODULES:
            if not user.may(key):
                continue
            if grp != group:
                header = QListWidgetItem(grp.upper())
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setData(Qt.ItemDataRole.UserRole, None)
                f = QFont()
                f.setPointSize(8)
                f.setBold(True)
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.8)
                header.setFont(f)
                self.nav.addItem(header)
                group = grp
            item = QListWidgetItem("   " + label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
            page = factory()
            self.pages[key] = page
            self.stack.addWidget(page)
        self.nav.currentItemChanged.connect(self._switch)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)
        side.addWidget(brand_widget(company))
        side.addWidget(self.nav, 1)
        lay.addWidget(sidebar)
        content = QWidget()
        content.setObjectName("content")
        cl = QVBoxLayout(content)
        cl.addWidget(self.stack)
        lay.addWidget(content, 1)
        self.setCentralWidget(central)
        self._menu()
        cfg, _ = config.load()
        self.statusBar().showMessage(
            f"Angemeldet: {user.username} ({m.ROLES.get(user.role, user.role)})   ·   {cfg.display()}")
        # Startseite
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole):
                self.nav.setCurrentRow(i)
                break
        # Periodische Aktualisierung, damit Änderungen anderer Arbeitsplätze sichtbar werden
        self.timer = QTimer(self, interval=120_000)
        self.timer.timeout.connect(self._auto_refresh)
        self.timer.start()

    def _menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&Datei")
        act = QAction("Aktualisieren", self, shortcut="F5", triggered=self.refresh_current)
        f.addAction(act)
        f.addAction(QAction("Passwort ändern …", self, triggered=self._change_password))
        f.addAction(QAction("Datenbankverbindung …", self, triggered=self._connection))
        f.addSeparator()
        f.addAction(QAction("Beenden", self, shortcut="Ctrl+Q", triggered=self.close))
        h = mb.addMenu("&Hilfe")
        h.addAction(QAction("Über …", self, triggered=lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME} {__version__}</b><br>ERP-System für das Autohaus: "
            "Fahrzeugbestand, Ausstellung, Bestellungen, Verkauf, Gebrauchtwagen-Ankauf, Kunden, "
            "Teile/Lager und Finanzbuchhaltung.<br><br>"
            "Lokal installierter Client mit gemeinsamer zentraler Datenbank.")))

    def _switch(self, item, _prev=None):
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key and key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])
            self.refresh_current()

    def refresh_current(self):
        page = self.stack.currentWidget()
        if page is not None and hasattr(page, "refresh"):
            page.refresh()

    def _auto_refresh(self):
        if QApplication.activeModalWidget() is None and isinstance(self.stack.currentWidget(),
                                                                   (DashboardPage, CockpitPage)):
            self.refresh_current()

    def _change_password(self):
        dlg = FormDialog(self, "Passwort ändern", [
            Field("old", "Altes Passwort", "password", required=True),
            Field("new", "Neues Passwort", "password", required=True),
            Field("new2", "Wiederholen", "password", required=True)])
        if dlg.exec():
            v = dlg.values()
            if v["new"] != v["new2"]:
                QMessageBox.warning(self, "Passwort", "Die Passwörter stimmen nicht überein.")
                return
            run(self, lambda s: auth.change_password(s, self.user.id, v["old"], v["new"]),
                success="Passwort geändert.")

    def _connection(self):
        QMessageBox.information(self, "Verbindung", "Nach Änderung der Verbindung wird die "
                                "Anwendung neu gestartet (Anmeldung erforderlich).")
        cfg, _ = config.load()
        if ConnectionDialog(cfg, self).exec():
            self.restart = True
            self.close()


def main(argv=None) -> int:
    app = QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    apply_theme(app)
    # Deutsche Texte für Standarddialoge (Speichern/Abbrechen, Datumsauswahl …)
    translator = QTranslator(app)
    if translator.load(QLocale(QLocale.Language.German), "qtbase", "_",
                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    force = "--setup" in (argv or sys.argv)
    while True:
        if not establish_connection(force_dialog=force):
            return 0
        force = False
        login = LoginDialog()
        if login.exec() != QDialog.DialogCode.Accepted:
            if login.change_connection:
                force = True
                continue
            return 0
        set_user(login.user)
        win = MainWindow(login.user)
        win.restart = False
        win.show()
        if login.user.username == "admin":
            ok = run(None, lambda s: auth.verify_password("admin", s.get(m.User, login.user.id).password_hash))
            if ok is True:
                QMessageBox.warning(win, "Sicherheit", "Sie verwenden noch das Standardpasswort "
                                    "'admin'. Bitte ändern Sie es über Datei → Passwort ändern.")
        app.exec()
        if not getattr(win, "restart", False):
            return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

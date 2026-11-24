"""Design „Midnight & Gold“ – dunkles Nachtblau/Schwarz mit goldenen Akzenten.

Sportlich-elegant: ruhige, fast schwarze Flächen, feine Linien, Gold nur für
Akzente (Auswahl, Hauptaktionen, Kennzahlen). Alle Farben sind hier als Tokens
definiert und werden von Stylesheet, Palette und Diagrammen gemeinsam genutzt.
"""
from __future__ import annotations

# Flächen (von hinten nach vorne)
BG = "#07090f"          # Fensterhintergrund, fast schwarz mit Blaustich
NAV = "#05070b"         # Navigationsleiste
SURFACE = "#0e131d"     # Karten, Tabellen, Eingabefelder
SURFACE_2 = "#141b29"   # abgesetzte Flächen, Tabellenkopf, alternierende Zeilen
RAISED = "#1b2436"      # Hover
BORDER = "#222c40"
BORDER_STRONG = "#33405a"

# Text
TEXT = "#ece8df"        # warmes Weiß
TEXT_MUTED = "#8d96a8"
TEXT_FAINT = "#5c6679"

# Gold
GOLD = "#c9a24d"
GOLD_LIGHT = "#e2c475"
GOLD_DARK = "#9c7a2e"
GOLD_TINT = "#2a2415"   # Gold auf Dunkel, z. B. Auswahlhintergrund
ON_GOLD = "#0a0b0f"

# Status
POSITIVE = "#57c08b"
NEGATIVE = "#e5675f"

STYLESHEET = f"""
QWidget {{ font-size: 10pt; color: {TEXT}; }}
QMainWindow, #content, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {GOLD_DARK}; padding: 4px; }}

/* Navigation */
#sidebar {{ background: {NAV}; border-right: 1px solid {BORDER}; }}
#brand {{ background: transparent; }}
#brandName {{ color: {GOLD}; font-size: 13pt; font-weight: 700; }}
#brandSub {{ color: {TEXT_MUTED}; font-size: 8pt; }}
#brandLine {{ background: {GOLD_DARK}; max-height: 1px; min-height: 1px; }}
#nav {{ background: {NAV}; color: {TEXT_MUTED}; border: none; padding-top: 4px; font-size: 10.5pt;
    outline: 0; }}
#nav::item {{ padding: 7px 10px; border-left: 3px solid transparent; }}
#nav::item:disabled {{ color: {GOLD_DARK}; padding-top: 16px; }}
#nav::item:selected {{ background: {GOLD_TINT}; color: {GOLD_LIGHT}; border-left: 3px solid {GOLD}; }}
#nav::item:hover:!selected {{ background: {SURFACE}; color: {TEXT}; }}

/* Typografie */
#pageTitle {{ font-size: 18pt; font-weight: 300; color: {TEXT}; padding: 6px 0 10px 0; }}
#loginTitle {{ font-size: 20pt; font-weight: 700; color: {GOLD}; }}
#hint {{ color: {TEXT_MUTED}; }}
#section {{ font-weight: 600; color: {GOLD}; padding-top: 10px; font-size: 9pt; }}
#totals {{ font-size: 11pt; }}
QLabel {{ background: transparent; }}

/* Karten */
#pnl {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 16px; }}
#tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-top: 2px solid {GOLD_DARK};
    border-radius: 4px; }}
#tile:hover {{ border-top: 2px solid {GOLD}; }}
#tileTitle {{ color: {TEXT_MUTED}; font-size: 9pt; }}
#tileValue {{ font-size: 19pt; font-weight: 300; color: {TEXT}; }}

/* Schaltflächen */
QPushButton, QToolButton {{ padding: 6px 14px; border: 1px solid {BORDER_STRONG}; color: {TEXT};
    background: {SURFACE}; border-radius: 3px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {GOLD}; color: {GOLD_LIGHT}; background: {SURFACE_2}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {GOLD_TINT}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}
QPushButton:default {{ border-color: {GOLD_DARK}; }}
QPushButton#primary {{ background: {GOLD}; color: {ON_GOLD}; border: 1px solid {GOLD}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {GOLD_LIGHT}; border-color: {GOLD_LIGHT}; color: {ON_GOLD}; }}
QPushButton#primary:pressed {{ background: {GOLD_DARK}; }}
QToolButton::menu-indicator {{ image: none; }}

/* Eingaben */
QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit {{ padding: 5px; border: 1px solid {BORDER_STRONG};
    border-radius: 3px; background: {SURFACE}; color: {TEXT}; selection-background-color: {GOLD_DARK};
    selection-color: {ON_GOLD}; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {GOLD}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled {{ color: {TEXT_FAINT};
    background: {BG}; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    selection-background-color: {GOLD_TINT}; selection-color: {GOLD_LIGHT}; outline: 0; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {BORDER_STRONG}; border-radius: 2px;
    background: {SURFACE}; }}
QCheckBox::indicator:hover {{ border-color: {GOLD}; }}
QCheckBox::indicator:checked {{ background: {GOLD}; border-color: {GOLD}; }}

/* Tabellen */
QTableWidget, QTableView {{ background: {SURFACE}; alternate-background-color: {SURFACE_2};
    gridline-color: {BORDER}; border: 1px solid {BORDER}; color: {TEXT};
    selection-background-color: {GOLD_TINT}; selection-color: {GOLD_LIGHT}; }}
QHeaderView {{ background: {BG}; }}
QHeaderView::section {{ background: {BG}; color: {TEXT_MUTED}; padding: 7px 5px; border: none;
    border-bottom: 1px solid {GOLD_DARK}; border-right: 1px solid {BORDER}; font-weight: 600; font-size: 9pt; }}
QHeaderView::section:hover {{ color: {GOLD_LIGHT}; }}
QTableCornerButton::section {{ background: {BG}; border: none; }}

/* Register */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 8px 18px; background: transparent; color: {TEXT_MUTED}; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {GOLD_LIGHT}; border-bottom: 2px solid {GOLD}; }}

/* Menüs, Statusleiste */
QMenuBar {{ background: {NAV}; color: {TEXT_MUTED}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ color: {GOLD_LIGHT}; background: {SURFACE}; }}
QMenu {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 14px; }}
QMenu::item:selected {{ background: {GOLD_TINT}; color: {GOLD_LIGHT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QStatusBar {{ background: {NAV}; color: {TEXT_MUTED}; border-top: 1px solid {BORDER}; }}

/* Bildlaufleisten */
QScrollBar:vertical {{ background: {BG}; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 4px;
    min-height: 28px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {GOLD_DARK}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* Kalender */
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {SURFACE_2}; }}
QCalendarWidget QToolButton {{ border: none; background: transparent; color: {TEXT}; }}
QCalendarWidget QAbstractItemView {{ background: {SURFACE}; color: {TEXT};
    selection-background-color: {GOLD}; selection-color: {ON_GOLD}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


def apply_theme(app) -> None:
    """Setzt Fusion-Stil, dunkle Palette und Stylesheet für die ganze Anwendung."""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_2,
        QPalette.ColorRole.ToolTipBase: SURFACE_2,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: TEXT_FAINT,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.BrightText: GOLD_LIGHT,
        QPalette.ColorRole.Highlight: GOLD,
        QPalette.ColorRole.HighlightedText: ON_GOLD,
        QPalette.ColorRole.Link: GOLD_LIGHT,
        QPalette.ColorRole.Light: RAISED,
        QPalette.ColorRole.Midlight: SURFACE_2,
        QPalette.ColorRole.Mid: BORDER,
        QPalette.ColorRole.Dark: NAV,
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_FAINT))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)


def spaced(widget, spacing: float = 3.0, caps: bool = True):
    """Großbuchstaben mit Laufweite – für Markenschriftzug und Überschriften."""
    from PySide6.QtGui import QFont

    f = widget.font()
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    if caps:
        f.setCapitalization(QFont.Capitalization.AllUppercase)
    widget.setFont(f)
    return widget

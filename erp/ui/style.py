"""Stylesheet – angelehnt an moderne Business-Oberflächen (helle Flächen, blaue Akzente)."""

STYLESHEET = """
QWidget { font-size: 10pt; }
QMainWindow, #content { background: #f5f6f7; }
#nav { background: #1d2d3e; color: #e5e9ee; border: none; padding-top: 8px; font-size: 10.5pt; }
#nav::item { padding: 7px 10px; }
#nav::item:disabled { color: #8396a8; padding-top: 14px; }
#nav::item:selected { background: #0a6ed1; color: white; }
#nav::item:hover:!selected { background: #2b3f54; }
#pageTitle { font-size: 16pt; font-weight: 600; color: #1d2d3e; padding: 4px 0 8px 0; }
#loginTitle { font-size: 22pt; font-weight: 700; color: #0a6ed1; }
#hint { color: #6a7785; }
#section { font-weight: 600; color: #1d2d3e; padding-top: 8px; }
#totals { font-size: 11pt; }
#pnl { background: white; border: 1px solid #dfe3e8; border-radius: 6px; padding: 16px; }
#tile { background: white; border: 1px solid #dfe3e8; border-radius: 8px; }
#tileTitle { color: #6a7785; }
#tileValue { font-size: 17pt; font-weight: 600; color: #1d2d3e; }
QPushButton, QToolButton { padding: 5px 12px; border: 1px solid #0a6ed1; color: #0a6ed1;
    background: white; border-radius: 4px; }
QPushButton:hover, QToolButton:hover { background: #ebf5fe; }
QPushButton:disabled { color: #a0a8b0; border-color: #c8ced4; }
QPushButton#primary, QPushButton:checked { background: #0a6ed1; color: white; }
QPushButton#primary:hover { background: #0854a1; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit { padding: 4px; border: 1px solid #b0bac4;
    border-radius: 3px; background: white; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #0a6ed1; }
QTableWidget { background: white; alternate-background-color: #f7f9fb; gridline-color: #e5e8eb;
    border: 1px solid #dfe3e8; selection-background-color: #d1e8ff; selection-color: #1d2d3e; }
QHeaderView::section { background: #eef1f4; padding: 5px; border: none;
    border-right: 1px solid #dfe3e8; border-bottom: 1px solid #dfe3e8; font-weight: 600; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 7px 16px; background: transparent; color: #4a5866; }
QTabBar::tab:selected { color: #0a6ed1; border-bottom: 2px solid #0a6ed1; }
QStatusBar { background: #eef1f4; color: #4a5866; }
"""

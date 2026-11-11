# PyInstaller-Spezifikation: erzeugt eine eigenständige Anwendung (kein Python nötig).
#   pyinstaller packaging/erp_desk.spec
# Ergebnis: dist/ERPDesk/ (Ordner mit ERPDesk.exe bzw. ERPDesk)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "run_erp.py")],
    pathex=[ROOT],
    hiddenimports=["psycopg2", "pymysql", "sqlalchemy.dialects.postgresql",
                   "sqlalchemy.dialects.mysql", "sqlalchemy.dialects.sqlite",
                   "PySide6.QtPrintSupport"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ERPDesk", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ERPDesk")

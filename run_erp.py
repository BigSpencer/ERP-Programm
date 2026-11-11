"""Startskript (auch Einstiegspunkt für PyInstaller)."""
import sys

from erp.__main__ import run

if __name__ == "__main__":
    sys.exit(run())

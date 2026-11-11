"""Lokale Konfiguration des Clients.

Jede Installation speichert ihre Verbindungsdaten zur gemeinsamen Datenbank in
einer INI-Datei im Benutzerprofil:

* Windows: ``%APPDATA%\\ERPDesk\\config.ini``
* Linux/macOS: ``~/.config/erpdesk/config.ini``

Der Pfad kann über die Umgebungsvariable ``ERP_CONFIG`` überschrieben werden,
die Datenbank-URL direkt über ``ERP_DATABASE_URL``.
"""
from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ERPDesk"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "erpdesk"


def config_path() -> Path:
    env = os.environ.get("ERP_CONFIG")
    return Path(env) if env else config_dir() / "config.ini"


DRIVERS = {
    "postgresql": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
    "sqlite": "sqlite",
}


@dataclass
class DatabaseConfig:
    backend: str = "postgresql"      # postgresql | mysql | sqlite
    host: str = "localhost"
    port: int = 5432
    database: str = "erp"
    user: str = "erp"
    password: str = ""
    sqlite_path: str = ""           # nur für sqlite (z. B. Netzlaufwerk)

    def url(self) -> str:
        env = os.environ.get("ERP_DATABASE_URL")
        if env:
            return env
        if self.backend == "sqlite":
            path = self.sqlite_path or str(config_dir() / "erp.sqlite")
            return f"sqlite:///{path}"
        driver = DRIVERS[self.backend]
        pw = quote_plus(self.password)
        return f"{driver}://{quote_plus(self.user)}:{pw}@{self.host}:{self.port}/{self.database}"

    def display(self) -> str:
        if os.environ.get("ERP_DATABASE_URL"):
            return "ERP_DATABASE_URL"
        if self.backend == "sqlite":
            return f"SQLite: {self.sqlite_path or config_dir() / 'erp.sqlite'}"
        return f"{self.backend}://{self.user}@{self.host}:{self.port}/{self.database}"


def load() -> tuple[DatabaseConfig, bool]:
    """Lädt die Konfiguration. Zweiter Rückgabewert: ob eine Datei existierte."""
    path = config_path()
    cfg = DatabaseConfig()
    if not path.exists():
        return cfg, bool(os.environ.get("ERP_DATABASE_URL"))
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if parser.has_section("database"):
        s = parser["database"]
        cfg.backend = s.get("backend", cfg.backend)
        cfg.host = s.get("host", cfg.host)
        cfg.port = s.getint("port", cfg.port)
        cfg.database = s.get("database", cfg.database)
        cfg.user = s.get("user", cfg.user)
        cfg.password = s.get("password", cfg.password)
        cfg.sqlite_path = s.get("sqlite_path", cfg.sqlite_path)
    return cfg, True


def save(cfg: DatabaseConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["database"] = {
        "backend": cfg.backend,
        "host": cfg.host,
        "port": str(cfg.port),
        "database": cfg.database,
        "user": cfg.user,
        "password": cfg.password,
        "sqlite_path": cfg.sqlite_path,
    }
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path

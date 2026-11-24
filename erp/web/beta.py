"""Beta-/Testumgebung: Kopie der Live-Daten, Änderungen bleiben in der Kopie.

Aktiv, wenn ERP_BETA_SOURCE_URL gesetzt ist (Verbindung zur Live-Datenbank).
Die Live-Datenbank wird ausschließlich gelesen – die Verbindung ist schreibgeschützt.
Ziel ist ERP_DATABASE_URL oder, wenn nicht gesetzt, eine SQLite-Datei im temporären
Verzeichnis (bei Render: wird bei jedem Neustart/Deploy frisch aus Live befüllt).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine

from .. import models as m

log = logging.getLogger("erp.web.beta")
_state = {"loaded_at": None, "rows": 0}


def source_url() -> str | None:
    url = (os.environ.get("ERP_BETA_SOURCE_URL") or "").strip().strip("\"'").strip()
    if not url:
        return None
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    if not url.startswith(("postgresql+", "sqlite:", "mysql")):
        shown = (url.split("://", 1)[0] + "://" if "://" in url else re.match(r"[A-Za-z0-9._-]{0,12}", url).group(0)) + "…"
        raise RuntimeError(
            "ERP_BETA_SOURCE_URL ist keine gültige Datenbank-Adresse (beginnt mit „" + shown + "“). Erwartet wird die "
            "„Internal Database URL“ der Live-Datenbank, z. B. postgresql://benutzer:passwort@host/datenbank – "
            "ohne Anführungszeichen und Leerzeichen.")
    return url


def is_beta() -> bool:
    return bool((os.environ.get("ERP_BETA_SOURCE_URL") or "").strip())


def target_url(explicit: str | None, configured: str) -> str:
    """Ziel der Beta-Kopie: explizit übergeben > eigene Beta-Datenbank (ERP_DATABASE_URL) > temporäre SQLite-Datei."""
    if explicit:
        return explicit
    if os.environ.get("ERP_DATABASE_URL") or os.environ.get("DATABASE_URL"):
        return configured
    return f"sqlite:///{Path(tempfile.gettempdir()) / 'erp-beta.sqlite'}"


def _readonly_engine(url: str) -> Engine:
    if url.startswith("postgresql"):
        # jede Transaktion auf der Live-Datenbank ist nur lesend
        return create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"},
                             pool_pre_ping=True)
    if url.startswith("sqlite"):
        path = url.split("sqlite:///", 1)[1]
        return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    return create_engine(url)


def clone(target: Engine, src_url: str | None = None) -> int:
    """Leert die Beta-Datenbank und kopiert alle Daten aus Live hinein. Gibt die Zeilenzahl zurück."""
    src_url = src_url or source_url()
    if not src_url:
        raise RuntimeError("ERP_BETA_SOURCE_URL ist nicht gesetzt")
    if target.url.render_as_string(hide_password=False) == src_url:
        raise RuntimeError("Beta- und Live-Datenbank dürfen nicht dieselbe sein")
    source = _readonly_engine(src_url)
    m.Base.metadata.drop_all(target)
    m.Base.metadata.create_all(target)
    insp = inspect(source)
    total = 0
    try:
        with source.connect() as src, target.begin() as dst:
            for table in m.Base.metadata.sorted_tables:          # Reihenfolge passend zu Fremdschlüsseln
                if not insp.has_table(table.name):
                    continue                                       # Tabelle gibt es in Live (noch) nicht
                live_cols = {c["name"] for c in insp.get_columns(table.name)}
                cols = [c for c in table.columns if c.name in live_cols]
                rows = [dict(r._mapping) for r in src.execute(select(*[table.c[c.name] for c in cols]))]
                if rows:
                    dst.execute(table.insert(), rows)
                    total += len(rows)
            if target.dialect.name == "postgresql":               # Zähler für neue IDs nachziehen
                for table in m.Base.metadata.sorted_tables:
                    if "id" in table.c and table.c.id.primary_key:
                        dst.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                                         f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"))
    finally:
        source.dispose()
    _state.update(loaded_at=dt.datetime.now().replace(microsecond=0), rows=total)
    log.info("Beta: %s Datensätze aus Live übernommen", total)
    return total


def info() -> dict | None:
    if not is_beta():
        return None
    t = _state["loaded_at"]
    return {"loadedAt": t.isoformat() if t else None, "rows": _state["rows"]}

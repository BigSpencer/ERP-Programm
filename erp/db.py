"""Datenbankverbindung, Schema-Initialisierung und Grunddaten."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import models as m

SCHEMA_VERSION = "1"

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # SQLite: mehrere Prozesse (z. B. Freigabe im LAN) – Wartezeit bei Sperren
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=1800)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def connect(url: str) -> Engine:
    """Verbindet die Anwendung mit der (gemeinsamen) Datenbank."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    with _engine.connect():  # Verbindung sofort prüfen
        pass
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Keine Datenbankverbindung")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaktion: commit bei Erfolg, rollback bei Fehler."""
    if _SessionLocal is None:
        raise RuntimeError("Keine Datenbankverbindung")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def is_initialized(eng: Engine | None = None) -> bool:
    eng = eng or engine()
    return inspect(eng).has_table("settings")


def init_schema(eng: Engine | None = None) -> None:
    """Legt fehlende Tabellen an und schreibt Grunddaten (idempotent)."""
    eng = eng or engine()
    m.Base.metadata.create_all(eng)
    add_missing_columns(eng)
    with Session(eng) as s:
        seed(s)
        s.commit()


def add_missing_columns(eng: Engine) -> list[str]:
    """Ergänzt neue Spalten in bestehenden Tabellen (create_all legt nur fehlende Tabellen an).

    Neue Spalten werden ohne Pflichtfeld-Einschränkung angelegt, damit vorhandene
    Datensätze gültig bleiben. Gibt die ergänzten Spalten zurück.
    """
    insp = inspect(eng)
    added = []
    for table in m.Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=eng.dialect)}'
            with eng.begin() as conn:
                conn.exec_driver_sql(ddl)
            added.append(f"{table.name}.{col.name}")
    return added


DEFAULT_ACCOUNTS = [
    # SKR03-angelehnter Minimal-Kontenrahmen
    ("0800", "Gezeichnetes Kapital", "equity"),
    ("1000", "Kasse", "asset"),
    ("1200", "Bank", "asset"),
    ("1400", "Forderungen aus Lieferungen und Leistungen", "asset"),
    ("1576", "Abziehbare Vorsteuer 19 %", "asset"),
    ("1571", "Abziehbare Vorsteuer 7 %", "asset"),
    ("1600", "Verbindlichkeiten aus Lieferungen und Leistungen", "liability"),
    ("1776", "Umsatzsteuer 19 %", "liability"),
    ("1771", "Umsatzsteuer 7 %", "liability"),
    ("3400", "Wareneingang 19 % Vorsteuer", "expense"),
    ("3300", "Wareneingang 7 % Vorsteuer", "expense"),
    ("3200", "Wareneingang Gebrauchtfahrzeuge (ohne Vorsteuer)", "expense"),
    ("4100", "Löhne und Gehälter", "expense"),
    ("4210", "Miete", "expense"),
    ("4900", "Sonstige betriebliche Aufwendungen", "expense"),
    ("8400", "Erlöse 19 % USt", "revenue"),
    ("8300", "Erlöse 7 % USt", "revenue"),
    ("8100", "Steuerfreie Umsätze", "revenue"),
    ("8190", "Erlöse Gebrauchtfahrzeuge (Differenzbesteuerung § 25a UStG)", "revenue"),
]

DEFAULT_RANGES = [
    ("customer", "", 10000, 5),
    ("supplier", "", 70000, 5),
    ("product", "M-", 1000, 5),
    ("sales_order", "AU-", 1, 6),
    ("invoice", "RE-", 1, 6),
    ("purchase_order", "BE-", 1, 6),
    ("journal", "BU-", 1, 7),
    ("vehicle", "FZ-", 1, 6),
    ("vehicle_model", "MD-", 1, 4),
    ("vehicle_order", "FB-", 1, 6),
    ("offer", "AG-", 1, 6),
    ("vehicle_sale", "KV-", 1, 6),
    ("used_purchase", "AK-", 1, 6),
]

DEFAULT_SETTINGS = {
    "schema_version": SCHEMA_VERSION,
    "company_name": "Premium Deluxe Motorsport",
    "company_street": "Musterstraße 1",
    "company_city": "12345 Musterstadt",
    "company_vat_id": "",
    "company_iban": "",
    "currency": "USD",
    "vehicle_vat_rate": "0",      # Fahrzeuge ohne Umsatzsteuer (Spielbetrieb)
}


def seed(s: Session) -> None:
    from .services.auth import hash_password

    for key, value in DEFAULT_SETTINGS.items():
        if s.get(m.Setting, key) is None:
            s.add(m.Setting(key=key, value=value))
    for key, prefix, start, width in DEFAULT_RANGES:
        if s.get(m.NumberRange, key) is None:
            s.add(m.NumberRange(key=key, prefix=prefix, next_value=start, width=width))
    existing = set(s.scalars(select(m.Account.number)))
    for number, name, typ in DEFAULT_ACCOUNTS:
        if number not in existing:
            s.add(m.Account(number=number, name=name, account_type=typ))
    if s.scalar(select(m.Warehouse).limit(1)) is None:
        s.add(m.Warehouse(code="HL", name="Hauptlager"))
    if s.scalar(select(m.User).limit(1)) is None:
        s.add(m.User(username="admin", full_name="Administrator",
                     password_hash=hash_password("admin"), role="admin"))

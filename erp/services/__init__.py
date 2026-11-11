"""Geschäftslogik – unabhängig von der Oberfläche und damit testbar."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m


class BusinessError(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt wird."""


class _Context:
    username: str = "system"
    role: str = "admin"


context = _Context()


def set_user(user: m.User | None) -> None:
    context.username = user.username if user else "system"
    context.role = user.role if user else "admin"


def audit(s: Session, action: str, entity: str, entity_id="", details: str = "") -> None:
    s.add(m.AuditLog(username=context.username, action=action, entity=entity,
                     entity_id=str(entity_id), details=details[:2000]))


def next_number(s: Session, key: str) -> str:
    """Vergibt die nächste Nummer eines Nummernkreises.

    Die Zeile wird per ``SELECT … FOR UPDATE`` gesperrt, sodass parallel
    arbeitende Clients niemals dieselbe Nummer erhalten.
    """
    rng = s.scalars(select(m.NumberRange).where(m.NumberRange.key == key)
                    .with_for_update()).first()
    if rng is None:
        raise BusinessError(f"Nummernkreis '{key}' fehlt")
    value = rng.next_value
    rng.next_value = value + 1
    s.flush()
    return f"{rng.prefix}{value:0{rng.width}d}"


def dec(value, default: str = "0") -> Decimal:
    """Wandelt Eingaben (auch '1.234,56') robust in Decimal um."""
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(" ", "").replace("€", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise BusinessError(f"Ungültige Zahl: {value}") from exc


def get_setting(s: Session, key: str, default: str = "") -> str:
    row = s.get(m.Setting, key)
    return row.value if row else default


def set_setting(s: Session, key: str, value: str) -> None:
    row = s.get(m.Setting, key)
    if row is None:
        s.add(m.Setting(key=key, value=value))
    else:
        row.value = value

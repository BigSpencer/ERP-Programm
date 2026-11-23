"""Geschäftslogik – unabhängig von der Oberfläche und damit testbar."""
from __future__ import annotations

import contextvars
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m


class BusinessError(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt wird."""


_current_user: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "erp_current_user", default=("system", "admin"))


class _Context:
    """Angemeldeter Benutzer – je Thread/Anfrage getrennt (wichtig für den Webserver)."""

    @property
    def username(self) -> str:
        return _current_user.get()[0]

    @property
    def role(self) -> str:
        return _current_user.get()[1]


context = _Context()


def set_user(user: m.User | None) -> None:
    _current_user.set((user.username, user.role) if user else ("system", "admin"))


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


_US_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+$")


def dec(value, default: str = "0") -> Decimal:
    """Wandelt Eingaben robust in Decimal um: amerikanisch ("1,234.56", "120,000")
    und deutsch ("1.234,56", "1,5")."""
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(" ", "").replace("€", "").replace("$", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):      # deutsch: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:                                        # amerikanisch: 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        if _US_THOUSANDS.match(text):                # amerikanisch: 120,000
            text = text.replace(",", "")
        else:                                        # deutsch: 1,5
            text = text.replace(",", ".")
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

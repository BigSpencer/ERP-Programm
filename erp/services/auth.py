"""Benutzerverwaltung und Anmeldung."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from . import BusinessError, audit

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(calc.hex(), digest)


def authenticate(s: Session, username: str, password: str) -> m.User:
    user = s.scalars(select(m.User).where(m.User.username == username.strip())).first()
    if user is None or not user.active or not verify_password(password, user.password_hash):
        raise BusinessError("Benutzername oder Passwort falsch")
    return user


def save_user(s: Session, user_id: int | None, username: str, full_name: str, role: str,
              active: bool, password: str | None = None) -> m.User:
    username = username.strip()
    if not username:
        raise BusinessError("Benutzername fehlt")
    if role not in m.ROLES:
        raise BusinessError("Unbekannte Rolle")
    dup = s.scalars(select(m.User).where(m.User.username == username)).first()
    if dup and dup.id != user_id:
        raise BusinessError(f"Benutzer '{username}' existiert bereits")
    if user_id:
        user = s.get(m.User, user_id)
    else:
        if not password:
            raise BusinessError("Für neue Benutzer ist ein Passwort erforderlich")
        user = m.User(username=username)
        s.add(user)
    user.username = username
    user.full_name = full_name
    user.role = role
    user.active = active
    if password:
        user.password_hash = hash_password(password)
    s.flush()
    _ensure_admin_left(s)
    audit(s, "save", "user", user.id, f"{username} ({role})")
    return user


def change_password(s: Session, user_id: int, old: str, new: str) -> None:
    user = s.get(m.User, user_id)
    if not verify_password(old, user.password_hash):
        raise BusinessError("Altes Passwort ist falsch")
    if not new:
        raise BusinessError("Bitte ein neues Passwort eingeben")
    user.password_hash = hash_password(new)
    audit(s, "password", "user", user.id)


def _ensure_admin_left(s: Session) -> None:
    admins = s.scalars(select(m.User).where(m.User.role == "admin", m.User.active.is_(True))).all()
    if not admins:
        raise BusinessError("Es muss mindestens ein aktiver Administrator bestehen bleiben")

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


def role_names(s: Session) -> dict[str, str]:
    names = dict(m.ROLES)
    names.update({r.key: r.name for r in s.scalars(select(m.Role))})
    return names


def modules_for(s: Session, user: m.User) -> set[str]:
    """Bereiche, die der Benutzer sehen darf – Vereinigung aller seiner Rollen. Admins dürfen alles."""
    if user.is_admin:
        return set(m.WEB_MODULES) | {"admin"}
    roles = {r.key: r for r in s.scalars(select(m.Role).where(m.Role.key.in_(user.role_keys)))}
    mods: set[str] = set()
    for key in user.role_keys:
        if key in roles:
            mods |= set(roles[key].module_list)
        else:                                             # Rolle (noch) nicht in der Datenbank: feste Rechte
            mods |= {mod for mod, keys in m.MODULE_ROLES.items() if key in keys}
    return mods


def may(s: Session, user: m.User, module: str) -> bool:
    return module in modules_for(s, user)


def save_role(s: Session, role_id: int | None, name: str, modules: list[str]) -> m.Role:
    name = (name or "").strip()
    if not name:
        raise BusinessError("Bitte einen Rollennamen angeben")
    unknown = [x for x in modules if x not in m.WEB_MODULES]
    if unknown:
        raise BusinessError("Unbekannter Bereich: " + ", ".join(unknown))
    dup = s.scalars(select(m.Role).where(m.Role.name == name)).first()
    if dup and dup.id != role_id:
        raise BusinessError(f"Die Rolle „{name}“ gibt es bereits")
    if role_id:
        role = s.get(m.Role, role_id)
        if role is None:
            raise BusinessError("Rolle nicht gefunden")
        if role.key == "admin":
            raise BusinessError("Die Rolle Administrator darf immer alles und kann nicht geändert werden")
    else:
        base = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:30] or "rolle"
        key, i = base, 2
        while s.scalars(select(m.Role).where(m.Role.key == key)).first() is not None or key in m.ROLES:
            key, i = f"{base}_{i}", i + 1
        role = m.Role(key=key, builtin=False)
        s.add(role)
    role.name = name
    role.modules = ",".join(x for x in m.WEB_MODULES if x in modules)
    s.flush()
    audit(s, "save", "role", role.key, role.modules)
    return role


def delete_role(s: Session, role_id: int) -> str:
    role = s.get(m.Role, role_id)
    if role is None:
        raise BusinessError("Rolle nicht gefunden")
    if role.key == "admin":
        raise BusinessError("Die Rolle Administrator kann nicht gelöscht werden")
    users = [u.username for u in s.scalars(select(m.User)) if role.key in u.role_keys]
    if users:
        raise BusinessError(f"Die Rolle ist noch {len(users)} Mitarbeiter(n) zugewiesen: {', '.join(users[:5])}")
    name = role.name
    s.delete(role)
    s.flush()
    audit(s, "delete", "role", role_id, name)
    return name


def save_user(s: Session, user_id: int | None, username: str, full_name: str, role,
              active: bool, password: str | None = None) -> m.User:
    """``role``: ein Rollenschlüssel oder eine Liste von Rollenschlüsseln."""
    username = username.strip()
    if not username:
        raise BusinessError("Benutzername fehlt")
    keys = [role] if isinstance(role, str) else [k for k in role if k]
    if not keys:
        raise BusinessError("Bitte mindestens eine Rolle zuweisen")
    known = role_names(s)
    if any(k not in known for k in keys):
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
    user.role = "admin" if "admin" in keys else keys[0]
    user.roles = ",".join(dict.fromkeys(keys))
    user.active = active
    if password:
        user.password_hash = hash_password(password)
    s.flush()
    _ensure_admin_left(s)
    audit(s, "save", "user", user.id, f"{username} ({user.roles})")
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
    admins = [u for u in s.scalars(select(m.User).where(m.User.active.is_(True))) if u.is_admin]
    if not admins:
        raise BusinessError("Es muss mindestens ein aktiver Administrator bestehen bleiben")

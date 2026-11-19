"""Prüfplanung nach DGUV Vorschrift 3.

Vorbereitung: Grundriss einer Abteilung anlegen und Prüfbereiche (Räume,
Teilflächen) als Rechtecke einzeichnen, optional einem Prüfer zuweisen.

Laufender Betrieb: Jeder Prüfer markiert seine Bereiche als *erledigt*,
*teilweise erledigt*, *Termin* (mit Datum) oder *in Klärung*. Alle Prüfer
arbeiten auf derselben Datenbank; gleichzeitige Änderungen am selben Bereich
werden über die Versionsnummer erkannt.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import models as m
from . import BusinessError, ConflictError, audit, context

GRID = 0.5          # Raster in Metern (Einrasten beim Zeichnen)
MIN_SIZE = 0.5      # kleinster Bereich (m)


def snap(value: float, grid: float = GRID) -> float:
    return round(round(float(value) / grid) * grid, 3)


def natural_key(text: str) -> list:
    """Sortierschlüssel, bei dem 'Büro 2' vor 'Büro 10' kommt."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text or "")]


def _check_version(obj, version: int | None) -> None:
    if version is not None and obj.version != version:
        raise ConflictError("Der Datensatz wurde inzwischen von einem anderen Benutzer geändert. "
                            "Die Ansicht wurde aktualisiert – bitte erneut versuchen.")


# --------------------------------------------------------------------------
# Grundrisse
# --------------------------------------------------------------------------

def floor_plans(s: Session, only_active: bool = True) -> list[m.FloorPlan]:
    q = select(m.FloorPlan).order_by(m.FloorPlan.location, m.FloorPlan.name)
    if only_active:
        q = q.where(m.FloorPlan.active.is_(True))
    return list(s.scalars(q))


def save_floor_plan(s: Session, plan_id: int | None, data: dict, version: int | None = None) -> m.FloorPlan:
    name = str(data.get("name", "")).strip()
    if not name:
        raise BusinessError("Bitte einen Namen für den Grundriss angeben")
    width = float(data.get("width_m") or 40)
    height = float(data.get("height_m") or 20)
    if not (2 <= width <= 1000 and 2 <= height <= 1000):
        raise BusinessError("Breite und Tiefe müssen zwischen 2 und 1000 m liegen")
    if plan_id:
        plan = s.get(m.FloorPlan, plan_id)
        if plan is None:
            raise BusinessError("Grundriss wurde inzwischen gelöscht")
        _check_version(plan, version)
    else:
        plan = m.FloorPlan()
        s.add(plan)
    plan.name = name
    plan.location = str(data.get("location", "")).strip()
    plan.width_m, plan.height_m = width, height
    plan.notes = str(data.get("notes", "") or "")
    s.flush()
    audit(s, "update" if plan_id else "create", "floor_plan", plan.id, name)
    return plan


def delete_floor_plan(s: Session, plan_id: int) -> None:
    plan = s.get(m.FloorPlan, plan_id)
    if plan is None:
        return
    audit(s, "delete", "floor_plan", plan_id, plan.name)
    s.delete(plan)


def set_background(s: Session, plan_id: int, image: bytes | None) -> None:
    """Hinterlegt einen eingescannten Plan/Fluchtwegplan als Zeichenhilfe."""
    plan = s.get(m.FloorPlan, plan_id)
    if image is not None and len(image) > 15 * 1024 * 1024:
        raise BusinessError("Das Bild ist zu groß (max. 15 MB)")
    plan.background = image
    plan.has_background = image is not None
    audit(s, "background", "floor_plan", plan_id, "gesetzt" if image else "entfernt")


def background(s: Session, plan_id: int) -> bytes | None:
    return s.scalar(select(m.FloorPlan.background).where(m.FloorPlan.id == plan_id))


def copy_floor_plan(s: Session, plan_id: int, new_name: str) -> m.FloorPlan:
    """Kopiert einen Grundriss mit allen Bereichen (Status zurückgesetzt) – z. B. für die
    Wiederholungsprüfung im nächsten Jahr."""
    src = s.get(m.FloorPlan, plan_id)
    plan = save_floor_plan(s, None, {"name": new_name, "location": src.location,
                                     "width_m": src.width_m, "height_m": src.height_m,
                                     "notes": src.notes})
    if src.has_background:
        plan.background = background(s, plan_id)
        plan.has_background = True
    for a in src.areas:
        s.add(m.InspectionArea(floor_plan_id=plan.id, name=a.name, x=a.x, y=a.y, w=a.w, h=a.h,
                               inspector_id=a.inspector_id, expected_devices=a.expected_devices,
                               notes=a.notes))
    s.flush()
    return plan


# --------------------------------------------------------------------------
# Prüfbereiche – Vorbereitung
# --------------------------------------------------------------------------

def _geometry(plan: m.FloorPlan, x, y, w, h) -> tuple[float, float, float, float]:
    w, h = max(MIN_SIZE, snap(w)), max(MIN_SIZE, snap(h))
    x = min(max(0.0, snap(x)), max(0.0, plan.width_m - w))
    y = min(max(0.0, snap(y)), max(0.0, plan.height_m - h))
    return x, y, w, h


def areas(s: Session, plan_id: int | None = None, inspector_id: int | None = None,
          status: str | None = None, text: str = "") -> list[m.InspectionArea]:
    q = (select(m.InspectionArea).join(m.FloorPlan)
         .options(selectinload(m.InspectionArea.inspector), selectinload(m.InspectionArea.floor_plan))
         .where(m.FloorPlan.active.is_(True)))
    if plan_id:
        q = q.where(m.InspectionArea.floor_plan_id == plan_id)
    if inspector_id == -1:
        q = q.where(m.InspectionArea.inspector_id.is_(None))
    elif inspector_id:
        q = q.where(m.InspectionArea.inspector_id == inspector_id)
    if status:
        q = q.where(m.InspectionArea.status == status)
    if text:
        like = f"%{text.lower()}%"
        q = q.where(func.lower(m.InspectionArea.name).like(like)
                    | func.lower(m.InspectionArea.notes).like(like)
                    | func.lower(m.InspectionArea.status_note).like(like))
    result = list(s.scalars(q))
    result.sort(key=lambda a: (natural_key(a.floor_plan.name), natural_key(a.name)))
    return result


def add_area(s: Session, plan_id: int, name: str, x: float, y: float, w: float, h: float,
             inspector_id: int | None = None, expected_devices: int = 0, notes: str = "") -> m.InspectionArea:
    plan = s.get(m.FloorPlan, plan_id)
    if plan is None:
        raise BusinessError("Grundriss nicht gefunden")
    name = (name or "").strip()
    if not name:
        raise BusinessError("Bitte eine Bezeichnung für den Bereich angeben")
    x, y, w, h = _geometry(plan, x, y, w, h)
    area = m.InspectionArea(floor_plan_id=plan_id, name=name, x=x, y=y, w=w, h=h,
                            inspector_id=inspector_id, expected_devices=int(expected_devices or 0),
                            notes=notes or "")
    s.add(area)
    s.flush()
    audit(s, "create", "inspection_area", area.id, f"{plan.name}: {name}")
    return area


def add_row(s: Session, plan_id: int, prefix: str, start_no: int, count: int, x: float, y: float,
            w: float, h: float, horizontal: bool = True, inspector_id: int | None = None) -> list[m.InspectionArea]:
    """Legt eine Reihe gleich großer Räume an (z. B. 8 Büros entlang eines Flurs)."""
    if not 1 <= count <= 100:
        raise BusinessError("Anzahl muss zwischen 1 und 100 liegen")
    plan = s.get(m.FloorPlan, plan_id)
    end_x = x + (w * count if horizontal else w)
    end_y = y + (h if horizontal else h * count)
    if end_x > plan.width_m + 1e-6 or end_y > plan.height_m + 1e-6:
        raise BusinessError(f"Die Raumreihe passt nicht auf den Grundriss "
                            f"({plan.width_m:g} × {plan.height_m:g} m)")
    result = []
    for i in range(count):
        nx, ny = (x + i * w, y) if horizontal else (x, y + i * h)
        result.append(add_area(s, plan_id, f"{prefix}{start_no + i}", nx, ny, w, h, inspector_id))
    return result


def update_area(s: Session, area_id: int, data: dict, version: int | None = None) -> m.InspectionArea:
    """Stammdaten eines Bereichs (Bezeichnung, Prüfer, Anzahl, Hinweise) ändern."""
    area = _area(s, area_id)
    _check_version(area, version)
    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            raise BusinessError("Bitte eine Bezeichnung für den Bereich angeben")
        area.name = name
    if "inspector_id" in data:
        area.inspector_id = data["inspector_id"] or None
    if "expected_devices" in data:
        area.expected_devices = int(data["expected_devices"] or 0)
    if "notes" in data:
        area.notes = data["notes"] or ""
    s.flush()
    audit(s, "update", "inspection_area", area.id, area.name)
    return area


def move_area(s: Session, area_id: int, x: float, y: float, w: float, h: float,
              version: int | None = None) -> m.InspectionArea:
    area = _area(s, area_id)
    _check_version(area, version)
    area.x, area.y, area.w, area.h = _geometry(area.floor_plan, x, y, w, h)
    s.flush()
    return area


def delete_area(s: Session, area_id: int) -> None:
    area = s.get(m.InspectionArea, area_id)
    if area is not None:
        audit(s, "delete", "inspection_area", area_id, area.name)
        s.delete(area)


def assign(s: Session, area_ids: list[int], inspector_id: int | None) -> int:
    for aid in area_ids:
        _area(s, aid).inspector_id = inspector_id or None
    audit(s, "assign", "inspection_area", ",".join(map(str, area_ids))[:40], str(inspector_id or "-"))
    return len(area_ids)


def inspectors(s: Session) -> list[m.User]:
    """Benutzer, denen Bereiche zugewiesen werden können."""
    return list(s.scalars(select(m.User).where(m.User.active.is_(True),
                                               m.User.role.in_(("inspector", "admin")))
                          .order_by(m.User.full_name, m.User.username)))


def _area(s: Session, area_id: int) -> m.InspectionArea:
    area = s.get(m.InspectionArea, area_id)
    if area is None:
        raise BusinessError("Der Bereich wurde inzwischen gelöscht")
    return area


# --------------------------------------------------------------------------
# Laufender Betrieb – Status
# --------------------------------------------------------------------------

def _may_change(area: m.InspectionArea) -> None:
    if context.role == "admin" or area.inspector_id in (None, context.user_id):
        return
    who = area.inspector.full_name or area.inspector.username
    raise BusinessError(f"Der Bereich '{area.name}' ist {who} zugewiesen. "
                        "Nur dieser Prüfer (oder ein Administrator) kann den Status ändern.")


def claim(s: Session, area_id: int, version: int | None = None) -> m.InspectionArea:
    """Nicht zugewiesenen Bereich selbst übernehmen."""
    area = _area(s, area_id)
    _check_version(area, version)
    _may_change(area)
    area.inspector_id = context.user_id
    s.flush()
    audit(s, "claim", "inspection_area", area.id, area.name)
    return area


def set_status(s: Session, area_id: int, status: str, *, date: dt.date | None = None,
               note: str = "", progress: int | None = None,
               version: int | None = None) -> m.InspectionArea:
    if status not in m.AREA_STATUS:
        raise BusinessError("Unbekannter Status")
    area = _area(s, area_id)
    _check_version(area, version)
    _may_change(area)
    note = (note or "").strip()
    if status == "appointment" and date is None:
        raise BusinessError("Für einen Termin bitte ein Datum angeben")
    if status == "clarify" and not note:
        raise BusinessError("Bitte kurz beschreiben, was geklärt werden muss")
    if status == "partial":
        progress = int(progress if progress is not None else 50)
        if not 1 <= progress <= 99:
            raise BusinessError("Fortschritt für 'teilweise erledigt' zwischen 1 und 99 % angeben")
    else:
        progress = 100 if status == "done" else (area.progress_pct if status in ("appointment", "clarify") else 0)
    if area.inspector_id is None and context.user_id is not None and status != "open":
        area.inspector_id = context.user_id          # wer markiert, übernimmt den Bereich
    area.status = status
    area.progress_pct = progress
    area.appointment_date = date if status == "appointment" else None
    area.status_note = note
    area.status_by = context.username
    area.status_at = dt.datetime.now()
    s.add(m.AreaStatusLog(area_id=area.id, username=context.username, status=status,
                          progress_pct=progress, appointment_date=area.appointment_date, note=note))
    s.flush()
    audit(s, "status", "inspection_area", area.id, f"{area.name}: {m.AREA_STATUS[status]}")
    return area


def history(s: Session, area_id: int) -> list[m.AreaStatusLog]:
    return list(s.scalars(select(m.AreaStatusLog).where(m.AreaStatusLog.area_id == area_id)
                          .order_by(m.AreaStatusLog.id.desc())))


def summary(area_list: list[m.InspectionArea]) -> dict:
    """Zählt Bereiche je Status und berechnet den Gesamtfortschritt."""
    counts = {k: 0 for k in m.AREA_STATUS}
    for a in area_list:
        counts[a.status] = counts.get(a.status, 0) + 1
    total = len(area_list)
    pct = round(sum(a.progress_pct for a in area_list) / total) if total else 0
    return {"total": total, "counts": counts, "progress": pct,
            "overdue": sum(1 for a in area_list if a.overdue)}


def appointments(s: Session, inspector_id: int | None = None) -> list[m.InspectionArea]:
    """Alle offenen Termine, nach Datum sortiert."""
    q = (select(m.InspectionArea).join(m.FloorPlan)
         .where(m.InspectionArea.status == "appointment", m.FloorPlan.active.is_(True))
         .order_by(m.InspectionArea.appointment_date))
    if inspector_id:
        q = q.where(m.InspectionArea.inspector_id == inspector_id)
    return list(s.scalars(q))

import datetime as dt

import pytest

from erp import db, models as m
from erp.services import BusinessError, auth, inspection as ins, set_user


@pytest.fixture()
def s(db_url):
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as session:
        yield session
    set_user(None)


def _users(s):
    p1 = auth.save_user(s, None, "p1", "Paula Prüfer", "inspector", True, "geheim1")
    p2 = auth.save_user(s, None, "p2", "Peter Prüfer", "inspector", True, "geheim2")
    return p1, p2


def test_floor_plan_and_areas(s):
    plan = ins.save_floor_plan(s, None, {"name": "Verwaltung EG", "width_m": 30, "height_m": 12})
    a = ins.add_area(s, plan.id, "Büro 1", 0.26, 0.74, 4.1, 3.9)
    assert (a.x, a.y, a.w, a.h) == (0.5, 0.5, 4.0, 4.0)          # Einrasten im 0,5-m-Raster
    b = ins.add_area(s, plan.id, "Rand", 29, 11, 4, 4)
    assert (b.x, b.y) == (26.0, 8.0)                            # bleibt auf dem Grundriss
    row = ins.add_row(s, plan.id, "Büro 1.", 1, 6, 0, 6, 5, 4)
    assert [r.name for r in row][-1] == "Büro 1.6" and row[-1].x == 25
    with pytest.raises(BusinessError, match="passt nicht"):
        ins.add_row(s, plan.id, "X", 1, 7, 0, 6, 5, 4)
    ins.move_area(s, a.id, 10, 2, 3, 3)
    assert (a.x, a.w) == (10, 3)
    assert len(ins.areas(s, plan.id)) == 8
    copy = ins.copy_floor_plan(s, plan.id, "Verwaltung EG 2027")
    assert len(copy.areas) == 8 and all(x.status == "open" for x in copy.areas)


def test_status_workflow(s):
    p1, p2 = _users(s)
    plan = ins.save_floor_plan(s, None, {"name": "Werkstatt"})
    a = ins.add_area(s, plan.id, "Halle", 0, 0, 10, 10, inspector_id=p1.id)
    free = ins.add_area(s, plan.id, "Lager", 10, 0, 5, 5)

    set_user(p1)
    ins.set_status(s, a.id, "partial", progress=40, note="Maschinen fehlen noch")
    assert a.status == "partial" and a.progress_pct == 40 and a.status_by == "p1"
    with pytest.raises(BusinessError, match="Datum"):
        ins.set_status(s, a.id, "appointment")
    ins.set_status(s, a.id, "appointment", date=dt.date.today() - dt.timedelta(days=1))
    assert a.overdue and a.progress_pct == 40
    with pytest.raises(BusinessError, match="geklärt"):
        ins.set_status(s, a.id, "clarify")
    ins.set_status(s, a.id, "clarify", note="Schlüssel fehlt")
    ins.set_status(s, a.id, "done")
    assert a.progress_pct == 100 and a.appointment_date is None
    assert [h.status for h in ins.history(s, a.id)] == ["done", "clarify", "appointment", "partial"]

    # Fremder Bereich ist geschützt, freier Bereich wird beim Markieren übernommen
    set_user(p2)
    with pytest.raises(BusinessError, match="zugewiesen"):
        ins.set_status(s, a.id, "open")
    ins.set_status(s, free.id, "done")
    assert free.inspector_id == p2.id

    summ = ins.summary(ins.areas(s, plan.id))
    assert summ["counts"]["done"] == 2 and summ["progress"] == 100
    assert len(ins.areas(s, inspector_id=p2.id)) == 1


def test_concurrent_status_change_detected(db_url):
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as s:
        plan = ins.save_floor_plan(s, None, {"name": "Büro"})
        aid = ins.add_area(s, plan.id, "R1", 0, 0, 4, 4).id
        version = s.get(m.InspectionArea, aid).version
    with db.session_scope() as s:                        # Prüfer A speichert zuerst
        ins.set_status(s, aid, "done", version=version)
    with pytest.raises(BusinessError, match="anderen Benutzer"):
        with db.session_scope() as s:                    # Prüfer B mit veraltetem Stand
            ins.set_status(s, aid, "clarify", note="x", version=version)


def test_inspector_role_modules():
    u = m.User(username="p", role="inspector")
    assert u.may("inspection") and not u.may("dashboard") and not u.may("finance")

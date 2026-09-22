"""Backend tests for Gantt System API"""
import os
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # Fallback to frontend env
    fe = Path(__file__).resolve().parents[2] / "frontend" / ".env"
    for line in fe.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
            break

API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def project(session):
    r = session.post(f"{API}/projects", json={
        "name": "TEST_GanttProject",
        "description": "test",
        "start_date": "2026-02-01",
    })
    assert r.status_code == 200, r.text
    p = r.json()
    assert "_id" not in p
    yield p
    session.delete(f"{API}/projects/{p['id']}")


# ---------- Projects ----------
def test_root(session):
    r = session.get(f"{API}/")
    assert r.status_code == 200


def test_list_projects(session, project):
    r = session.get(f"{API}/projects")
    assert r.status_code == 200
    arr = r.json()
    assert any(p["id"] == project["id"] for p in arr)
    for p in arr:
        assert "_id" not in p


def test_get_project(session, project):
    r = session.get(f"{API}/projects/{project['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "TEST_GanttProject"


# ---------- Activities ----------
def test_create_activity_invalid_duration(session, project):
    r = session.post(f"{API}/activities", json={
        "project_id": project["id"], "name": "Bad", "duration": 0, "predecessors": []
    })
    assert r.status_code == 400


def test_create_activity_invalid_project(session):
    r = session.post(f"{API}/activities", json={
        "project_id": "nonexistent", "name": "X", "duration": 1, "predecessors": []
    })
    assert r.status_code == 404


@pytest.fixture(scope="module")
def activities(session, project):
    """Create A(3d), B(5d, pred=A), C(2d, pred=B)"""
    pid = project["id"]
    a = session.post(f"{API}/activities", json={
        "project_id": pid, "name": "A", "duration": 3, "predecessors": []
    }).json()
    b = session.post(f"{API}/activities", json={
        "project_id": pid, "name": "B", "duration": 5, "predecessors": [a["id"]]
    }).json()
    c = session.post(f"{API}/activities", json={
        "project_id": pid, "name": "C", "duration": 2, "predecessors": [b["id"]]
    }).json()
    for x in (a, b, c):
        assert "_id" not in x
        assert "id" in x
    return {"a": a, "b": b, "c": c}


def test_list_activities_filter(session, project, activities):
    r = session.get(f"{API}/activities", params={"project_id": project["id"]})
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) >= 3
    for a in arr:
        assert "_id" not in a


def test_update_activity(session, activities):
    aid = activities["a"]["id"]
    r = session.put(f"{API}/activities/{aid}", json={"responsible": "John"})
    assert r.status_code == 200
    assert r.json()["responsible"] == "John"

    r = session.put(f"{API}/activities/{aid}", json={"duration": 0})
    assert r.status_code == 400


# ---------- Gantt computation ----------
def test_gantt_original_dates(session, project, activities):
    r = session.get(f"{API}/projects/{project['id']}/gantt")
    assert r.status_code == 200
    g = r.json()
    by_name = {a["name"]: a for a in g["activities"]}
    # A: Feb 1-3, B: Feb 4-8, C: Feb 9-10
    assert by_name["A"]["original_start"] == "2026-02-01"
    assert by_name["A"]["original_end"] == "2026-02-03"
    assert by_name["B"]["original_start"] == "2026-02-04"
    assert by_name["B"]["original_end"] == "2026-02-08"
    assert by_name["C"]["original_start"] == "2026-02-09"
    assert by_name["C"]["original_end"] == "2026-02-10"
    # No incidents -> modified == original
    assert by_name["B"]["modified_end"] == "2026-02-08"
    assert by_name["C"]["modified_end"] == "2026-02-10"
    assert g["stats"]["total_original_days"] == 10
    assert g["stats"]["schedule_slip_days"] == 0


# ---------- Incidents ----------
def test_create_incident_invalid_activity(session):
    r = session.post(f"{API}/incidents", json={
        "activity_id": "nope", "reason": "x", "delay_days": 1, "incident_date": "2026-02-05"
    })
    assert r.status_code == 404


def test_create_incident_negative_delay(session, activities):
    r = session.post(f"{API}/incidents", json={
        "activity_id": activities["b"]["id"], "reason": "x", "delay_days": -1,
        "incident_date": "2026-02-05"
    })
    assert r.status_code == 400


@pytest.fixture(scope="module")
def incident(session, activities):
    r = session.post(f"{API}/incidents", json={
        "activity_id": activities["b"]["id"],
        "reason": "Reprogramación",
        "impediment": "Gerente",
        "delay_days": 3,
        "detail": "x",
        "responsible": "Finanzas",
        "incident_date": "2026-02-05",
    })
    assert r.status_code == 200, r.text
    inc = r.json()
    assert "_id" not in inc
    return inc


def test_list_incidents_filters(session, project, activities, incident):
    r = session.get(f"{API}/incidents", params={"project_id": project["id"]})
    assert r.status_code == 200
    arr = r.json()
    assert any(i["id"] == incident["id"] for i in arr)

    r = session.get(f"{API}/incidents", params={"activity_id": activities["b"]["id"]})
    assert r.status_code == 200
    arr = r.json()
    assert any(i["id"] == incident["id"] for i in arr)


def test_gantt_modified_with_incident(session, project, activities, incident):
    r = session.get(f"{API}/projects/{project['id']}/gantt")
    g = r.json()
    by_name = {a["name"]: a for a in g["activities"]}
    # B: Feb 4-11 (5+3), C: Feb 12-13
    assert by_name["B"]["modified_start"] == "2026-02-04"
    assert by_name["B"]["modified_end"] == "2026-02-11"
    assert by_name["C"]["modified_start"] == "2026-02-12"
    assert by_name["C"]["modified_end"] == "2026-02-13"
    # original unchanged
    assert by_name["B"]["original_end"] == "2026-02-08"
    assert by_name["C"]["original_end"] == "2026-02-10"
    # stats
    assert g["stats"]["total_delay_days"] == 3
    assert g["stats"]["schedule_slip_days"] == 3
    assert g["stats"]["total_modified_days"] == 13
    assert g["stats"]["total_incidents"] == 1
    assert g["stats"]["total_activities"] == 3


def test_delete_incident(session, project, activities):
    # Create extra incident, delete it, verify gone
    r = session.post(f"{API}/incidents", json={
        "activity_id": activities["a"]["id"], "reason": "extra",
        "delay_days": 1, "incident_date": "2026-02-02"
    })
    iid = r.json()["id"]
    r = session.delete(f"{API}/incidents/{iid}")
    assert r.status_code == 200
    r = session.get(f"{API}/incidents", params={"activity_id": activities["a"]["id"]})
    assert all(i["id"] != iid for i in r.json())


def test_delete_activity_cleans_predecessors(session, project):
    """Create P1, P2 with pred=P1, then delete P1 and verify P2.predecessors no longer includes P1"""
    pid = project["id"]
    p1 = session.post(f"{API}/activities", json={
        "project_id": pid, "name": "TEST_P1", "duration": 1, "predecessors": []
    }).json()
    p2 = session.post(f"{API}/activities", json={
        "project_id": pid, "name": "TEST_P2", "duration": 1, "predecessors": [p1["id"]]
    }).json()
    r = session.delete(f"{API}/activities/{p1['id']}")
    assert r.status_code == 200
    # Refetch P2
    r = session.get(f"{API}/activities", params={"project_id": pid})
    p2_new = next(a for a in r.json() if a["id"] == p2["id"])
    assert p1["id"] not in p2_new["predecessors"]
    session.delete(f"{API}/activities/{p2['id']}")


def test_delete_project_cascades(session):
    """Create a temporary project + activity + incident, delete project, verify cascade"""
    p = session.post(f"{API}/projects", json={
        "name": "TEST_Cascade", "description": "", "start_date": "2026-03-01"
    }).json()
    a = session.post(f"{API}/activities", json={
        "project_id": p["id"], "name": "X", "duration": 2, "predecessors": []
    }).json()
    inc = session.post(f"{API}/incidents", json={
        "activity_id": a["id"], "reason": "r", "delay_days": 1,
        "incident_date": "2026-03-02"
    }).json()

    r = session.delete(f"{API}/projects/{p['id']}")
    assert r.status_code == 200

    # Project gone
    assert session.get(f"{API}/projects/{p['id']}").status_code == 404
    # Activities gone
    arr = session.get(f"{API}/activities", params={"project_id": p["id"]}).json()
    assert arr == []
    # Incidents for that activity gone
    arr = session.get(f"{API}/incidents", params={"activity_id": a["id"]}).json()
    assert arr == []

from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone, date, timedelta
from io import BytesIO
import openpyxl


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")


# ---------- Models ----------
class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = ""
    start_date: str  # ISO date YYYY-MM-DD
    director: Optional[str] = ""


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    director: Optional[str] = None


class Project(ProjectBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


ACTIVITY_STATUSES = ["Sin iniciar", "En proceso", "Detenido", "En evaluación", "Finalizado"]


class ActivityBase(BaseModel):
    project_id: str
    name: str
    duration: int  # in days, >= 0
    predecessors: List[str] = []  # list of activity ids
    responsible: Optional[str] = ""
    category: Optional[str] = ""
    start_date: Optional[str] = None  # manual override ISO date
    end_date: Optional[str] = None    # manual override ISO date
    status: Optional[str] = "Sin iniciar"
    progress: Optional[int] = 0  # 0-100


class ActivityCreate(ActivityBase):
    pass


class ActivityUpdate(BaseModel):
    name: Optional[str] = None
    duration: Optional[int] = None
    predecessors: Optional[List[str]] = None
    responsible: Optional[str] = None
    category: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = None


class Activity(ActivityBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IncidentBase(BaseModel):
    activity_id: str
    reason: str  # MOTIVO
    impediment: Optional[str] = ""  # IMPEDIMENTO
    delay_days: int  # DIAS DE RETRASO
    detail: Optional[str] = ""  # DETALLE
    responsible: Optional[str] = ""  # RESPONSABLE
    incident_date: str  # ISO date


class IncidentCreate(IncidentBase):
    pass


class Incident(IncidentBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------- Project endpoints ----------
@api_router.get("/")
async def root():
    return {"message": "Gantt System API"}


@api_router.post("/projects", response_model=Project)
async def create_project(payload: ProjectCreate):
    proj = Project(**payload.model_dump())
    await db.projects.insert_one(proj.model_dump())
    return proj


@api_router.get("/projects")
async def list_projects():
    docs = await db.projects.find({}, {"_id": 0}).to_list(1000)
    # Enrich with end_date = max(modified_end of activities)
    for p in docs:
        sched = await _compute_schedule(p["id"])
        if sched and sched["schedule"]:
            max_end = max(s["mod_end"] for s in sched["schedule"].values())
            p["end_date"] = max_end.isoformat()
        else:
            p["end_date"] = None
    return docs


@api_router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str):
    doc = await db.projects.find_one({"id": project_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    return doc


@api_router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    await db.projects.delete_one({"id": project_id})
    # cascade delete activities & incidents
    acts = await db.activities.find({"project_id": project_id}, {"_id": 0, "id": 1}).to_list(1000)
    activity_ids = [a["id"] for a in acts]
    await db.activities.delete_many({"project_id": project_id})
    if activity_ids:
        await db.incidents.delete_many({"activity_id": {"$in": activity_ids}})
    return {"success": True}


@api_router.put("/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, payload: ProjectUpdate):
    existing = await db.projects.find_one({"id": project_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if updates:
        await db.projects.update_one({"id": project_id}, {"$set": updates})
    updated = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return updated


# ---------- Activity endpoints ----------
@api_router.post("/activities", response_model=Activity)
async def create_activity(payload: ActivityCreate):
    if payload.duration < 0:
        raise HTTPException(status_code=400, detail="Duration must be >= 0")
    if payload.status and payload.status not in ACTIVITY_STATUSES:
        raise HTTPException(status_code=400, detail=f"status inválido. Valores: {ACTIVITY_STATUSES}")
    if payload.progress is not None and (payload.progress < 0 or payload.progress > 100):
        raise HTTPException(status_code=400, detail="progress debe estar entre 0 y 100")
    # ensure project exists
    proj = await db.projects.find_one({"id": payload.project_id}, {"_id": 0})
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    act = Activity(**payload.model_dump())
    await db.activities.insert_one(act.model_dump())
    return act


@api_router.get("/activities", response_model=List[Activity])
async def list_activities(project_id: Optional[str] = None):
    q = {"project_id": project_id} if project_id else {}
    docs = await db.activities.find(q, {"_id": 0}).to_list(2000)
    return docs


@api_router.put("/activities/{activity_id}", response_model=Activity)
async def update_activity(activity_id: str, payload: ActivityUpdate):
    existing = await db.activities.find_one({"id": activity_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Activity not found")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "duration" in updates and updates["duration"] < 0:
        raise HTTPException(status_code=400, detail="Duration must be >= 0")
    if "status" in updates and updates["status"] not in ACTIVITY_STATUSES:
        raise HTTPException(status_code=400, detail=f"status inválido. Valores: {ACTIVITY_STATUSES}")
    if "progress" in updates and (updates["progress"] < 0 or updates["progress"] > 100):
        raise HTTPException(status_code=400, detail="progress debe estar entre 0 y 100")
    if updates:
        await db.activities.update_one({"id": activity_id}, {"$set": updates})
    doc = await db.activities.find_one({"id": activity_id}, {"_id": 0})
    return doc


@api_router.delete("/activities/{activity_id}")
async def delete_activity(activity_id: str):
    await db.activities.delete_one({"id": activity_id})
    await db.incidents.delete_many({"activity_id": activity_id})
    # remove this activity from other activities' predecessors
    await db.activities.update_many(
        {"predecessors": activity_id},
        {"$pull": {"predecessors": activity_id}},
    )
    return {"success": True}


# ---------- Incident endpoints ----------
@api_router.post("/incidents", response_model=Incident)
async def create_incident(payload: IncidentCreate):
    act = await db.activities.find_one({"id": payload.activity_id}, {"_id": 0})
    if not act:
        raise HTTPException(status_code=404, detail="Activity not found")
    if payload.delay_days < 0:
        raise HTTPException(status_code=400, detail="delay_days must be >= 0")
    inc = Incident(**payload.model_dump())
    await db.incidents.insert_one(inc.model_dump())
    return inc


@api_router.get("/incidents", response_model=List[Incident])
async def list_incidents(activity_id: Optional[str] = None, project_id: Optional[str] = None):
    if activity_id:
        docs = await db.incidents.find({"activity_id": activity_id}, {"_id": 0}).to_list(2000)
        return docs
    if project_id:
        acts = await db.activities.find({"project_id": project_id}, {"_id": 0, "id": 1}).to_list(2000)
        ids = [a["id"] for a in acts]
        docs = await db.incidents.find({"activity_id": {"$in": ids}}, {"_id": 0}).to_list(2000)
        return docs
    docs = await db.incidents.find({}, {"_id": 0}).to_list(2000)
    return docs


@api_router.delete("/incidents/{incident_id}")
async def delete_incident(incident_id: str):
    await db.incidents.delete_one({"id": incident_id})
    return {"success": True}


# ---------- Gantt computation ----------
def _topological_order(activities: List[dict]) -> List[dict]:
    """Return activities in topo order (predecessors first). Cycles produce fallback by creation order."""
    by_id = {a["id"]: a for a in activities}
    result = []
    visited = {}  # id -> state: 0 unvisited, 1 visiting, 2 done

    def dfs(node_id):
        state = visited.get(node_id, 0)
        if state == 2:
            return
        if state == 1:
            # cycle -> treat as already visited to break cycle
            return
        visited[node_id] = 1
        node = by_id.get(node_id)
        if node:
            for p in node.get("predecessors", []) or []:
                if p in by_id:
                    dfs(p)
            visited[node_id] = 2
            result.append(node)

    for a in activities:
        dfs(a["id"])
    return result


async def _compute_schedule(project_id: str):
    """Helper que devuelve project, activities, incidents, schedule y delay_map.
    Devuelve None si el proyecto no existe."""
    proj = await db.projects.find_one({"id": project_id}, {"_id": 0})
    if not proj:
        return None
    activities = await db.activities.find({"project_id": project_id}, {"_id": 0}).to_list(2000)
    incidents = []
    if activities:
        ids = [a["id"] for a in activities]
        incidents = await db.incidents.find({"activity_id": {"$in": ids}}, {"_id": 0}).to_list(2000)
    delay_map = {}
    for inc in incidents:
        delay_map[inc["activity_id"]] = delay_map.get(inc["activity_id"], 0) + int(inc.get("delay_days", 0))
    project_start = date.fromisoformat(proj["start_date"])
    topo = _topological_order(activities)
    schedule = {}
    for act in topo:
        aid = act["id"]
        duration = int(act.get("duration", 1))
        preds = act.get("predecessors", []) or []
        valid_preds = [p for p in preds if p in schedule]
        manual_start = act.get("start_date")
        manual_end = act.get("end_date")
        if manual_start:
            orig_start = date.fromisoformat(manual_start)
            mod_start = orig_start
        elif not valid_preds:
            orig_start = project_start
            mod_start = project_start
        else:
            orig_start = max(schedule[p]["orig_end"] for p in valid_preds) + timedelta(days=1)
            mod_start = max(schedule[p]["mod_end"] for p in valid_preds) + timedelta(days=1)
        if manual_end:
            orig_end = date.fromisoformat(manual_end)
        else:
            span = max(duration - 1, 0)
            orig_end = orig_start + timedelta(days=span)
        own_delay = delay_map.get(aid, 0)
        if manual_start:
            mod_end = orig_end + timedelta(days=own_delay)
        else:
            mod_end = mod_start + timedelta(days=max(duration - 1, 0) + own_delay)
        schedule[aid] = {
            "orig_start": orig_start,
            "orig_end": orig_end,
            "mod_start": mod_start,
            "mod_end": mod_end,
        }
    return {
        "project": proj,
        "activities": activities,
        "incidents": incidents,
        "schedule": schedule,
        "delay_map": delay_map,
    }


@api_router.get("/projects/{project_id}/gantt")
async def compute_gantt(project_id: str):
    sched = await _compute_schedule(project_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Project not found")
    proj = sched["project"]
    activities = sched["activities"]
    incidents = sched["incidents"]
    schedule = sched["schedule"]
    delay_map = sched["delay_map"]

    result_acts = []
    for act in activities:
        s = schedule.get(act["id"])
        if not s:
            continue
        result_acts.append({
            "id": act["id"],
            "name": act["name"],
            "duration": act["duration"],
            "predecessors": act.get("predecessors", []),
            "responsible": act.get("responsible", ""),
            "category": act.get("category", ""),
            "status": act.get("status") or "Sin iniciar",
            "progress": int(act.get("progress") or 0),
            "original_start": s["orig_start"].isoformat(),
            "original_end": s["orig_end"].isoformat(),
            "modified_start": s["mod_start"].isoformat(),
            "modified_end": s["mod_end"].isoformat(),
            "own_delay_days": delay_map.get(act["id"], 0),
        })

    # stats
    total_original_days = 0
    total_modified_days = 0
    if result_acts:
        min_orig = min(date.fromisoformat(a["original_start"]) for a in result_acts)
        max_orig = max(date.fromisoformat(a["original_end"]) for a in result_acts)
        min_mod = min(date.fromisoformat(a["modified_start"]) for a in result_acts)
        max_mod = max(date.fromisoformat(a["modified_end"]) for a in result_acts)
        total_original_days = (max_orig - min_orig).days + 1
        total_modified_days = (max_mod - min_mod).days + 1

    by_status = {s: 0 for s in ACTIVITY_STATUSES}
    for a in result_acts:
        st = a.get("status") or "Sin iniciar"
        if st in by_status:
            by_status[st] += 1
        else:
            by_status["Sin iniciar"] += 1

    # ---------- Executive Summary ----------
    today = date.today()
    total_dur = sum(int(a.get("duration") or 0) for a in result_acts) or 1
    total_progress_weighted = sum(
        int(a.get("progress") or 0) * int(a.get("duration") or 0) for a in result_acts
    )
    progress_pct = round(total_progress_weighted / total_dur)

    if result_acts:
        proj_start_dt = min(date.fromisoformat(a["modified_start"]) for a in result_acts)
        proj_end_dt = max(date.fromisoformat(a["modified_end"]) for a in result_acts)
    else:
        proj_start_dt = date.fromisoformat(proj["start_date"])
        proj_end_dt = proj_start_dt
    span_days = max((proj_end_dt - proj_start_dt).days + 1, 1)
    elapsed = max((today - proj_start_dt).days, 0)
    time_consumed_pct = min(round(elapsed / span_days * 100), 100)

    overdue = [
        a for a in result_acts
        if date.fromisoformat(a["modified_end"]) < today and a.get("status") != "Finalizado"
    ]
    stopped = [a for a in result_acts if a.get("status") == "Detenido"]
    critical_unstarted = [
        a for a in result_acts
        if date.fromisoformat(a["modified_start"]) <= today and a.get("status") == "Sin iniciar"
    ]
    progress_lag = progress_pct < time_consumed_pct - 10
    many_incidents = len(incidents) >= 5

    detected_risks = []
    if overdue:
        detected_risks.append(f"{len(overdue)} actividades vencidas sin finalizar")
    if stopped:
        detected_risks.append(f"{len(stopped)} actividades detenidas")
    if critical_unstarted:
        detected_risks.append(
            f"{len(critical_unstarted)} actividades sin iniciar deberían estar en curso"
        )
    if progress_lag:
        detected_risks.append(
            f"Avance {progress_pct}% por debajo del tiempo consumido ({time_consumed_pct}%)"
        )
    if many_incidents:
        detected_risks.append(f"{len(incidents)} incidencias registradas")

    delay_total = sum(delay_map.values())
    if (progress_lag and (delay_total > 10 or len(stopped) > 2)) or len(overdue) >= 5:
        health_status = "high_risk"
    elif detected_risks:
        health_status = "warning"
    else:
        health_status = "ok"

    upcoming = []
    for a in result_acts:
        if a.get("status") == "Finalizado":
            continue
        end_d = date.fromisoformat(a["modified_end"])
        diff = (end_d - today).days
        if -3 <= diff <= 7:
            upcoming.append({
                "id": a["id"],
                "name": a["name"],
                "end_date": a["modified_end"],
                "days_until_end": diff,
                "category": a.get("category", ""),
                "status": a.get("status", "Sin iniciar"),
            })
    upcoming.sort(key=lambda x: x["days_until_end"])
    upcoming_critical = upcoming[:5]

    recommendations = []
    if stopped:
        recommendations.append("Atender bloqueos en actividades detenidas")
    if progress_lag:
        recommendations.append("Acelerar tareas con bajo avance")
    if overdue:
        recommendations.append("Replanificar o cerrar actividades vencidas")
    if many_incidents:
        recommendations.append("Revisar causas recurrentes de incidencias")
    if critical_unstarted:
        recommendations.append("Iniciar actividades pendientes que ya tocan en cronograma")

    # ---------- Upcoming Operational Risks ----------
    upcoming_operational_risks = []
    successors = {}
    for a in result_acts:
        for p in (a.get("predecessors") or []):
            successors.setdefault(p, []).append(a["id"])

    # 1. Próximas a vencer (3-5 días)
    deadline_acts = [
        a for a in result_acts
        if a.get("status") != "Finalizado"
        and 0 <= (date.fromisoformat(a["modified_end"]) - today).days <= 5
    ]
    if deadline_acts:
        sorted_dl = sorted(deadline_acts, key=lambda x: x["modified_end"])
        upcoming_operational_risks.append({
            "type": "deadline",
            "severity": "high" if len(deadline_acts) >= 4 else "warning",
            "title": f"{len(deadline_acts)} actividades vencen en los próximos 5 días",
            "detail": "; ".join(a["name"] for a in sorted_dl[:3])
                       + ("…" if len(deadline_acts) > 3 else ""),
            "affected_count": len(deadline_acts),
        })

    # 2. Críticas sin iniciar (con sucesoras y fecha cercana)
    critical_with_succ = []
    for a in result_acts:
        if a.get("status") != "Sin iniciar":
            continue
        sc = len(successors.get(a["id"], []))
        if sc < 2:
            continue
        try:
            start_d = date.fromisoformat(a["modified_start"])
        except ValueError:
            continue
        if (start_d - today).days <= 7:
            critical_with_succ.append((a, sc))
    critical_with_succ.sort(key=lambda x: -x[1])
    for a, sc in critical_with_succ[:3]:
        upcoming_operational_risks.append({
            "type": "critical_unstarted",
            "severity": "high",
            "title": f"{a['name']} aún no inicia y afecta {sc} actividades",
            "detail": a.get("category", "") or "Sin categoría",
            "affected_count": sc,
        })

    # 3. Responsables sobrecargados (>= 5 actividades activas)
    OVERLOAD_THRESHOLD = 5
    active_per_resp = {}
    for a in result_acts:
        if a.get("status") in ("En proceso", "Detenido", "En evaluación"):
            r = (a.get("responsible") or "").strip()
            if r:
                active_per_resp[r] = active_per_resp.get(r, 0) + 1
    for resp, count in sorted(active_per_resp.items(), key=lambda x: -x[1]):
        if count >= OVERLOAD_THRESHOLD:
            upcoming_operational_risks.append({
                "type": "overloaded_responsible",
                "severity": "warning",
                "title": f"{resp} tiene {count} actividades activas",
                "detail": "Posible sobrecarga operativa",
                "affected_count": count,
            })

    # 4. Detenidas sin seguimiento (>= 5 días)
    incidents_by_act = {}
    for inc in incidents:
        incidents_by_act.setdefault(inc["activity_id"], []).append(inc)
    STALE_DAYS = 5
    stale = []
    for a in result_acts:
        if a.get("status") != "Detenido":
            continue
        acts_incs = incidents_by_act.get(a["id"], [])
        latest_days_ago = None
        if acts_incs:
            latest = max((inc.get("incident_date", "") for inc in acts_incs), default="")
            if latest:
                try:
                    latest_days_ago = (today - date.fromisoformat(latest)).days
                except ValueError:
                    latest_days_ago = None
        if latest_days_ago is None or latest_days_ago >= STALE_DAYS:
            stale.append((a, latest_days_ago))
    if stale:
        max_days = max((s[1] for s in stale if s[1] is not None), default=STALE_DAYS)
        upcoming_operational_risks.append({
            "type": "stale_stopped",
            "severity": "high",
            "title": f"{len(stale)} actividades detenidas sin actualización reciente",
            "detail": f"Sin novedades hace {max_days}+ días",
            "affected_count": len(stale),
        })

    # 5. Cuellos de botella (retrasadas con muchas sucesoras)
    bottlenecks = []
    for a in result_acts:
        delay = a.get("own_delay_days") or 0
        if delay <= 0:
            continue
        sc = len(successors.get(a["id"], []))
        if sc >= 2:
            bottlenecks.append((a, sc))
    bottlenecks.sort(key=lambda x: -x[1])
    for a, sc in bottlenecks[:3]:
        upcoming_operational_risks.append({
            "type": "bottleneck",
            "severity": "high",
            "title": f"{a['name']} bloquea {sc} actividades",
            "detail": f"Con {a['own_delay_days']}d de retraso",
            "affected_count": sc,
        })

    # 6. Progreso inconsistente
    for a in result_acts:
        if a.get("status") == "Finalizado":
            continue
        try:
            start_d = date.fromisoformat(a["modified_start"])
            end_d = date.fromisoformat(a["modified_end"])
        except ValueError:
            continue
        span = max((end_d - start_d).days + 1, 1)
        elapsed = (today - start_d).days
        if elapsed <= 0:
            continue
        time_pct = min(int(elapsed / span * 100), 100)
        progress = a.get("progress") or 0
        if time_pct >= 60 and progress < time_pct - 30:
            upcoming_operational_risks.append({
                "type": "inconsistent_progress",
                "severity": "warning",
                "title": f"{a['name']}: {time_pct}% tiempo, solo {progress}% avance",
                "detail": "Avance inconsistente con cronograma",
                "affected_count": 1,
            })

    severity_rank = {"high": 0, "warning": 1, "info": 2}
    upcoming_operational_risks.sort(
        key=lambda r: (severity_rank.get(r["severity"], 99), -r.get("affected_count", 0))
    )
    upcoming_operational_risks = upcoming_operational_risks[:10]

    return {
        "project": proj,
        "activities": result_acts,
        "incidents": incidents,
        "stats": {
            "total_activities": len(result_acts),
            "total_incidents": len(incidents),
            "total_delay_days": sum(delay_map.values()),
            "total_original_days": total_original_days,
            "total_modified_days": total_modified_days,
            "schedule_slip_days": total_modified_days - total_original_days,
            "by_status": by_status,
        },
        "executive_summary": {
            "health_status": health_status,
            "time_consumed_pct": time_consumed_pct,
            "progress_pct": progress_pct,
            "detected_risks": detected_risks,
            "upcoming_critical": upcoming_critical,
            "recommendations": recommendations,
        },
        "upcoming_operational_risks": upcoming_operational_risks,
    }


@api_router.get("/gantt-general")
async def gantt_general():
    """Devuelve el Gantt computado de TODOS los proyectos."""
    projects = await db.projects.find({}, {"_id": 0}).to_list(1000)
    out = []
    for proj in projects:
        gantt = await compute_gantt(proj["id"])
        out.append(gantt)
    return {"projects": out}


# ---------- Excel import ----------
def _norm(s):
    if s is None:
        return ""
    return str(s).strip().lower()


def _to_date(v):
    if v is None:
        return None
    if hasattr(v, "date"):
        try:
            return v.date()
        except Exception:
            pass
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except Exception:
            return None
    return None


@api_router.post("/import/sheets")
async def import_sheets(file: UploadFile = File(...)):
    """Lista las hojas de un Excel con sus encabezados detectados (fila 1)."""
    content = await file.read()
    try:
        wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo leer el archivo: {exc}")
    sheets = []
    for name in wb.sheetnames:
        ws = wb[name]
        header = []
        for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
            header = [str(c).strip() if c is not None else "" for c in row]
            break
        sheets.append({
            "name": name,
            "headers": header,
            "rows": max(0, (ws.max_row or 1) - 1),
        })
    return {"sheets": sheets}


@api_router.post("/import/excel")
async def import_excel(
    file: UploadFile = File(...),
    sheet: str = Form(...),
    mode: str = Form("append"),
):
    """Importa una hoja del Excel. Crea proyectos/actividades según las columnas
    Proyecto / Categoría / Actividades / Fecha inicio / Fecha Fin.

    mode:
      - append: si el proyecto existe, agrega las nuevas actividades.
      - replace: si el proyecto existe, elimina sus actividades e incidencias
        actuales antes de crear las nuevas.
    """
    if mode not in ("append", "replace"):
        raise HTTPException(status_code=400, detail="mode debe ser 'append' o 'replace'")
    content = await file.read()
    try:
        wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo leer el archivo: {exc}")
    if sheet not in wb.sheetnames:
        raise HTTPException(status_code=400, detail="Hoja no encontrada en el archivo")
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="La hoja está vacía")

    header = [_norm(c) for c in rows[0]]

    def col_idx(*candidates):
        for cand in candidates:
            target = _norm(cand)
            for i, h in enumerate(header):
                if h == target:
                    return i
            for i, h in enumerate(header):
                if target and target in h:
                    return i
        return None

    idx_proj = col_idx("proyecto")
    idx_cat = col_idx("categoría", "categoria")
    idx_act = col_idx("actividades", "actividad")
    idx_start = col_idx("fecha inicio", "fecha de inicio")
    idx_end = col_idx("fecha fin", "fecha final", "fecha de fin")
    idx_resp = col_idx("responsable", "responsible", "asignado", "owner")
    idx_status = col_idx("estado", "estatus", "status")

    missing = []
    if idx_proj is None:
        missing.append("Proyecto")
    if idx_act is None:
        missing.append("Actividades")
    if idx_start is None:
        missing.append("Fecha inicio")
    if idx_end is None:
        missing.append("Fecha Fin")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Columnas faltantes en la hoja: {', '.join(missing)}",
        )

    by_project: dict = {}
    skipped = 0
    for r in rows[1:]:
        if r is None:
            continue
        max_idx = max(i for i in [idx_proj, idx_act, idx_start, idx_end] if i is not None)
        if len(r) <= max_idx:
            skipped += 1
            continue
        proj_val = r[idx_proj]
        act_val = r[idx_act]
        if proj_val is None or str(proj_val).strip() == "":
            skipped += 1
            continue
        if act_val is None or str(act_val).strip() == "":
            skipped += 1
            continue
        start = _to_date(r[idx_start])
        end = _to_date(r[idx_end])
        if start is None or end is None:
            skipped += 1
            continue
        cat_val = r[idx_cat] if idx_cat is not None else ""
        resp_val = r[idx_resp] if idx_resp is not None else ""
        status_val = r[idx_status] if idx_status is not None else None
        # Normalize status: accept case/accent variations, default to "Sin iniciar"
        status_resolved = "Sin iniciar"
        if status_val:
            s_norm = _norm(status_val)
            for valid in ACTIVITY_STATUSES:
                if _norm(valid) == s_norm:
                    status_resolved = valid
                    break
        by_project.setdefault(str(proj_val).strip(), []).append({
            "name": str(act_val).strip(),
            "category": str(cat_val).strip() if cat_val else "",
            "responsible": str(resp_val).strip() if resp_val else "",
            "status": status_resolved,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "duration": max((end - start).days + 1, 0),
        })

    summary = []
    for proj_name, acts in by_project.items():
        existing = await db.projects.find_one({"name": proj_name}, {"_id": 0})
        if existing:
            project_id = existing["id"]
            replaced = 0
            if mode == "replace":
                # remove existing activities and their incidents
                old_acts = await db.activities.find(
                    {"project_id": project_id}, {"_id": 0, "id": 1}
                ).to_list(2000)
                old_ids = [a["id"] for a in old_acts]
                replaced = len(old_ids)
                if old_ids:
                    await db.incidents.delete_many({"activity_id": {"$in": old_ids}})
                await db.activities.delete_many({"project_id": project_id})
            action = "replaced" if mode == "replace" else "appended"
        else:
            min_start = min(a["start_date"] for a in acts)
            new_proj = Project(
                name=proj_name,
                description=f"Importado desde Excel · hoja '{sheet}'",
                start_date=min_start,
            )
            await db.projects.insert_one(new_proj.model_dump())
            project_id = new_proj.id
            action = "created"
            replaced = 0

        created = 0
        for a in acts:
            act_obj = Activity(
                project_id=project_id,
                name=a["name"],
                duration=a["duration"],
                predecessors=[],
                responsible=a.get("responsible", ""),
                category=a["category"],
                start_date=a["start_date"],
                end_date=a["end_date"],
                status=a.get("status", "Sin iniciar"),
            )
            await db.activities.insert_one(act_obj.model_dump())
            created += 1

        summary.append({
            "project": proj_name,
            "project_id": project_id,
            "action": action,
            "activities_created": created,
            "activities_replaced": replaced,
        })

    return {
        "summary": summary,
        "total_projects": len(summary),
        "total_activities": sum(s["activities_created"] for s in summary),
        "skipped_rows": skipped,
        "sheet": sheet,
        "mode": mode,
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

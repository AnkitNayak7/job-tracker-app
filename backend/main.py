import asyncio
import io
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import pipeline
from auth import (
    COOKIE_NAME,
    MAX_AGE_SECONDS,
    SITE_PASSWORD,
    make_session_token,
    require_api_key,
    require_session,
)
from db import ensure_indexes, get_db, make_dedupe_key, now_iso

app = FastAPI(title="Job Tracker")
logger = logging.getLogger("job_tracker")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

STALE_DAYS = 60
SCHEDULED_RUN_HOUR_UTC = 1  # ~7:00am IST

_pipeline_lock = asyncio.Lock()


@app.on_event("startup")
async def _startup():
    await ensure_indexes()
    asyncio.create_task(_daily_scheduler())


async def _daily_scheduler():
    """Runs the pipeline automatically once a day, around SCHEDULED_RUN_HOUR_UTC.
    Checked every 5 minutes rather than tied to a specific minute so a brief
    restart near the target hour doesn't cause a missed day."""
    last_run_date = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.hour == SCHEDULED_RUN_HOUR_UTC:
                today = now.date().isoformat()
                if last_run_date != today:
                    last_run_date = today
                    await _run_pipeline_guarded()
        except Exception:
            logger.exception("daily scheduler tick failed")
        await asyncio.sleep(300)


async def _run_pipeline_guarded():
    if _pipeline_lock.locked():
        return  # a run (scheduled or manual) is already in progress
    async with _pipeline_lock:
        await db.set_pipeline_state(status="running", started_at=now_iso())
        try:
            result = await pipeline.run_pipeline_once()
            await db.set_pipeline_state(
                status="idle", last_run_at=now_iso(), last_result=result, last_error=None,
            )
        except Exception as e:
            logger.exception("pipeline run failed")
            await db.set_pipeline_state(status="idle", last_run_at=now_iso(), last_error=str(e))
            await db.log_run(
                run_date=date.today().isoformat(), notes=f"Run failed: {e}",
            )


# ---------------------------------------------------------------- auth -----

class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody, response: Response):
    if not SITE_PASSWORD:
        raise HTTPException(500, "Server has no SITE_PASSWORD configured")
    if body.password != SITE_PASSWORD:
        raise HTTPException(401, "Wrong password")
    token = make_session_token()
    response.set_cookie(
        COOKIE_NAME, token, max_age=MAX_AGE_SECONDS, httponly=True,
        samesite="lax", secure=True, path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(ok: bool = Depends(require_session)):
    return {"ok": True}


# ---------------------------------------------------------------- jobs -----

def _job_out(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.get("/api/jobs")
async def list_jobs(
    source: Optional[str] = None,
    applied: Optional[str] = None,
    include_stale: bool = False,
    ok: bool = Depends(require_session),
):
    db = get_db()
    query: dict = {}
    if source:
        query["source"] = source
    if applied in ("yes", "no"):
        query["applied"] = applied == "yes"
    if not include_stale:
        cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
        # keep it if it's recent OR already applied (never hide applied history)
        query["$or"] = [{"posted_date": {"$gte": cutoff}}, {"applied": True}]
    cursor = db.jobs.find(query).sort([("fit_score", -1), ("posted_date", -1)])
    jobs = [_job_out(d) async for d in cursor]
    return jobs


class AppliedBody(BaseModel):
    applied: bool


@app.patch("/api/jobs/{job_id}")
async def update_job(job_id: str, body: AppliedBody, ok: bool = Depends(require_session)):
    from bson import ObjectId
    db = get_db()
    update = {"applied": body.applied, "updated_at": now_iso()}
    update["applied_at"] = now_iso() if body.applied else None
    res = await db.jobs.update_one({"_id": ObjectId(job_id)}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Job not found")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/resume")
async def get_resume(job_id: str, ok: bool = Depends(require_session)):
    from bson import ObjectId
    db = get_db()
    resume = await db.resumes.find_one({"job_id": job_id})
    if not resume:
        raise HTTPException(404, "No tailored resume for this job yet")
    job = await db.jobs.find_one({"_id": ObjectId(job_id)})
    docx_bytes = _text_to_docx(resume["content_text"])
    filename = "resume_{}.docx".format((job or {}).get("company_or_family", "tailored").replace(" ", "_"))
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _text_to_docx(text: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            doc.add_paragraph("")
            continue
        is_heading = line.strip() == line.strip().upper() and len(line.strip()) < 60 and any(c.isalpha() for c in line)
        if is_heading:
            p = doc.add_paragraph()
            run = p.add_run(line.strip())
            run.bold = True
            run.font.size = Pt(12)
        else:
            doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@app.get("/api/summary")
async def summary(ok: bool = Depends(require_session)):
    db = get_db()
    cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    active_or_applied = {"$or": [{"posted_date": {"$gte": cutoff}}, {"applied": True}]}
    out = {}
    for source in ("Accenture", "Product", "Watchlist"):
        q = {"source": source, **active_or_applied}
        total = await db.jobs.count_documents(q)
        strong = await db.jobs.count_documents({**q, "fit_score": {"$gte": 4}})
        reviewed = await db.jobs.count_documents({**q, "jd_reviewed": True})
        applied_count = await db.jobs.count_documents({"source": source, "applied": True})
        out[source] = {
            "total": total, "strong_or_excellent": strong,
            "jd_reviewed": reviewed, "applied": applied_count,
        }
    out["applied_total"] = await db.jobs.count_documents({"applied": True})
    out["resumes_generated"] = await db.resumes.count_documents({})
    return out


@app.get("/api/runs")
async def list_runs(limit: int = 20, ok: bool = Depends(require_session)):
    db = get_db()
    cursor = db.runs.find({}).sort("run_date", -1).limit(limit)
    out = []
    async for d in cursor:
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


# ------------------------------------------------------------ watchlist ----

class WatchlistItem(BaseModel):
    company_name: str
    career_url: str
    notes: Optional[str] = ""


def _watchlist_out(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.get("/api/watchlist")
async def list_watchlist(ok: bool = Depends(require_session)):
    db = get_db()
    cursor = db.watchlist.find({}).sort("company_name", 1)
    return [_watchlist_out(d) async for d in cursor]


@app.post("/api/watchlist")
async def add_watchlist(item: WatchlistItem, ok: bool = Depends(require_session)):
    db = get_db()
    doc = item.model_dump()
    doc["created_at"] = now_iso()
    doc["updated_at"] = now_iso()
    res = await db.watchlist.insert_one(doc)
    return {"id": str(res.inserted_id)}


@app.put("/api/watchlist/{item_id}")
async def update_watchlist(item_id: str, item: WatchlistItem, ok: bool = Depends(require_session)):
    from bson import ObjectId
    db = get_db()
    update = item.model_dump()
    update["updated_at"] = now_iso()
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(400, "Invalid watchlist id")
    res = await db.watchlist.update_one({"_id": oid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Watchlist item not found")
    return {"ok": True}


@app.delete("/api/watchlist/{item_id}")
async def delete_watchlist(item_id: str, ok: bool = Depends(require_session)):
    from bson import ObjectId
    db = get_db()
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(400, "Invalid watchlist id")
    res = await db.watchlist.delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise HTTPException(404, "Watchlist item not found")
    return {"ok": True}


# --------------------------------------------------------- pipeline control --
# The pipeline runs in-process (see pipeline.py) - "run now" just launches it
# as a background task immediately, guarded so only one run happens at a
# time. No routine/trigger credential involved.

@app.post("/api/pipeline/run_now")
async def request_pipeline_run(ok: bool = Depends(require_session)):
    if _pipeline_lock.locked():
        return {"ok": True, "already_running": True}
    asyncio.create_task(_run_pipeline_guarded())
    return {"ok": True, "already_running": False}


@app.get("/api/pipeline/status")
async def pipeline_status(ok: bool = Depends(require_session)):
    state = await db.get_pipeline_state()
    return {
        "running": _pipeline_lock.locked(),
        "last_run_at": state.get("last_run_at"),
        "last_result": state.get("last_result"),
        "last_error": state.get("last_error"),
    }


# ------------------------------------------------------- internal (pipeline) --

@app.get("/api/internal/watchlist")
async def internal_list_watchlist(ok: bool = Depends(require_api_key)):
    """Kept for external/manual inspection - the in-process pipeline
    (pipeline.py) reads the watchlist directly via db.list_watchlist()."""
    return await db.list_watchlist()


@app.get("/api/internal/config/base_resume")
async def get_base_resume(ok: bool = Depends(require_api_key)):
    db = get_db()
    doc = await db.config.find_one({"_id": "base_resume"})
    if not doc:
        return {"text": "", "updated_at": None}
    return {"text": doc.get("text", ""), "updated_at": doc.get("updated_at")}


class BaseResumeBody(BaseModel):
    text: str
    filename: Optional[str] = None


async def _set_base_resume(text: str, filename: Optional[str]):
    db = get_db()
    await db.config.update_one(
        {"_id": "base_resume"},
        {"$set": {"text": text, "filename": filename, "updated_at": now_iso()}},
        upsert=True,
    )


@app.post("/api/internal/config/base_resume")
async def set_base_resume(body: BaseResumeBody, ok: bool = Depends(require_api_key)):
    await _set_base_resume(body.text, body.filename)
    return {"ok": True}


@app.get("/api/internal/config/base_resume/set")
async def set_base_resume_get(
    text: str = Query(...),
    filename: Optional[str] = Query(default=None),
    ok: bool = Depends(require_api_key),
):
    """GET mirror of the POST above, for a caller that can only make GET
    requests with no custom body (see DAILY_PIPELINE_RUNBOOK.md)."""
    await _set_base_resume(text, filename)
    return {"ok": True}


@app.get("/api/internal/jobs")
async def internal_list_jobs(source: Optional[str] = None, ok: bool = Depends(require_api_key)):
    """Used by the pipeline to see what's already stored, for dedup."""
    db = get_db()
    query = {"source": source} if source else {}
    cursor = db.jobs.find(query, {"dedupe_key": 1, "title": 1, "company_or_family": 1, "posted_date": 1, "apply_url": 1})
    out = []
    async for d in cursor:
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


@app.post("/api/internal/jobs/upsert")
async def upsert_jobs(jobs: list[dict] = Body(...), ok: bool = Depends(require_api_key)):
    inserted, updated = 0, 0
    for job in jobs:
        result = await db.upsert_one_job(job)
        if result == "inserted":
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated}


@app.get("/api/internal/jobs/upsert_one")
async def upsert_one_job_get(job: str = Query(...), ok: bool = Depends(require_api_key)):
    """GET mirror of the POST above, one job per call (job=URL-encoded JSON
    object). For a caller that can only make GET requests - see
    DAILY_PIPELINE_RUNBOOK.md."""
    try:
        job_dict = json.loads(job)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"job must be valid JSON: {e}")
    result = await db.upsert_one_job(job_dict)
    return {result: 1}


class ResumeBody(BaseModel):
    job_id: str
    content_text: str
    base_resume_version: Optional[str] = None


@app.post("/api/internal/resumes")
async def save_resume(body: ResumeBody, ok: bool = Depends(require_api_key)):
    await db.save_resume(body.job_id, body.content_text, body.base_resume_version)
    return {"ok": True}


@app.get("/api/internal/resumes/save")
async def save_resume_get(
    job_id: str = Query(...),
    content_text: str = Query(...),
    base_resume_version: Optional[str] = Query(default=None),
    ok: bool = Depends(require_api_key),
):
    """GET mirror of the POST above - see README.md."""
    await db.save_resume(job_id, content_text, base_resume_version)
    return {"ok": True}


class RunBody(BaseModel):
    run_date: str
    jobs_added: int = 0
    jobs_reviewed: int = 0
    resumes_generated: int = 0
    notes: str = ""


@app.post("/api/internal/runs")
async def log_run_route(body: RunBody, ok: bool = Depends(require_api_key)):
    await db.log_run(body.run_date, body.jobs_added, body.jobs_reviewed, body.resumes_generated, body.notes)
    return {"ok": True}


@app.get("/api/internal/runs/log")
async def log_run_get(
    run_date: str = Query(...),
    jobs_added: int = Query(default=0),
    jobs_reviewed: int = Query(default=0),
    resumes_generated: int = Query(default=0),
    notes: str = Query(default=""),
    ok: bool = Depends(require_api_key),
):
    """GET mirror of the POST above - see README.md."""
    await db.log_run(run_date, jobs_added, jobs_reviewed, resumes_generated, notes)
    return {"ok": True}


# ------------------------------------------------------------- static UI ---

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health():
    return {"status": "ok"}

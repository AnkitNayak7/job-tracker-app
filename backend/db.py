"""MongoDB access layer for the Job Tracker app.

Collections
-----------
jobs      One document per job posting.
            {
              _id, dedupe_key (unique),
              source: "Accenture" | "Product",
              title, company_or_family, location,
              posted_date (YYYY-MM-DD), experience_req,
              fit_score (1-5), fit_label, jd_reviewed (bool), why,
              apply_url,
              applied (bool), applied_at (iso or None),
              resume_tailored (bool),
              created_at, updated_at
            }
resumes   One document per tailored resume, keyed by job_id.
            { _id, job_id, generated_at, content_text, base_resume_version }
config    Small singleton docs, keyed by _id.
            { _id: "base_resume", text, updated_at, filename }
            { _id: "pipeline_control", manual_run_requested, requested_at, last_run_at }
runs      One document per daily pipeline run (append-only log).
            { _id, run_date, jobs_added, jobs_reviewed, resumes_generated, notes, finished_at }
watchlist One document per user-submitted company/career page to check every run.
            { _id, company_name, career_url, notes, created_at, updated_at }
"""
import hashlib
import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB_NAME", "job_tracker")

_client = None


def get_client():
    global _client
    if _client is None:
        if MONGO_URL == "mongomock":
            # Local smoke-testing only (no real Mongo server needed).
            from mongomock_motor import AsyncMongoMockClient
            _client = AsyncMongoMockClient()
        else:
            _client = AsyncIOMotorClient(MONGO_URL)
    return _client


def get_db():
    return get_client()[DB_NAME]


async def ensure_indexes():
    db = get_db()
    await db.jobs.create_index("dedupe_key", unique=True)
    await db.jobs.create_index("source")
    await db.jobs.create_index("applied")
    await db.jobs.create_index("posted_date")
    await db.resumes.create_index("job_id", unique=True)
    await db.runs.create_index("run_date")
    await db.watchlist.create_index("company_name")


def make_dedupe_key(job: dict) -> str:
    """Stable identity for a posting: prefer the apply URL (stripped of
    query params, which are often tracking noise), else fall back to a
    composite of title+company+location+posted_date."""
    url = (job.get("apply_url") or "").strip()
    if url:
        base = url.split("?", 1)[0].rstrip("/").lower()
        return "url:" + hashlib.sha1(base.encode()).hexdigest()
    composite = "|".join([
        (job.get("source") or "").lower(),
        (job.get("title") or "").strip().lower(),
        (job.get("company_or_family") or "").strip().lower(),
        (job.get("location") or "").strip().lower(),
        (job.get("posted_date") or "").strip(),
    ])
    return "composite:" + hashlib.sha1(composite.encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_one_job(job: dict) -> str:
    """Insert or update a job posting by its dedupe key. Returns 'inserted' or
    'updated'. Shared by the internal HTTP endpoints and the in-process
    pipeline (backend/pipeline.py) so both write through the same path."""
    db = get_db()
    job = dict(job)
    key = make_dedupe_key(job)
    job["dedupe_key"] = key
    job.setdefault("applied", False)
    job.setdefault("applied_at", None)
    job.setdefault("resume_tailored", False)
    job["updated_at"] = now_iso()
    existing = await db.jobs.find_one({"dedupe_key": key})
    if existing:
        # never clobber applied state or timestamps set by the user
        job.pop("applied", None)
        job.pop("applied_at", None)
        await db.jobs.update_one({"dedupe_key": key}, {"$set": job})
        return "updated"
    job["created_at"] = now_iso()
    await db.jobs.insert_one(job)
    return "inserted"


async def find_job_id_by_apply_url(apply_url: str) -> "str | None":
    """Look up a job's Mongo id by its apply_url (via the same dedupe key
    upsert_one_job used), for linking a tailored resume to its posting."""
    db = get_db()
    key = make_dedupe_key({"apply_url": apply_url})
    doc = await db.jobs.find_one({"dedupe_key": key}, {"_id": 1})
    return str(doc["_id"]) if doc else None


async def save_resume(job_id: str, content_text: str, base_resume_version: "str | None" = None) -> None:
    from bson import ObjectId
    db = get_db()
    await db.resumes.update_one(
        {"job_id": job_id},
        {"$set": {
            "job_id": job_id,
            "content_text": content_text,
            "base_resume_version": base_resume_version,
            "generated_at": now_iso(),
        }},
        upsert=True,
    )
    await db.jobs.update_one({"_id": ObjectId(job_id)}, {"$set": {"resume_tailored": True}})


async def log_run(run_date: str, jobs_added: int = 0, jobs_reviewed: int = 0,
                   resumes_generated: int = 0, notes: str = "") -> None:
    db = get_db()
    doc = {
        "run_date": run_date, "jobs_added": jobs_added, "jobs_reviewed": jobs_reviewed,
        "resumes_generated": resumes_generated, "notes": notes, "finished_at": now_iso(),
    }
    await db.runs.insert_one(doc)


async def get_base_resume_text() -> str:
    db = get_db()
    doc = await db.config.find_one({"_id": "base_resume"})
    return (doc or {}).get("text", "")


async def list_watchlist() -> list:
    db = get_db()
    out = []
    async for d in db.watchlist.find({}):
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


async def list_job_summaries(source: "str | None" = None) -> list:
    """Compact view of already-tracked postings (title/company/location/date/
    apply_url) for a source, so the pipeline can dedupe without resending or
    re-rating full job docs."""
    db = get_db()
    query = {"source": source} if source else {}
    projection = {"title": 1, "company_or_family": 1, "location": 1, "posted_date": 1, "apply_url": 1}
    out = []
    async for d in db.jobs.find(query, projection):
        d.pop("_id", None)
        out.append(d)
    return out


async def get_pipeline_state() -> dict:
    db = get_db()
    return await db.config.find_one({"_id": "pipeline_control"}) or {}


async def set_pipeline_state(**fields) -> None:
    db = get_db()
    await db.config.update_one({"_id": "pipeline_control"}, {"$set": fields}, upsert=True)

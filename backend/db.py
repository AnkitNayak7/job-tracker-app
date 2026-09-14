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

"""The job-pull pipeline, running as backend code instead of an external
agent session.

Why it's built this way: this used to be a prompt for a scheduled Claude
Code cloud routine that called back into this backend's HTTP API. That
routine's sandbox turned out to block outbound network access to arbitrary
hosts (including this app's own Railway domain) for both `curl` and
`WebFetch` - so a routine could never actually reach this backend at all.
Moving the pipeline in-process sidesteps that: it calls the Claude API
directly (a normal outbound HTTPS call any Railway service can make) and
writes to MongoDB directly, with no cross-service network hop to block.

This also makes "run it now" instant and self-serve (see main.py's
/api/pipeline/run_now) instead of depending on a session's own trigger
credential, which is never exportable outside that session.

Model/cost note: uses Claude Opus 5 by default. A full run does several
web searches plus rating/tailoring reasoning - real, metered API cost
(distinct from a Claude subscription). Set PIPELINE_MODEL to
"claude-sonnet-5" as a cheaper alternative if that matters more than
top-end judgment quality for this task.
"""
import os
from datetime import date, timedelta

import anthropic

import db

MODEL = os.environ.get("PIPELINE_MODEL", "claude-opus-5")
STALE_DAYS = 60
MAX_LOOP_ITERATIONS = 60  # safety cap on assistant turns per run, not on job count

ROLE_FAMILIES = [
    "TPM / Program Manager",
    "AI & GenAI Product Manager",
    "Delivery / Engagement Manager",
]
LOCATIONS = ["Bengaluru", "Hyderabad", "Pune"]

WHO_THIS_IS_FOR = """\
Ankit Nayak - Technical Program Manager (8.9+ yrs) at Rockwell Automation,
Bengaluru. Enterprise data platforms, IT-OT integration, GenAI/RAG systems,
PMP-certified, GxP/21 CFR Part 11 domain experience (pharma: Pfizer, Amgen;
manufacturing: Aarti Industries, Exide, Grasim; energy). AWS (IoT Hub,
Lambda, Step Functions, Redshift), Kafka, Databricks, Agile/SAFe delivery."""

RECORD_JOB_TOOL = {
    "name": "record_job",
    "description": (
        "Record one rated job posting so it gets saved to the tracker. Call this "
        "once per posting you want tracked - do not batch multiple postings into "
        "one call."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": ["Accenture", "Product", "Watchlist"]},
            "title": {"type": "string"},
            "company_or_family": {
                "type": "string",
                "description": "Company name (Product/Watchlist) or role-family label (Accenture).",
            },
            "location": {"type": "string"},
            "posted_date": {"type": "string", "description": "YYYY-MM-DD"},
            "experience_req": {"type": "string"},
            "fit_score": {"type": "integer", "minimum": 1, "maximum": 5},
            "fit_label": {"type": "string", "enum": ["Excellent", "Strong", "Moderate", "Weak", "Skip"]},
            "jd_reviewed": {"type": "boolean"},
            "why": {"type": "string", "description": "One-to-two sentence rationale grounded in the JD."},
            "apply_url": {"type": "string"},
        },
        "required": [
            "source", "title", "company_or_family", "location", "posted_date",
            "experience_req", "fit_score", "fit_label", "jd_reviewed", "why", "apply_url",
        ],
        "additionalProperties": False,
    },
}

RECORD_RESUME_TOOL = {
    "name": "record_resume",
    "description": (
        "Save a tailored resume for a specific posting you already recorded with "
        "record_job (matched by its apply_url)."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "apply_url": {"type": "string"},
            "content_text": {
                "type": "string",
                "description": (
                    "Plain text, short ALL-CAPS section headers (SUMMARY, EXPERIENCE, "
                    "SKILLS, ...). Under ~4000 characters."
                ),
            },
        },
        "required": ["apply_url", "content_text"],
        "additionalProperties": False,
    },
}

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 40}
WEB_FETCH_TOOL = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 40}


def _build_prompt(existing: dict, watchlist: list, base_resume: str, cutoff: str) -> str:
    existing_lines = []
    for source, jobs in existing.items():
        existing_lines.append(f"{source} ({len(jobs)} already tracked):")
        for j in jobs:
            existing_lines.append(
                f"  - {j.get('title','')} | {j.get('company_or_family','')} | "
                f"{j.get('location','')} | {j.get('posted_date','')} | {j.get('apply_url','')}"
            )
    existing_block = "\n".join(existing_lines) if existing_lines else "(nothing tracked yet)"

    watchlist_block = "\n".join(
        f"- {w['company_name']}: {w['career_url']}" + (f" ({w['notes']})" if w.get("notes") else "")
        for w in watchlist
    ) or "(no watchlist companies added yet)"

    base_resume_note = (
        "A base resume IS on file - fetch it is not needed, it's included in full below."
        if base_resume else
        "No base resume is on file yet - skip resume tailoring entirely (record_job jobs "
        "as usual, just never call record_resume) and say so in your final summary."
    )

    return f"""\
You are running the job-pull pipeline for Ankit Nayak's Job Tracker. This is a
single self-contained run - you have no memory of previous runs beyond what's
listed below as already tracked.

## Who this is for
{WHO_THIS_IS_FOR}

## Scope of roles
Three role families, across {', '.join(LOCATIONS)} only:
{chr(10).join(f'{i+1}. {r}' for i, r in enumerate(ROLE_FAMILIES))}

Three sources, use the matching value in record_job's `source` field:
- "Accenture" - all open reqs matching the families/locations above.
- "Product" - genuine product-based companies only. Excludes IT-services/
  consulting/staffing firms (Accenture, TCS, Infosys, Wipro, Cognizant,
  Capgemini, HCL, LTIMindtree, Persistent, EPAM, Deloitte, etc.) and pure
  banks/insurers with no core software product. Healthcare-tech/payer
  platforms that build software (UnitedHealth/Optum, Zelis, Cohere Health)
  DO count; plain insurers/banks (Synchrony, Citi, Barclays) do not.
- "Watchlist" - Ankit's own hand-picked companies (list below). Location and
  role-family filters still apply - a watchlist company doesn't bypass
  scope, it just guarantees its career page gets checked this run.

## Watchlist companies to check this run
{watchlist_block}

For each one, use web_fetch on its career_url (it's already given above, so
web_fetch can use it directly). Some of these are JS-rendered single-page
apps (e.g. Workday boards) - web_fetch may only return an empty app shell for
those. If that happens, try web_search for "<company> careers <role> Bengaluru"
as a fallback to find individually indexed posting pages instead, and if that
also turns up nothing, skip the company for this run rather than guessing at
its listings.

## Hard rule: 60-day cutoff
Cutoff date is {cutoff}. Never call record_job for anything posted before
this date.

## Rating scale (1-5, apply to every posting)
- 5 Excellent - experience band matches, domain/skills transfer directly, no red flags.
- 4 Strong - close match with one minor gap.
- 3 Moderate - real overlap but a real gap (seniority reach, unproven specialism, ambiguous scope from title alone).
- 2 Weak - meaningful mismatch (domain, seniority, or hands-on-technical requirement well outside profile).
- 1 Skip - hard disqualifier.

Every rating needs a one-to-two-sentence `why` grounded in actual JD
language. Fetch the full JD (web_fetch) for anything promising or ambiguous
(mark jd_reviewed: true); rate the rest from title/company/summary alone
(jd_reviewed: false).

## Already tracked (skip these - do not re-search, re-rate, or re-record)
Match by apply_url (ignore query string) or by title+company+location+posted_date.
{existing_block}

## Procedure
1. Search per role family x per city for both Accenture-specific and
   generic/product-company postings, using web_search. Work through the
   watchlist per the section above.
2. For everything new and within the cutoff, rate it and call record_job.
   Skip anything that duplicates an already-tracked posting above.
3. {base_resume_note}
   For every posting you just recorded with fit_score >= 4 and source in
   Product or Watchlist: tailor the base resume for that specific JD -
   reorder/emphasize relevant experience, mirror the JD's terminology where
   truthful, never invent employers, titles, dates, or metrics not in the
   base resume or the JD. Call record_resume with the same apply_url you
   used in record_job for that posting.
4. When you've covered the scope, stop (do not keep searching indefinitely -
   a thorough single pass is the goal, not exhaustive coverage of every
   possible query variant). End your final message with a short plain-text
   summary: counts of jobs found per source, resumes tailored, and anything
   that needs Ankit's attention (e.g. a watchlist company whose page
   structure defeated both web_fetch and web_search).

{"## Base resume" + chr(10) + base_resume if base_resume else ""}
"""


async def run_pipeline_once() -> dict:
    """Runs one full pass: search, rate, tailor, write to Mongo. Returns a
    summary dict. Raises on unrecoverable errors (caller logs a failed run)."""
    client = anthropic.AsyncAnthropic()

    cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    existing = {
        source: await db.list_job_summaries(source)
        for source in ("Accenture", "Product", "Watchlist")
    }
    watchlist = await db.list_watchlist()
    base_resume = await db.get_base_resume_text()

    prompt = _build_prompt(existing, watchlist, base_resume, cutoff)

    tools = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL, RECORD_JOB_TOOL, RECORD_RESUME_TOOL]
    messages = [{"role": "user", "content": prompt}]

    collected_jobs = []
    collected_resumes = []
    final_summary = ""

    for _ in range(MAX_LOOP_ITERATIONS):
        response = await client.messages.create(
            model=MODEL,
            max_tokens=16000,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "pause_turn":
            continue  # server-tool turn paused mid-way; resend history to continue

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "record_job":
                    collected_jobs.append(dict(block.input))
                    result = "recorded"
                elif block.name == "record_resume":
                    collected_resumes.append(dict(block.input))
                    result = "saved"
                else:
                    result = f"error: unknown tool {block.name}"
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id, "content": result,
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        # end_turn (or max_tokens/refusal) - capture the final text and stop
        final_summary = next((b.text for b in response.content if b.type == "text"), "")
        break
    else:
        final_summary = f"(stopped after hitting the {MAX_LOOP_ITERATIONS}-turn safety cap)"

    inserted = updated = 0
    for job in collected_jobs:
        outcome = await db.upsert_one_job(job)
        if outcome == "inserted":
            inserted += 1
        else:
            updated += 1

    resumes_saved = 0
    for resume in collected_resumes:
        job_id = await db.find_job_id_by_apply_url(resume["apply_url"])
        if not job_id:
            continue  # couldn't match to a recorded job; skip rather than guess
        await db.save_resume(job_id, resume["content_text"])
        resumes_saved += 1

    notes = final_summary[:2000]
    await db.log_run(
        run_date=date.today().isoformat(),
        jobs_added=inserted,
        jobs_reviewed=inserted + updated,
        resumes_generated=resumes_saved,
        notes=notes,
    )

    return {
        "jobs_inserted": inserted,
        "jobs_updated": updated,
        "resumes_saved": resumes_saved,
        "summary": final_summary,
    }

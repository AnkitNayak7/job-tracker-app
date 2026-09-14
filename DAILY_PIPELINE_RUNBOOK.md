# Job-pull pipeline

**This used to document a scheduled Claude Code cloud-routine prompt. That
approach was retired** — the cloud routine's sandbox blocks outbound network
access to arbitrary hosts (including this app's own Railway domain) for
both `curl` and `WebFetch`, so a routine could never actually reach this
backend. The pipeline now runs as backend code instead: see
[`backend/pipeline.py`](backend/pipeline.py).

## How it works now

`pipeline.run_pipeline_once()` calls the Claude API directly (the
`anthropic` Python SDK, model `claude-opus-5` by default) with:
- the `web_search` and `web_fetch` server tools, for open-ended job search
  and fetching specific postings/career pages,
- two custom tools, `record_job` and `record_resume`, that Claude calls
  once per rated posting / tailored resume it wants saved — the backend
  intercepts these tool calls and writes straight to MongoDB (no HTTP hop).

It's triggered two ways:
- **Automatically**, once a day around 7am IST, by a background asyncio
  task started at app startup (`main.py`'s `_daily_scheduler`).
- **On demand**, via the dashboard's "Run pipeline now" button
  (`POST /api/pipeline/run_now`), which launches the same function as a
  background task and returns immediately — the dashboard polls
  `GET /api/pipeline/status` and refreshes once it finishes.

An `asyncio.Lock` (`main.py`'s `_pipeline_lock`) ensures only one run
happens at a time regardless of which trigger fired it.

## Configuration

- `ANTHROPIC_API_KEY` — required. Billed API usage, separate from any
  Claude subscription — a run does several web searches plus rating/
  tailoring reasoning. Get one at console.anthropic.com; the org's credit
  balance must be funded or every run fails with a 400
  `credit balance too low` error (visible in `/api/pipeline/status` and the
  Run Log tab).
- `PIPELINE_MODEL` — optional override (defaults to `claude-opus-5`).
  `claude-sonnet-5` is a materially cheaper alternative if cost matters
  more than top-end judgment quality for this task.

## Who this is for / scope of roles / rating scale / watchlist

Unchanged from the original design — see the constants and prompt-building
in `backend/pipeline.py` (`WHO_THIS_IS_FOR`, `ROLE_FAMILIES`, `LOCATIONS`,
and `_build_prompt`) for the exact current wording, since that file is now
the single source of truth instead of this doc.

Ankit manages the watchlist himself from the dashboard's "Manage Watchlist"
tab (`GET/POST/PUT/DELETE /api/watchlist`, cookie-auth); the pipeline reads
it directly via `db.list_watchlist()`.

## Known limitation

`web_fetch` fetches whatever HTML a page returns — it does not run
JavaScript. Career pages that are pure client-rendered SPAs (e.g. some
Workday boards, including Light & Wonder's) may come back as an empty app
shell. The pipeline's prompt tells it to fall back to `web_search` for
individually-indexed posting pages when that happens, and to skip the
company for that run (noting it in the run log) rather than guess at
content it can't see. A true headless-browser render (Playwright + Chromium
in the container) would fix this fully but adds meaningful image size,
memory, and build complexity — a deliberate scope cut, not an oversight;
revisit if a specific watchlist company's postings keep getting missed.

## Notes for whoever edits this later

- `apply_url` is the dedupe key when present (query string stripped);
  fall back to title+company+location+posted_date only when a posting has
  no URL. See `db.make_dedupe_key`.
- The web dashboard reads `GET /api/jobs` (cookie-authenticated), which
  applies the 60-day filter automatically at read time (unless
  `applied: true`, always shown so the Follow-up tab keeps full history) —
  so the pipeline does not need to prune/delete old jobs itself, only avoid
  re-adding or re-reviewing them.
- The internal HTTP endpoints under `/api/internal/...` (API-key auth)
  still exist for external/manual inspection and are unchanged in shape,
  but the in-process pipeline no longer calls itself over HTTP — it uses
  the `db.py` functions directly.

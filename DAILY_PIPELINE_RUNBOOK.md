# Job-pull pipeline — runbook / scheduled-task prompt

This is the exact procedure a fresh Claude session should run to refresh
Ankit Nayak's Job Tracker. The routine wakes up **hourly** (the platform's
minimum interval) but only does a full run when told to — see step 0. Every
firing is self-contained — it must not assume any memory of previous runs
beyond what's stored in MongoDB via the API below.

Placeholders to fill in after deployment:
- `{{API_BASE_URL}}` — the Railway public URL, e.g. `https://backend-production-ecca8.up.railway.app`
- `{{API_KEY}}` — the `INTERNAL_API_KEY` value set on the Railway service

## Transport

Try `curl {{API_BASE_URL}}/health` via `Bash` first each run. If `Bash` can
reach the backend directly, use the normal POST endpoints
(`/api/internal/jobs/upsert`, `/api/internal/resumes`, `/api/internal/runs`,
`/api/internal/config/base_resume`) with header `X-API-Key: {{API_KEY}}`.
If `Bash` cannot reach it (egress to `*.up.railway.app` blocked), fall back
to the **GET-mirror endpoints** (`/api/internal/jobs/upsert_one`,
`/api/internal/resumes/save`, `/api/internal/runs/log`,
`/api/internal/config/base_resume/set`) called via the `WebFetch` tool,
with the API key and payload passed as URL query parameters
(`api_key=...`), built with `Bash`/Python URL-encoding first (ask WebFetch
to "return the raw response body verbatim" so you get exact JSON back).

## Step 0 — should this wake-up do a full run?

Every hourly fire, first check:
`GET {{API_BASE_URL}}/api/internal/pipeline/should_run?api_key={{API_KEY}}`
→ `{"should_run": bool, "manual_triggered": bool, "scheduled": bool}`.

- `should_run: false` → **stop immediately**, do nothing else. This keeps
  off-schedule hourly wake-ups essentially free.
- `should_run: true` → proceed with the full run below. This happens either
  because it's the scheduled hour (~7am IST) or because Ankit clicked
  "Run pipeline now" on the dashboard (`manual_triggered: true`).

At the very end of a full run (step 7), always call
`GET {{API_BASE_URL}}/api/internal/pipeline/ack?api_key={{API_KEY}}` — this
clears the manual-run flag and records `last_run_at`, so a manual click
doesn't cause a second run on the next hourly wake-up.

## Who this is for

Ankit Nayak — Technical Program Manager (8.9+ yrs) at Rockwell Automation,
Bengaluru. Enterprise data platforms, IT-OT integration, GenAI/RAG systems,
PMP-certified, GxP/21 CFR Part 11 domain experience (pharma: Pfizer, Amgen;
manufacturing: Aarti Industries, Exide, Grasim; energy). AWS (IoT Hub,
Lambda, Step Functions, Redshift), Kafka, Databricks, Agile/SAFe delivery.

## Scope of roles

Three role families, across **Bengaluru, Hyderabad, Pune** only:
1. TPM / Program Manager
2. AI & GenAI Product Manager
3. Delivery / Engagement Manager

Three sources, tagged in the `source` field:
- `"Accenture"` — all open reqs matching the families/locations above.
- `"Product"` — genuine product-based companies only. Excludes
  IT-services/consulting/staffing firms (Accenture, TCS, Infosys, Wipro,
  Cognizant, Capgemini, HCL, LTIMindtree, Persistent, EPAM, Deloitte, etc.)
  and pure banks/insurers with no core software product. Healthcare-tech /
  payer platforms that build software (UnitedHealth/Optum, Zelis, Cohere
  Health) DO count; plain insurers/banks (Synchrony, Citi, Barclays) do not.
- `"Watchlist"` — Ankit's own hand-picked companies (see below). Location
  and role-family filters still apply here — a watchlist company doesn't
  bypass scope, it just guarantees its career page gets checked every run.

## Watchlist — user-submitted companies/career pages

`GET {{API_BASE_URL}}/api/internal/watchlist?api_key={{API_KEY}}` returns
`[{"id", "company_name", "career_url", "notes"}, ...]` — companies Ankit
added from the dashboard. For every entry:

1. Open `career_url`. Try `WebFetch` first. Many career pages (Workday,
   Greenhouse SPA views, etc.) render nothing useful to WebFetch because
   the listing is built client-side with JS — if the fetched content looks
   like an empty app shell (no job titles/links in the text), fall back to
   the JS-rendering browser step below.
2. **JS-rendering fallback (Playwright via Bash):** if not already
   installed this session, run
   `pip install --quiet playwright && playwright install --with-deps chromium`
   via `Bash`, then drive it with a short Python script (sync API) that:
   navigates to `career_url`, waits for network-idle / the job-list
   selector to appear, accepts any cookie-consent banner, and extracts job
   titles + links (and, for a Workday-style board, applies the
   Bengaluru/Hyderabad/Pune location filter in the UI first if one
   exists). Print the extracted listing as JSON/text so the rest of the
   run can consume it.
3. Rate and file matches the same way as any other posting, tagged
   `"source": "Watchlist"`, `"company_or_family": <company_name>`.
4. If a watchlist URL 404s, times out, or its structure defeats both
   WebFetch and the Playwright script, skip it for this run and mention it
   in the run log's `notes` (don't guess at content, and don't retry
   endlessly within one run).

This same Playwright approach is also how to handle any other JS-heavy
source you encounter (nothing is hardcoded as "browser-tool-only" anymore
— try WebFetch, fall back to Playwright whenever the page is a JS SPA).

## Hard rule: 60-day cutoff

Never fetch details on, rate, or upsert a posting older than 60 days from
today. Compute `cutoff = today - 60 days` at the start of the run and skip
anything posted before it.

## Rating scale (1–5, apply to every posting)

- **5 Excellent** — experience band matches, domain/skills transfer directly, no red flags.
- **4 Strong** — close match with one minor gap.
- **3 Moderate** — real overlap but a real gap (seniority reach, unproven specialism, ambiguous scope from title alone).
- **2 Weak** — meaningful mismatch (domain, seniority, or hands-on-technical requirement well outside profile).
- **1 Skip** — hard disqualifier.

Every rating needs a one-to-two-sentence `why` grounded in actual JD
language. Fetch the full JD for anything promising or ambiguous (mark
`jd_reviewed: true`); rate the rest from title/company/summary alone
(`jd_reviewed: false`).

## Step-by-step procedure

1. **Load what's already tracked** (to dedupe and to avoid re-rating), via
   `WebFetch`:
   `{{API_BASE_URL}}/api/internal/jobs?source=Accenture&api_key={{API_KEY}}`,
   and the same with `source=Product` and `source=Watchlist`. Each entry
   has `apply_url` and `posted_date` — skip anything you'd otherwise fetch
   that matches an existing `apply_url` (stripped of query string) or an
   existing title+company+location+posted_date combo. Ask WebFetch to
   "return the raw response body verbatim" so you get exact JSON back, not
   a summary.

2. **Search.** Run job searches per role family × per city for both
   Accenture-specific and generic/product-company queries (e.g. Indeed
   search tools, or whatever job-search tool this session has). Also work
   through every watchlist entry per the Watchlist section above. Drop
   anything past the 60-day cutoff immediately.

3. **Fetch & rate new postings.** For everything not already tracked and
   within the cutoff: fetch the full JD for promising/ambiguous ones, rate
   1–5 per the scale above, write a one-line `why`.

4. **Tailor a resume for high-fit product-company or watchlist roles.**
   For every posting with `fit_score >= 4` and `source` in `Product` or
   `Watchlist`:
   a. `WebFetch` `{{API_BASE_URL}}/api/internal/config/base_resume?api_key={{API_KEY}}`
      to get the current base resume text.
   b. If it's empty, skip resume generation for this run and note it in
      the run log — Ankit hasn't uploaded a base resume yet.
   c. Otherwise, rewrite/tailor that resume for this specific JD: reorder
      and emphasize the most relevant experience, mirror the JD's own
      terminology where truthful, keep every fact grounded in the base
      resume (never invent employers, titles, dates, or metrics that
      aren't in the base resume or the JD match). Keep it one page's
      worth of content, plain text with short ALL-CAPS section headers
      (e.g. `SUMMARY`, `EXPERIENCE`, `SKILLS`) — the backend renders that
      into a formatted .docx automatically. Keep it under ~3000 characters
      so it fits comfortably in a URL query parameter (step 5).

5. **Push results**, one job/resume/run per call — build each URL with
   `Bash` (e.g. Python `urllib.parse.quote` on the JSON/text value), then
   fetch the finished URL with `WebFetch` ("return the raw response body
   verbatim"):
   - Upsert every new/updated posting (existing postings you didn't
     re-review don't need to be resent), one at a time:
     `{{API_BASE_URL}}/api/internal/jobs/upsert_one?api_key={{API_KEY}}&job=<url-encoded JSON>`
     where the JSON object is:
     ```json
     {
       "source": "Accenture" | "Product" | "Watchlist",
       "title": "...", "company_or_family": "...", "location": "...",
       "posted_date": "YYYY-MM-DD", "experience_req": "...",
       "fit_score": 1-5, "fit_label": "Excellent|Strong|Moderate|Weak|Skip",
       "jd_reviewed": true|false, "why": "...", "apply_url": "..."
     }
     ```
     (Applied-status is preserved automatically server-side — never send
     an `applied` field.) The response is `{"inserted": 1}` or `{"updated": 1}`.
   - For each tailored resume, first re-fetch the job list to get its
     Mongo `id` (the upsert response doesn't return ids — call
     `.../api/internal/jobs?source=...&api_key=...` again and match by
     `apply_url`), then:
     `{{API_BASE_URL}}/api/internal/resumes/save?api_key={{API_KEY}}&job_id=<id>&content_text=<url-encoded tailored resume text>`.

6. **Log the run.**
   `{{API_BASE_URL}}/api/internal/runs/log?api_key={{API_KEY}}&run_date=YYYY-MM-DD&jobs_added=N&jobs_reviewed=N&resumes_generated=N&notes=<url-encoded short summary>`
   — mention any watchlist entries that failed to load in `notes`.

7. **Acknowledge the run** (clears the manual-trigger flag):
   `GET {{API_BASE_URL}}/api/internal/pipeline/ack?api_key={{API_KEY}}`.

8. Do not message the user unless something needs their attention (e.g.
   base resume missing, a source became unreachable, or a watchlist
   company's page structure changed and needs a human look). Routine
   successful runs are silent — the user checks the dashboard when they
   want to.

## Notes for whoever (human or Claude) edits this pipeline later

- The web dashboard reads `GET /api/jobs` (cookie-authenticated) which
  applies the same 60-day filter automatically at read time (unless
  `applied: true`, which is always shown so the Follow-up tab keeps full
  history) — so the pipeline does not need to prune/delete old jobs
  itself, only avoid re-adding or re-reviewing them.
- `apply_url` is the dedupe key when present (query string stripped);
  fall back to title+company+location+posted_date only when a posting has
  no URL.
- Ankit manages the watchlist himself from the dashboard's "Manage
  Watchlist" tab (`GET/POST/PUT/DELETE /api/watchlist`, cookie-auth) — the
  pipeline only ever reads it via the internal endpoint above.
- The hourly should_run/ack pair exists purely so the dashboard's "Run
  pipeline now" button can get a run going within the hour instead of
  waiting for the next scheduled slot. If per-hour wake-up cost ever
  becomes a concern, the schedule can revert to a single daily fire and
  the button removed/disabled — should_run degrades gracefully either way
  since it just checks a Mongo flag.

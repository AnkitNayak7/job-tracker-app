# Daily job-pull pipeline — runbook / scheduled-task prompt

This is the exact procedure a fresh Claude session should run once a day to
refresh Ankit Nayak's Job Tracker. Once the app is deployed, this text
(with the placeholders filled in) becomes the `prompt` of a daily scheduled
task (`create_trigger`), so every firing is self-contained — it must not
assume any memory of previous runs beyond what's stored in MongoDB via the
API below.

Placeholders to fill in after deployment:
- `{{API_BASE_URL}}` — the Railway public URL, e.g. `https://backend-production-ecca8.up.railway.app`
- `{{API_KEY}}` — the `INTERNAL_API_KEY` value set on the Railway service

## IMPORTANT — transport constraint discovered during setup

This sandboxed environment's outbound network (the `Bash` tool's egress)
blocks arbitrary HTTPS hosts including Railway's own app domains
(`*.up.railway.app`) by organization policy — `curl`/`requests`/any direct
HTTP client called from `Bash` cannot reach the backend. The **`WebFetch`
tool can** reach it (it runs through a separate, unrestricted path) but it
only issues GET requests and cannot send a custom header or a request
body. Because of this, every internal write in this runbook uses the
**GET-based mirror endpoints** (`/api/internal/.../*_get`, `/upsert_one`,
`/save`, `/log`, `/set`) with the API key and payload passed as URL query
parameters, called via `WebFetch` — never `Bash`/`curl` for the actual
HTTP call. Use `Bash` only to build the encoded URL string (e.g. with
Python's `urllib.parse.quote`), then hand that finished URL to `WebFetch`.
If a future session finds `Bash` can reach `{{API_BASE_URL}}` directly
(the policy may differ or change), the original POST endpoints
(`/api/internal/jobs/upsert`, `/api/internal/resumes`, `/api/internal/runs`,
`/api/internal/config/base_resume`) are simpler and fine to use instead —
try a quick `curl {{API_BASE_URL}}/health` from Bash first each run to see
which path is available.

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

Two sources, tagged in the `source` field:
- `"Accenture"` — all open reqs matching the families/locations above.
- `"Product"` — genuine product-based companies only. Excludes
  IT-services/consulting/staffing firms (Accenture, TCS, Infosys, Wipro,
  Cognizant, Capgemini, HCL, LTIMindtree, Persistent, EPAM, Deloitte, etc.)
  and pure banks/insurers with no core software product. Healthcare-tech /
  payer platforms that build software (UnitedHealth/Optum, Zelis, Cohere
  Health) DO count; plain insurers/banks (Synchrony, Citi, Barclays) do not.
- Standing extra source to check every run: **Light & Wonder's careers
  page** (Workday board, Bengaluru filter) — a gaming/casino-tech product
  company. It's a JS SPA; use a browser tool (not WebFetch), accept the
  cookie banner, then read the job list.

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
   `{{API_BASE_URL}}/api/internal/jobs?source=Accenture&api_key={{API_KEY}}`
   and the same with `source=Product`. Each entry has `apply_url` and
   `posted_date` — skip anything you'd otherwise fetch that matches an
   existing `apply_url` (stripped of query string) or an existing
   title+company+location+posted_date combo. Ask WebFetch to "return the
   raw response body verbatim" so you get exact JSON back, not a summary.

2. **Search.** Run job searches per role family × per city for both
   Accenture-specific and generic/product-company queries (e.g. Indeed
   search tools, or whatever job-search tool this session has). Also open
   the Light & Wonder Workday board via a browser tool and read its
   current listing. Drop anything past the 60-day cutoff immediately.

3. **Fetch & rate new postings.** For everything not already tracked and
   within the cutoff: fetch the full JD for promising/ambiguous ones, rate
   1–5 per the scale above, write a one-line `why`.

4. **Tailor a resume for high-fit product-company roles.** For every
   posting with `fit_score >= 4`:
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
       "source": "Accenture" | "Product",
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
   `{{API_BASE_URL}}/api/internal/runs/log?api_key={{API_KEY}}&run_date=YYYY-MM-DD&jobs_added=N&jobs_reviewed=N&resumes_generated=N&notes=<url-encoded short summary>`.

7. Do not message the user unless something needs their attention (e.g.
   base resume missing, a source became unreachable, or the Light &
   Wonder board structure changed and needs a human look). Routine
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

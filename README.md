# Job Tracker — web app

A password-protected dashboard (JS frontend + Python/FastAPI backend +
MongoDB) that replaces the Excel tracker. A pipeline built into the backend
calls the Claude API directly (once a day automatically, or on demand from
the dashboard) to pull new job postings — including any companies you add
to the watchlist — rate them against Ankit's profile, tailor a resume for
the strongest matches, and write everything into MongoDB. The site displays
what's in the database and lets you mark roles as Applied — which then also
shows up on the Follow-up tab.

## What's in this folder

```
backend/        FastAPI app (API + serves the frontend)
  main.py         all routes + the daily scheduler
  pipeline.py     the job-pull pipeline (calls the Claude API directly)
  db.py           MongoDB access + schema notes
  auth.py         password-cookie auth (dashboard) + API-key auth (internal endpoints)
  requirements.txt
frontend/       Plain HTML/CSS/JS dashboard, no build step
  index.html, app.js, style.css
Dockerfile      Single-container build (backend serves frontend as static files)
DAILY_PIPELINE_RUNBOOK.md   How the pipeline works, its prompt content, and
                             known limitations (see backend/pipeline.py for
                             the actual source of truth).
```

## Why deployment needs one manual step from you

Railway can deploy this two ways: from a GitHub repo, or from a Docker
image already sitting in a registry (Docker Hub, GHCR, etc). The sandbox
this was built in can reach Railway's *management API* (so Claude can spin
up services, set env vars, generate a domain, etc.) but its outbound
network is blocked from Railway's own CLI/deploy endpoints and from Docker
Hub — an org egress policy, not something Claude can work around. So the
one thing that needs to happen outside this chat is getting this code into
a GitHub repo Claude can point Railway at.

### Step 1 — push this code to a new GitHub repo (you do this, ~2 minutes)

1. Create a new **empty** repo on GitHub (no README/license, so there's no merge conflict) — e.g. `job-tracker-app`, public or private, your call.
2. In a terminal, from this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial job tracker app"
   git branch -M main
   git remote add origin https://github.com/<your-username>/job-tracker-app.git
   git push -u origin main
   ```
3. Tell Claude the repo in `owner/name` form (e.g. `ankitnayak7/job-tracker-app`).

From there Claude takes over: creates the Railway project, a MongoDB
service, the backend service pointed at your repo, sets the password and
API key, generates a public URL, and sets up the daily scheduled pull.

### Step 2 — give Claude your base resume

Attach your current resume (.docx or .pdf) in the chat. It becomes the
template the daily pipeline tailors per job for anything rated 4 or 5 in
the Product Companies list.

## Local development (optional)

Requires a MongoDB reachable at `MONGO_URL` (a free MongoDB Atlas cluster
works fine, or a local `mongod`/`mongo` container on a machine that isn't
network-restricted).

```bash
cd backend
pip install -r requirements.txt
export MONGO_URL="mongodb://localhost:27017"
export SITE_PASSWORD="pick-something"
export SESSION_SECRET="pick-something-random"
export INTERNAL_API_KEY="pick-something-random"
export ANTHROPIC_API_KEY="sk-ant-..."  # required for the pipeline to run; app works without it otherwise
uvicorn main:app --reload --port 8080
```

Then open `http://localhost:8080`.

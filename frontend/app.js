const state = {
  tab: "accenture",
  minFit: 3,
  appliedOnly: false,
};

const el = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    showLogin();
    throw new Error("unauthenticated");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function showLogin() {
  el("login-view").classList.remove("hidden");
  el("app-view").classList.add("hidden");
}

function showApp() {
  el("login-view").classList.add("hidden");
  el("app-view").classList.remove("hidden");
  loadSummary();
  loadPipelineStatus();
  loadTab();
}

async function init() {
  try {
    await api("/api/me");
    showApp();
  } catch (e) {
    showLogin();
  }
}

el("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = el("login-password").value;
  el("login-error").classList.add("hidden");
  try {
    await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }).then((r) => {
      if (!r.ok) throw new Error("bad password");
      return r.json();
    });
    showApp();
  } catch (err) {
    el("login-error").textContent = "Wrong password.";
    el("login-error").classList.remove("hidden");
  }
});

el("logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin();
});

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.tab = btn.dataset.tab;
    const showFilters = state.tab === "accenture" || state.tab === "product" || state.tab === "watchlist-jobs";
    document.querySelector(".filters").style.display = showFilters ? "flex" : "none";
    el("watchlist-form-wrap").classList.toggle("hidden", state.tab !== "watchlist");
    loadTab();
  });
});

let pipelinePollTimer = null;

el("run-now-btn").addEventListener("click", async () => {
  el("run-now-btn").disabled = true;
  try {
    await api("/api/pipeline/run_now", { method: "POST" });
  } finally {
    await loadPipelineStatus();
  }
});

async function loadPipelineStatus() {
  const s = await api("/api/pipeline/status");
  const statusEl = el("run-now-status");
  el("run-now-btn").disabled = s.running;

  if (s.running) {
    statusEl.textContent = "Running — this can take a few minutes (live search + rating)…";
    if (!pipelinePollTimer) {
      pipelinePollTimer = setInterval(async () => {
        const cur = await api("/api/pipeline/status");
        if (!cur.running) {
          clearInterval(pipelinePollTimer);
          pipelinePollTimer = null;
          await loadPipelineStatus();
          loadSummary();
          loadTab();
        }
      }, 8000);
    }
  } else if (s.last_error) {
    statusEl.textContent = `Last run failed: ${s.last_error}`;
  } else if (s.last_run_at) {
    const r = s.last_result || {};
    const parts = [];
    if (r.jobs_inserted != null) parts.push(`${r.jobs_inserted} new`);
    if (r.jobs_updated != null) parts.push(`${r.jobs_updated} updated`);
    if (r.resumes_saved != null) parts.push(`${r.resumes_saved} resumes`);
    const detail = parts.length ? ` (${parts.join(", ")})` : "";
    statusEl.textContent = `Last run: ${new Date(s.last_run_at).toLocaleString()}${detail}`;
  } else {
    statusEl.textContent = "";
  }
}

function watchlistRow(item) {
  return `
    <tr>
      <td>${escapeHtml(item.company_name)}</td>
      <td><a class="apply-link" href="${item.career_url}" target="_blank" rel="noopener">${escapeHtml(item.career_url)}</a></td>
      <td class="why">${escapeHtml(item.notes || "")}</td>
      <td class="watchlist-actions">
        <button type="button" class="ghost small edit-watchlist-btn" data-id="${item.id}">Edit</button>
        <button type="button" class="ghost small delete-watchlist-btn" data-id="${item.id}">Delete</button>
      </td>
    </tr>`;
}

async function loadWatchlistManage() {
  const items = await api("/api/watchlist");
  if (!items.length) {
    el("table-wrap").innerHTML = `<div class="empty-state">No companies added yet — use the form above to add one.</div>`;
  } else {
    el("table-wrap").innerHTML = `
      <div class="table-scroll">
      <table>
        <thead><tr><th>Company</th><th>Career page</th><th>Notes</th><th></th></tr></thead>
        <tbody>${items.map(watchlistRow).join("")}</tbody>
      </table>
      </div>`;
  }
  document.querySelectorAll(".edit-watchlist-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = items.find((i) => i.id === btn.dataset.id);
      el("watchlist-edit-id").value = item.id;
      el("watchlist-company").value = item.company_name;
      el("watchlist-url").value = item.career_url;
      el("watchlist-notes").value = item.notes || "";
      el("watchlist-submit-btn").textContent = "Save changes";
      el("watchlist-cancel-btn").classList.remove("hidden");
      el("watchlist-company").focus();
    });
  });
  document.querySelectorAll(".delete-watchlist-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Remove this company from the watchlist?")) return;
      await api(`/api/watchlist/${btn.dataset.id}`, { method: "DELETE" });
      loadWatchlistManage();
    });
  });
}

function resetWatchlistForm() {
  el("watchlist-form").reset();
  el("watchlist-edit-id").value = "";
  el("watchlist-submit-btn").textContent = "Add company";
  el("watchlist-cancel-btn").classList.add("hidden");
}

el("watchlist-cancel-btn").addEventListener("click", resetWatchlistForm);

el("watchlist-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = JSON.stringify({
    company_name: el("watchlist-company").value.trim(),
    career_url: el("watchlist-url").value.trim(),
    notes: el("watchlist-notes").value.trim(),
  });
  const editId = el("watchlist-edit-id").value;
  if (editId) {
    await api(`/api/watchlist/${editId}`, { method: "PUT", body });
  } else {
    await api("/api/watchlist", { method: "POST", body });
  }
  resetWatchlistForm();
  loadWatchlistManage();
});

el("min-fit-filter").addEventListener("change", (e) => {
  state.minFit = Number(e.target.value);
  loadTab();
});
el("applied-only-filter").addEventListener("change", (e) => {
  state.appliedOnly = e.target.checked;
  loadTab();
});

async function loadSummary() {
  const s = await api("/api/summary");
  const cards = [
    ["Accenture open", s.Accenture?.total ?? 0],
    ["Accenture strong+", s.Accenture?.strong_or_excellent ?? 0],
    ["Product open", s.Product?.total ?? 0],
    ["Product strong+", s.Product?.strong_or_excellent ?? 0],
    ["Watchlist open", s.Watchlist?.total ?? 0],
    ["Watchlist strong+", s.Watchlist?.strong_or_excellent ?? 0],
    ["Applied (total)", s.applied_total ?? 0],
    ["Resumes tailored", s.resumes_generated ?? 0],
  ];
  el("summary-bar").innerHTML = cards
    .map(([label, num]) => `<div class="summary-card"><div class="num">${num}</div><div class="label">${label}</div></div>`)
    .join("");
}

function fitBadge(score, label) {
  return `<span class="fit-badge fit-${score}">${score} ${label || ""}</span>`;
}

function jobRow(job, showCompanyCol) {
  const resumeCell = job.resume_tailored
    ? `<a class="resume-link" href="/api/jobs/${job.id}/resume">Download</a>`
    : `<span class="muted">—</span>`;
  return `
    <tr>
      <td>${fitBadge(job.fit_score, job.fit_label)}</td>
      <td>${escapeHtml(job.title || "")}</td>
      <td>${escapeHtml(job.company_or_family || "")}</td>
      <td>${escapeHtml(job.location || "")}</td>
      <td>${escapeHtml(job.posted_date || "")}</td>
      <td>${escapeHtml(job.experience_req || "")}</td>
      <td>${job.jd_reviewed ? "Yes" : "Title only"}</td>
      <td class="why">${escapeHtml(job.why || "")}</td>
      <td><a class="apply-link" href="${job.apply_url || "#"}" target="_blank" rel="noopener">Apply ↗</a></td>
      <td>${resumeCell}</td>
      <td class="applied-toggle">
        <input type="checkbox" ${job.applied ? "checked" : ""} data-job-id="${job.id}" class="applied-checkbox" />
      </td>
    </tr>`;
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function renderJobsTable(jobs, opts = {}) {
  if (!jobs.length) {
    el("table-wrap").innerHTML = `<div class="empty-state">No jobs match these filters yet.</div>`;
    return;
  }
  el("table-wrap").innerHTML = `
    <div class="table-scroll">
    <table>
      <thead><tr>
        <th>Fit</th><th>Role</th><th>${opts.companyLabel || "Company"}</th><th>Location</th>
        <th>Posted</th><th>Experience</th><th>JD</th><th>Why</th><th>Link</th><th>Resume</th><th>Applied?</th>
      </tr></thead>
      <tbody>${jobs.map((j) => jobRow(j)).join("")}</tbody>
    </table>
    </div>`;
  document.querySelectorAll(".applied-checkbox").forEach((cb) => {
    cb.addEventListener("change", async (e) => {
      const jobId = e.target.dataset.jobId;
      await api(`/api/jobs/${jobId}`, {
        method: "PATCH",
        body: JSON.stringify({ applied: e.target.checked }),
      });
      loadSummary();
      if (state.tab === "followup") loadTab();
    });
  });
}

async function loadTab() {
  if (state.tab === "accenture" || state.tab === "product" || state.tab === "watchlist-jobs") {
    const source = state.tab === "accenture" ? "Accenture" : state.tab === "product" ? "Product" : "Watchlist";
    let jobs = await api(`/api/jobs?source=${source}`);
    jobs = jobs.filter((j) => j.fit_score >= state.minFit && (!state.appliedOnly || j.applied));
    renderJobsTable(jobs, { companyLabel: source === "Accenture" ? "Family" : "Company" });
  } else if (state.tab === "watchlist") {
    await loadWatchlistManage();
  } else if (state.tab === "followup") {
    const jobs = await api(`/api/jobs?applied=yes&include_stale=true`);
    renderJobsTable(jobs, { companyLabel: "Company / Family" });
  } else if (state.tab === "runs") {
    const runs = await api(`/api/runs`);
    if (!runs.length) {
      el("table-wrap").innerHTML = `<div class="empty-state">No pipeline runs logged yet.</div>`;
      return;
    }
    el("table-wrap").innerHTML = `
      <div class="table-scroll">
      <table>
        <thead><tr><th>Date</th><th>Jobs added</th><th>Jobs reviewed</th><th>Resumes generated</th><th>Notes</th></tr></thead>
        <tbody>${runs
          .map(
            (r) => `<tr><td>${escapeHtml(r.run_date)}</td><td>${r.jobs_added}</td><td>${r.jobs_reviewed}</td><td>${r.resumes_generated}</td><td>${escapeHtml(r.notes || "")}</td></tr>`
          )
          .join("")}</tbody>
      </table>
      </div>`;
  }
}

init();

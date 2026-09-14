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
    const showFilters = state.tab === "accenture" || state.tab === "product";
    document.querySelector(".filters").style.display = showFilters ? "flex" : "none";
    loadTab();
  });
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
  if (state.tab === "accenture" || state.tab === "product") {
    const source = state.tab === "accenture" ? "Accenture" : "Product";
    let jobs = await api(`/api/jobs?source=${source}`);
    jobs = jobs.filter((j) => j.fit_score >= state.minFit && (!state.appliedOnly || j.applied));
    renderJobsTable(jobs, { companyLabel: source === "Accenture" ? "Family" : "Company" });
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

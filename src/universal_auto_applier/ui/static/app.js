/* UniversalAutoApplier dashboard - Phase 6.
 * Supports: status, queue, interventions, review, logs views.
 * Vanilla JS, no framework. Local-first, no external calls.
 */

(() => {
  "use strict";

  const POLL_INTERVAL_MS = 10000;
  let pollTimer = null;

  // ---- View navigation ----
  const views = document.querySelectorAll(".uaa-view");
  const navLinks = document.querySelectorAll(".uaa-nav a");

  function showView(viewName) {
    views.forEach((v) => v.classList.remove("uaa-view-active"));
    navLinks.forEach((a) => a.removeAttribute("aria-current"));
    const view = document.getElementById("view-" + viewName);
    if (view) view.classList.add("uaa-view-active");
    const link = document.querySelector(`.uaa-nav a[data-view="${viewName}"]`);
    if (link) link.setAttribute("aria-current", "page");
  }

  navLinks.forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const viewName = link.getAttribute("data-view");
      showView(viewName);
      if (viewName === "queue") loadQueue();
      if (viewName === "interventions") loadInterventions();
      if (viewName === "logs") loadLogs();
    });
  });

  // ---- Helpers ----
  async function fetchJSON(url) {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    if (!resp.ok) throw new Error(`${url} returned ${resp.status}`);
    return resp.json();
  }

  async function postJSON(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function pillClassFor(state) {
    const map = {
      idle: "uaa-pill-idle",
      ready: "uaa-pill-ready",
      unavailable: "uaa-pill-unavailable",
      not_configured: "uaa-pill-not_configured",
      invalid: "uaa-pill-invalid",
      running: "uaa-pill-running",
      paused: "uaa-pill-paused",
    };
    return "uaa-pill " + (map[state] || "uaa-pill-unknown");
  }

  // ---- Dashboard / Status ----
  async function loadStatus() {
    try {
      const [status, health] = await Promise.all([
        fetchJSON("/api/status"),
        fetchJSON("/api/health"),
      ]);

      document.getElementById("run-status").textContent = status.run_status;
      document.getElementById("run-status").className = pillClassFor(status.run_status);
      document.getElementById("submit-mode").textContent = status.submit_mode;
      document.getElementById("jobs-total").textContent = status.jobs_total;
      document.getElementById("pending-interventions").textContent = status.pending_interventions;

      // Jobs by status
      const breakdown = document.getElementById("jobs-by-status");
      breakdown.innerHTML = "";
      if (status.jobs_by_status && Object.keys(status.jobs_by_status).length > 0) {
        for (const [s, count] of Object.entries(status.jobs_by_status)) {
          const div = document.createElement("div");
          div.className = "uaa-stat";
          div.innerHTML = `<span class="uaa-stat-label">${s}</span><span>${count}</span>`;
          breakdown.appendChild(div);
        }
      } else {
        breakdown.innerHTML = '<p class="uaa-empty">No jobs imported yet.</p>';
      }

      // Health components
      const compList = document.getElementById("component-list");
      compList.innerHTML = "";
      for (const c of health.components || []) {
        const li = document.createElement("li");
        li.innerHTML = `<span class="uaa-component-name">${c.name}</span><span class="${pillClassFor(c.state)}">${c.state}</span>`;
        compList.appendChild(li);
      }

      // Pipeline phase/action/error
      const phaseEl = document.getElementById("pipeline-phase");
      const actionEl = document.getElementById("pipeline-last-action");
      const errorEl = document.getElementById("pipeline-last-error");
      if (phaseEl) phaseEl.textContent = status.current_phase ? "Phase: " + status.current_phase : "";
      if (actionEl) actionEl.textContent = status.last_action ? "Action: " + status.last_action : "";
      if (errorEl) errorEl.textContent = status.last_error ? "Error: " + status.last_error : "";
    } catch (err) {
      console.error("[UAA] status load failed", err);
    }
  }

  // ---- Pipeline start ----
  document.getElementById("pipeline-start")?.addEventListener("click", async () => {
    const btn = document.getElementById("pipeline-start");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Running...";
    }
    try {
      const resp = await postJSON("/api/pipeline/start", {
        fixture_html: null,
        max_jobs: 10,
      });
      loadStatus();
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Start Dry-Run";
      }
      alert(
        "Pipeline " +
          resp.status +
          ". Processed: " +
          resp.jobs_processed +
          ", Succeeded: " +
          resp.jobs_succeeded +
          ", Failed: " +
          resp.jobs_failed +
          ". No real submissions occurred."
      );
    } catch (err) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Start Dry-Run";
      }
      alert("Pipeline start failed: " + err.message);
    }
  });

  // ---- Queue ----
  async function loadQueue() {
    try {
      const filter = document.getElementById("queue-status-filter").value;
      let url = "/api/queue?limit=100";
      if (filter) url += "&status=" + encodeURIComponent(filter);
      const data = await fetchJSON(url);

      const tbody = document.getElementById("queue-tbody");
      tbody.innerHTML = "";
      if (data.jobs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="uaa-empty">No jobs found.</td></tr>';
        return;
      }
      for (const job of data.jobs) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${esc(job.company)}</td>
          <td>${esc(job.title)}</td>
          <td>${esc(job.platform)}</td>
          <td><span class="${pillClassFor(job.status)}">${esc(job.status)}</span></td>
          <td>${job.score != null ? job.score.toFixed(1) : "—"}</td>
          <td>${fmtDate(job.last_updated_at)}</td>`;
        tbody.appendChild(tr);
      }
    } catch (err) {
      console.error("[UAA] queue load failed", err);
    }
  }

  document.getElementById("queue-refresh")?.addEventListener("click", loadQueue);
  document.getElementById("queue-status-filter")?.addEventListener("change", loadQueue);

  // ---- Interventions ----
  async function loadInterventions() {
    try {
      const data = await fetchJSON("/api/interventions?pending_only=true");
      const container = document.getElementById("intervention-list");
      container.innerHTML = "";

      if (data.interventions.length === 0) {
        container.innerHTML = '<p class="uaa-empty">No pending interventions.</p>';
      } else {
        for (const iv of data.interventions) {
          const card = document.createElement("div");
          card.className = "uaa-intervention-card";
          const llmMeta = iv.llm_metadata || {};
          const metaParts = [];
          if (llmMeta.category) metaParts.push("Category: " + esc(llmMeta.category));
          if (llmMeta.risk_level) metaParts.push("Risk: " + esc(llmMeta.risk_level));
          if (llmMeta.evidence_summary) metaParts.push("Evidence: " + esc(llmMeta.evidence_summary));
          if (llmMeta.unresolved_reason) metaParts.push("Reason: " + esc(llmMeta.unresolved_reason));
          if (llmMeta.field_token) metaParts.push("Token: " + esc(llmMeta.field_token));
          if (llmMeta.answer_source) metaParts.push("Source: " + esc(llmMeta.answer_source));
          const metaHtml = metaParts.length > 0
            ? '<div class="uaa-iv-llm-meta">' + metaParts.join("<br>") + "</div>"
            : "";
          const optionsHtml = (iv.options && iv.options.length > 0)
            ? '<p class="uaa-iv-options">Options: ' + iv.options.map(esc).join(", ") + "</p>"
            : "";
          card.innerHTML = `
            <div class="uaa-iv-header">
              <span class="uaa-iv-kind">${esc(iv.kind)}</span>
              <span class="uaa-pill ${iv.status === "pending" ? "uaa-pill-not_configured" : "uaa-pill-ready"}">${esc(iv.status)}</span>
            </div>
            <p class="uaa-iv-question">${esc(iv.question)}</p>
            ${optionsHtml}
            <p class="uaa-iv-meta">Job: ${esc(iv.application_id.substring(0, 12))}... · Confidence: ${iv.confidence != null ? iv.confidence : "—"}</p>
            ${iv.suggested_answer ? `<p class="uaa-iv-suggested">Suggested: <code>${esc(iv.suggested_answer)}</code></p>` : ""}
            ${metaHtml}
            <div class="uaa-iv-actions" data-iv-id="${esc(iv.intervention_id)}">
              <button class="uaa-btn uaa-btn-success" data-action="approve">Approve</button>
              <button class="uaa-btn" data-action="edit">Edit</button>
              <button class="uaa-btn" data-action="skip">Skip</button>
              <button class="uaa-btn uaa-btn-danger" data-action="block">Block</button>
            </div>
            <label class="uaa-iv-remember">
              <input type="checkbox" class="uaa-iv-remember-cb" checked> Remember answer
            </label>`;

          // Wire action buttons
          const actions = card.querySelectorAll(".uaa-iv-actions button");
          actions.forEach((btn) => {
            btn.addEventListener("click", () => {
              const rememberCb = card.querySelector(".uaa-iv-remember-cb");
              resolveIntervention(iv.intervention_id, btn.dataset.action, rememberCb ? rememberCb.checked : false);
            });
          });

          container.appendChild(card);
        }
      }
    } catch (err) {
      console.error("[UAA] interventions load failed", err);
    }
    await updateResumeVisibility();
  }

  async function resolveIntervention(ivId, action, rememberChecked) {
    const resolutionMap = {
      approve: "approved",
      edit: "edited",
      skip: "skipped",
      block: "blocked",
    };
    const resolution = resolutionMap[action];
    if (!resolution) return;

    let answer = null;
    let saveToMemory = false;
    if (action === "approve" || action === "edit") {
      answer = prompt("Enter the answer:");
      if (answer === null) return; // cancelled
      saveToMemory = rememberChecked;
    }

    try {
      await postJSON(`/api/interventions/${ivId}/resolve`, {
        resolution,
        answer: answer || undefined,
        save_to_memory: saveToMemory,
      });
      await loadInterventions();
      loadStatus();
    } catch (err) {
      alert("Failed to resolve: " + err.message);
    }
  }

  // ---- Resume / Retry ----
  document.getElementById("resume-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("resume-btn");
    const msg = document.getElementById("resume-msg");
    if (btn) btn.disabled = true;
    if (msg) msg.textContent = "Retrying application...";
    try {
      const data = await fetchJSON("/api/interventions?pending_only=false");
      const resolved = data.interventions.filter(iv => iv.status !== "pending");
      if (resolved.length === 0) {
        if (msg) msg.textContent = "No resolved interventions to resume.";
        if (btn) btn.disabled = false;
        return;
      }
      const appId = resolved[0].application_id;
      await postJSON(`/api/queue/${appId}/retry`, {});
      if (msg) msg.textContent = "Application re-queued. Starting pipeline...";
      try {
        await postJSON("/api/pipeline/start", { fixture_html: null, max_jobs: 1 });
        if (msg) msg.textContent = "Pipeline completed.";
      } catch (pipelineErr) {
        if (msg) msg.textContent = "Pipeline error: " + pipelineErr.message;
      }
      await loadStatus();
      await loadInterventions();
    } catch (err) {
      if (msg) msg.textContent = "Retry failed: " + err.message;
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  async function updateResumeVisibility() {
    const section = document.getElementById("resume-section");
    const btn = document.getElementById("resume-btn");
    if (!section || !btn) return;
    try {
      const pendingData = await fetchJSON("/api/interventions?pending_only=true");
      const allData = await fetchJSON("/api/interventions?pending_only=false");
      const hasResolved = allData.total > 0;
      const hasPending = pendingData.total > 0;
      if (hasResolved && !hasPending) {
        // All interventions resolved — Resume enabled.
        section.style.display = "block";
        btn.disabled = false;
      } else if (hasResolved && hasPending) {
        // Some pending — Resume visible but disabled.
        section.style.display = "block";
        btn.disabled = true;
      } else {
        section.style.display = "none";
      }
    } catch (err) {
      console.error("[UAA] updateResumeVisibility failed:", err);
      section.style.display = "none";
    }
  }

  // ---- Review ----
  document.getElementById("review-load")?.addEventListener("click", loadReviewState);

  async function loadReviewState() {
    const jobId = document.getElementById("review-job-id").value.trim();
    if (!jobId) return;

    try {
      const data = await fetchJSON(`/api/review/${encodeURIComponent(jobId)}`);
      const display = document.getElementById("review-state-display");
      const controls = document.getElementById("review-controls");

      display.innerHTML = `
        <div class="uaa-status-grid">
          <div class="uaa-stat"><span class="uaa-stat-label">Approved</span><span class="${data.approved ? "uaa-pill uaa-pill-ready" : "uaa-pill uaa-pill-idle"}">${data.approved ? "Yes" : "No"}</span></div>
          <div class="uaa-stat"><span class="uaa-stat-label">Can Submit</span><span class="${data.can_submit ? "uaa-pill uaa-pill-ready" : "uaa-pill uaa-pill-unavailable"}">${data.can_submit ? "Yes" : "No"}</span></div>
          <div class="uaa-stat"><span class="uaa-stat-label">Unresolved Interventions</span><span>${data.has_unresolved_interventions ? "Yes" : "No"}</span></div>
        </div>
        ${data.final_action_detected ? `<p><strong>Final action detected:</strong> ${esc(data.final_action_detected)}</p>` : ""}
        ${data.unanswered_fields && data.unanswered_fields.length > 0 ? `<p><strong>Unanswered fields:</strong> ${data.unanswered_fields.map(esc).join(", ")}</p>` : ""}
        ${data.documents && data.documents.length > 0 ? `<p><strong>Documents:</strong> ${data.documents.map(esc).join(", ")}</p>` : ""}
        <p class="uaa-safety-note">Note: Approving does NOT submit. It only sets the approval flag. Submission requires the pipeline orchestrator (Phase 8).</p>`;

      controls.style.display = "flex";
      updateSubmitCheck(jobId);
    } catch (err) {
      document.getElementById("review-state-display").innerHTML = `<p class="uaa-empty">Error: ${esc(err.message)}</p>`;
      controls.style.display = "none";
    }
  }

  async function updateSubmitCheck(jobId) {
    try {
      const data = await fetchJSON(`/api/review/${encodeURIComponent(jobId)}/submit-check`);
      const el = document.getElementById("submit-check-result");
      el.textContent = data.can_submit ? "Submit Allowed" : "Submit Blocked";
      el.className = data.can_submit ? "uaa-pill uaa-pill-ready" : "uaa-pill uaa-pill-unavailable";
    } catch {
      // ignore
    }
  }

  document.getElementById("review-approve")?.addEventListener("click", async () => {
    const jobId = document.getElementById("review-job-id").value.trim();
    if (!jobId) return;
    const approvalId = "manual-" + Date.now();
    try {
      await postJSON(`/api/review/${encodeURIComponent(jobId)}/approve`, { approval_id: approvalId });
      loadReviewState();
    } catch (err) {
      alert("Cannot approve: " + err.message);
    }
  });

  document.getElementById("review-deny")?.addEventListener("click", async () => {
    const jobId = document.getElementById("review-job-id").value.trim();
    if (!jobId) return;
    try {
      await postJSON(`/api/review/${encodeURIComponent(jobId)}/deny`);
      loadReviewState();
    } catch (err) {
      alert("Cannot deny: " + err.message);
    }
  });

  // ---- Logs ----
  async function loadLogs() {
    try {
      const [logs, errors] = await Promise.all([
        fetchJSON("/api/logs?limit=50"),
        fetchJSON("/api/errors?limit=50"),
      ]);

      renderLogList("log-list", logs.entries);
      renderLogList("error-list", errors.entries);
    } catch (err) {
      console.error("[UAA] logs load failed", err);
    }
  }

  function renderLogList(elementId, entries) {
    const el = document.getElementById(elementId);
    if (!entries || entries.length === 0) {
      el.innerHTML = '<p class="uaa-empty">No entries.</p>';
      return;
    }
    el.innerHTML = "";
    for (const entry of entries) {
      const div = document.createElement("div");
      div.className = "uaa-log-entry uaa-log-" + entry.level;
      div.innerHTML = `<span class="uaa-log-time">${fmtDate(entry.timestamp)}</span> <span class="uaa-log-level">${esc(entry.level)}</span> ${esc(entry.message)}`;
      el.appendChild(div);
    }
  }

  // ---- Controlled Submission View ----
  const submitLoadBtn = document.getElementById("submit-load");
  const submitApproveBtn = document.getElementById("submit-approve");
  const submitRevokeBtn = document.getElementById("submit-revoke");
  const submitExecuteBtn = document.getElementById("submit-execute");
  const submitConfirmYesBtn = document.getElementById("submit-confirm-yes");
  const submitConfirmNoBtn = document.getElementById("submit-confirm-no");
  let submitCurrentJobId = null;

  async function loadSubmitState() {
    const jobId = document.getElementById("submit-job-id").value.trim();
    if (!jobId) return;
    submitCurrentJobId = jobId;
    const display = document.getElementById("submit-state-display");
    const controls = document.getElementById("submit-controls");
    const gateStatus = document.getElementById("submit-gate-status");
    display.innerHTML = '<p class="uaa-empty">Loading...</p>';
    controls.style.display = "none";
    try {
      const resp = await fetch(`/api/submit/${jobId}/status`, {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      const rawData = await resp.json();
      // New API format: {snapshot: {...}}. Old format: flat.
      const data = rawData.snapshot || rawData;
      renderSubmitState(data);
    } catch (err) {
      display.innerHTML = `<p class="uaa-error">Error: ${esc(err.message)}</p>`;
    }
  }

  function renderSubmitState(data) {
    const display = document.getElementById("submit-state-display");
    const controls = document.getElementById("submit-controls");
    const gateStatus = document.getElementById("submit-gate-status");
    const executeBtn = document.getElementById("submit-execute");

    let html = "";
    html += `<div class="uaa-submit-field"><strong>Application ID:</strong> ${esc(data.application_id?.slice(0, 12) || "")}</div>`;
    html += `<div class="uaa-submit-field"><strong>Real submission enabled:</strong> ${data.enable_real_submission ? "YES" : "NO"}</div>`;
    html += `<div class="uaa-submit-field"><strong>Active approval:</strong> ${data.has_active_approval ? "Yes (ID: " + esc((data.approval_id || "").slice(0, 12)) + ")" : "No"}</div>`;
    if (data.snapshot_hash) {
      html += `<div class="uaa-submit-field"><strong>Snapshot hash:</strong> ${esc(data.snapshot_hash.slice(0, 12))}</div>`;
    }
    if (data.is_stale) {
      html += `<div class="uaa-submit-warning">⚠ Approval is STALE — form state has changed since approval. Re-approve the new snapshot.</div>`;
    }
    if (data.latest_result_state) {
      html += `<div class="uaa-submit-field"><strong>Latest result:</strong> ${esc(data.latest_result_state)}</div>`;
      html += `<div class="uaa-submit-field"><strong>Clicked:</strong> ${data.latest_result_clicked ? "Yes" : "No"}</div>`;
      if (data.latest_result_error) {
        html += `<div class="uaa-submit-error"><strong>Last error:</strong> ${esc(data.latest_result_error)}</div>`;
      }
    }
    html += `<div class="uaa-submit-field"><strong>Can submit:</strong> ${data.can_submit ? "YES" : "NO"}</div>`;
    if (data.gate_reason) {
      html += `<div class="uaa-submit-warning">Gate: ${esc(data.gate_reason)}</div>`;
    }
    display.innerHTML = html;
    controls.style.display = "block";

    // Enable/disable the Submit button based on gates.
    // The backend independently enforces all gates — this is advisory only.
    if (data.can_submit && data.has_active_approval) {
      executeBtn.disabled = false;
      gateStatus.textContent = "Gates passed";
      gateStatus.className = "uaa-pill uaa-pill-success";
    } else {
      executeBtn.disabled = true;
      gateStatus.textContent = "Gates blocked";
      gateStatus.className = "uaa-pill uaa-pill-danger";
    }
  }

  if (submitLoadBtn) {
    submitLoadBtn.addEventListener("click", loadSubmitState);
  }

  if (submitApproveBtn) {
    submitApproveBtn.addEventListener("click", async () => {
      if (!submitCurrentJobId) return;
      // Load the review state to build a snapshot, then approve it.
      try {
        const reviewState = await fetchJSON(`/api/review/${submitCurrentJobId}`);
        const snapshot = {
          application_id: submitCurrentJobId,
          application_url: reviewState.application_url || "",
          fields: reviewState.fill_summary?.results || [],
          documents: reviewState.documents || [],
          pending_intervention_count: reviewState.has_unresolved_interventions ? 1 : 0,
        };
        const result = await postJSON(
          `/api/submit/${submitCurrentJobId}/approve`,
          { snapshot: snapshot, confirm: true }
        );
        await loadSubmitState();
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          `<p class="uaa-error">Approve error: ${esc(err.message)}</p>`;
      }
    });
  }

  if (submitRevokeBtn) {
    submitRevokeBtn.addEventListener("click", async () => {
      if (!submitCurrentJobId) return;
      try {
        await postJSON(`/api/submit/${submitCurrentJobId}/revoke`, {});
        await loadSubmitState();
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          `<p class="uaa-error">Revoke error: ${esc(err.message)}</p>`;
      }
    });
  }

  if (submitExecuteBtn) {
    submitExecuteBtn.addEventListener("click", () => {
      document.getElementById("submit-confirm-dialog").style.display = "block";
    });
  }

  if (submitConfirmNoBtn) {
    submitConfirmNoBtn.addEventListener("click", () => {
      document.getElementById("submit-confirm-dialog").style.display = "none";
    });
  }

  if (submitConfirmYesBtn) {
    submitConfirmYesBtn.addEventListener("click", async () => {
      document.getElementById("submit-confirm-dialog").style.display = "none";
      if (!submitCurrentJobId) return;
      try {
        const status = await fetchJSON(`/api/submit/${submitCurrentJobId}/status`);
        if (!status.approval_id) {
          throw new Error("No active approval");
        }
        const result = await postJSON(
          `/api/submit/${submitCurrentJobId}/submit`,
          { approval_id: status.approval_id, confirm: true }
        );
        await loadSubmitState();
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          `<p class="uaa-error">Submit error: ${esc(err.message)}</p>`;
      }
    });
  }

  // ---- HTML escape ----
  function esc(text) {
    if (text == null) return "";
    const div = document.createElement("div");
    div.textContent = String(text);
    return div.innerHTML;
  }

  // ---- Polling ----
  function startPolling() {
    loadStatus();
    pollTimer = setInterval(loadStatus, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else startPolling();
  });

  startPolling();
})();

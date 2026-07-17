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
  const submitRefreshBtn = document.getElementById("submit-refresh");
  const submitApproveBtn = document.getElementById("submit-approve");
  const submitRevokeBtn = document.getElementById("submit-revoke");
  const submitExecuteBtn = document.getElementById("submit-execute");
  const submitConfirmHighRiskBtn = document.getElementById("submit-confirm-high-risk");
  const submitConfirmYesBtn = document.getElementById("submit-confirm-yes");
  const submitConfirmNoBtn = document.getElementById("submit-confirm-no");
  let submitCurrentJobId = null;
  let submitRequestInFlight = false;

  function setSubmitBusy(busy) {
    submitRequestInFlight = busy;
    const btns = document.querySelectorAll("#submit-controls button, #submit-high-risk-controls button, #submit-refresh, #submit-load");
    btns.forEach((b) => { b.disabled = busy; });
    const display = document.getElementById("submit-state-display");
    if (busy) {
      display.classList.add("uaa-submit-loading");
    } else {
      display.classList.remove("uaa-submit-loading");
    }
  }

  function announce(msg) {
    const el = document.getElementById("submit-announce");
    if (el) el.textContent = msg;
  }

  let _lastSubmitData = null;
  let _lastButtonStates = null;

  function _applyButtonStates() {
    if (!_lastButtonStates) return;
    const approveBtn = document.getElementById("submit-approve");
    const revokeBtn = document.getElementById("submit-revoke");
    const executeBtn = document.getElementById("submit-execute");
    const confirmBtn = document.getElementById("submit-confirm-high-risk");
    if (approveBtn) approveBtn.disabled = _lastButtonStates.approveDisabled;
    if (revokeBtn) revokeBtn.disabled = _lastButtonStates.revokeDisabled;
    if (executeBtn) executeBtn.disabled = _lastButtonStates.executeDisabled;
    if (confirmBtn) confirmBtn.disabled = _lastButtonStates.confirmDisabled;
  }

  async function loadSubmitState(doObserve) {
    const jobId = document.getElementById("submit-job-id").value.trim();
    if (!jobId || submitRequestInFlight) return;
    submitCurrentJobId = jobId;
    const display = document.getElementById("submit-state-display");
    const controls = document.getElementById("submit-controls");
    const highRiskControls = document.getElementById("submit-high-risk-controls");
    controls.style.display = "none";
    highRiskControls.style.display = "none";
    display.innerHTML = '<p class="uaa-empty">Loading...</p>';
    announce("Loading submission state");
    setSubmitBusy(true);
    try {
      let rawData;
      if (doObserve) {
        const resp = await fetch(`/api/submit/${encodeURIComponent(jobId)}/observe`, {
          method: "POST",
          headers: { Accept: "application/json" },
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        rawData = await resp.json();
        announce("Observation complete");
      } else {
        const resp = await fetch(`/api/submit/${encodeURIComponent(jobId)}/status`, {
          headers: { Accept: "application/json" },
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        rawData = await resp.json();
      }
      _lastSubmitData = rawData.snapshot || rawData;
      renderSubmitState(_lastSubmitData);
    } catch (err) {
      _lastSubmitData = null;
      _lastButtonStates = null;
      display.innerHTML = `<p class="uaa-error">Error: ${esc(err.message)}</p>`;
      announce("Error loading submission state");
    } finally {
      setSubmitBusy(false);
      _applyButtonStates();
    }
  }

  function renderSubmitState(data) {
    const display = document.getElementById("submit-state-display");
    const controls = document.getElementById("submit-controls");
    const highRiskControls = document.getElementById("submit-high-risk-controls");
    const gateStatus = document.getElementById("submit-gate-status");
    const executeBtn = document.getElementById("submit-execute");
    const approveBtn = document.getElementById("submit-approve");
    const revokeBtn = document.getElementById("submit-revoke");
    const confirmBtn = document.getElementById("submit-confirm-high-risk");

    let html = "";
    _lastButtonStates = {
      approveDisabled: true,
      revokeDisabled: true,
      executeDisabled: true,
      confirmDisabled: true,
    };

    const hasSnapshot = !!data.snapshot_hash;
    const fields = data.fields || [];
    const docs = data.documents || [];
    const isComplete = data.is_complete;
    const snapshotHash = data.snapshot_hash || "";
    const isStale = data.is_stale || data.approval_is_stale || false;

    // ---- Job section (always shown) ----
    html += '<div class="uaa-submit-section">';
    html += '<h3>Job</h3>';
    html += '<div class="uaa-submit-field"><strong>Company:</strong> ' + esc(data.company || "") + '</div>';
    html += '<div class="uaa-submit-field"><strong>Job Title:</strong> ' + esc(data.job_title || "") + '</div>';
    html += '<div class="uaa-submit-field"><strong>Application ID:</strong> <code>' + esc(data.application_id || "") + '</code></div>';
    if (data.external_job_id) {
      html += '<div class="uaa-submit-field"><strong>External Job ID:</strong> ' + esc(data.external_job_id) + '</div>';
    }
    if (data.platform) {
      html += '<div class="uaa-submit-field"><strong>Platform:</strong> ' + esc(data.platform) + '</div>';
    }
    if (data.application_url) {
      html += '<div class="uaa-submit-field"><strong>URL:</strong> <a href="' + esc(data.application_url) + '" target="_blank" rel="noopener">' + esc(data.application_url) + '</a></div>';
    }
    if (data.observation_timestamp) {
      html += '<div class="uaa-submit-field"><strong>Observation Timestamp:</strong> ' + esc(fmtDate(data.observation_timestamp)) + '</div>';
    }
    html += '</div>';

    // ---- No snapshot state ----
    if (!hasSnapshot) {
      html += '<div class="uaa-submit-section">';
      html += '<div class="uaa-submit-warning">No persisted snapshot. Click <strong>Refresh Live Review</strong> to observe the live form.</div>';
      html += '</div>';
      display.innerHTML = html;
      controls.style.display = "flex";
      highRiskControls.style.display = "none";
      approveBtn.disabled = true;
      revokeBtn.disabled = true;
      executeBtn.disabled = true;
      gateStatus.textContent = "No snapshot";
      gateStatus.className = "uaa-pill uaa-pill-idle";
      return;
    }

    // ---- Form fields section ----
    if (fields.length > 0) {
      html += '<div class="uaa-submit-section">';
      html += '<h3>Form Fields <span class="uaa-submit-confidence">(' + fields.length + ' fields)</span></h3>';
      fields.forEach(function (f, idx) {
        var isSecret = (f.field_type === "password" || f.field_type === "token" || f.field_type === "api_key");
        var isHighRisk = f.requires_confirmation || f.risk_level === "high";
        html += '<div class="uaa-submit-field-detail">';
        html += '<div><strong>Label:</strong> ' + esc(f.label || esc(f.field_token)) + '</div>';
        html += '<div><strong>Type:</strong> <span class="uaa-field-type-tag">' + esc(f.field_type) + '</span> ' + (f.required ? '<span class="uaa-submit-state-pill pending">Required</span>' : '') + '</div>';
        if (isSecret) {
          html += '<div><strong>Filled:</strong> <em class="uaa-submit-confidence">(hidden)</em></div>';
        } else {
          html += '<div><strong>Filled:</strong> ' + esc(f.filled_value || "—") + '</div>';
        }
        html += '<div><strong>Selected:</strong> ' + esc(f.selected_value || "—") + '</div>';
        html += '<div><strong>Status:</strong> ' + esc(f.status || "—") + '</div>';
        var riskClass = "";
        if (f.risk_level === "high") riskClass = "uaa-field-risk-high";
        else if (f.risk_level === "medium") riskClass = "uaa-field-risk-medium";
        else riskClass = "uaa-field-risk-low";
        html += '<div><strong>Risk:</strong> <span class="' + riskClass + '">' + esc(f.risk_level || "low") + '</span></div>';
        if (f.evidence) {
          html += '<div class="uaa-submit-confidence"><strong>Evidence:</strong> ' + esc(f.evidence) + '</div>';
        } else {
          html += '<div class="uaa-submit-confidence"><strong>Evidence:</strong> —</div>';
        }
        if (f.validation_error) {
          html += '<div><strong>Validation:</strong> <span class="uaa-field-risk-high">' + esc(f.validation_error) + '</span></div>';
        } else {
          html += '<div><strong>Validation:</strong> None</div>';
        }
        if (f.source) {
          html += '<div class="uaa-submit-confidence"><strong>Source:</strong> ' + esc(f.source) + '</div>';
        }
        if (f.options && f.options.length > 0) {
          html += '<div class="uaa-submit-options"><strong>Options:</strong> ' + f.options.map(function (o) { return esc(o); }).join(", ") + '</div>';
        }
        // High-risk confirmation checkbox
        if (isHighRisk) {
          var checkedAttr = f.confirmed ? ' checked="checked" disabled="disabled"' : '';
          html += '<div class="uaa-submit-checkbox-label"><input type="checkbox" class="uaa-hr-checkbox" data-field-token="' + esc(f.field_token) + '"' + checkedAttr + ' /> <span>High-risk field' + (f.confirmed ? ' (confirmed)' : ' — requires confirmation') + '</span></div>';
        }
        html += '<div class="uaa-submit-confidence"><strong>Confirmation state:</strong> ' + (f.confirmed ? 'Confirmed' : 'Pending') + '</div>';
        html += '</div>';
      });
      html += '</div>';
    }

    // ---- Documents section ----
    if (docs.length > 0) {
      html += '<div class="uaa-submit-section">';
      html += '<h3>Documents <span class="uaa-submit-confidence">(' + docs.length + ' documents)</span></h3>';
      docs.forEach(function (d) {
        html += '<div class="uaa-submit-doc">';
        html += '<div><strong>Kind:</strong> ' + esc(d.document_kind || "—") + '</div>';
        html += '<div><strong>Filename:</strong> ' + esc(d.filename || "—") + '</div>';
        html += '<div class="uaa-doc-path"><strong>Path:</strong> ' + esc(d.path || "—") + '</div>';
        html += '<div class="uaa-doc-path"><strong>Hash:</strong> ' + esc(d.content_hash || "—") + '</div>';
        html += '<div><strong>Exists:</strong> ' + (d.exists ? 'Yes' : 'No') + '</div>';
        html += '<div><strong>Readable:</strong> ' + (d.readable ? 'Yes' : 'No') + '</div>';
        html += '</div>';
      });
      html += '</div>';
    }

    // ---- Safety & State section ----
    html += '<div class="uaa-submit-section">';
    html += '<h3>Safety &amp; State</h3>';
    html += '<div class="uaa-submit-field"><strong>Form Fingerprint:</strong> <code>' + esc(snapshotHash ? (data.form_fingerprint || "—") : "—") + '</code></div>';
    html += '<div class="uaa-submit-field"><strong>Snapshot Hash:</strong> <code>' + esc(snapshotHash) + '</code></div>';
    html += '<div class="uaa-submit-field"><strong>Completeness:</strong> ' + (isComplete ? '<span class="uaa-submit-state-pill ready">Complete</span>' : '<span class="uaa-submit-state-pill blocked">Incomplete</span>') + '</div>';
    html += '<div class="uaa-submit-field"><strong>Pending Interventions:</strong> ' + (data.pending_intervention_count || 0) + '</div>';
    html += '<div class="uaa-submit-field"><strong>Unresolved Required Fields:</strong> ' + (data.unresolved_required_field_count || 0) + '</div>';
    html += '<div class="uaa-submit-field"><strong>Unconfirmed High-Risk:</strong> ' + (data.unconfirmed_high_risk_count || 0) + '</div>';
    html += '<div class="uaa-submit-field"><strong>Real Submission Enabled:</strong> ' + (data.enable_real_submission ? 'YES' : 'NO') + '</div>';
    if (data.submit_control) {
      html += '<div class="uaa-submit-field"><strong>Submit Control:</strong> ' + esc(data.submit_control.text || "—") + ' <code>' + esc(data.submit_control.selector || "") + '</code></div>';
    }
    html += '</div>';

    // ---- Approval & Submit section ----
    html += '<div class="uaa-submit-section">';
    html += '<h3>Approval &amp; Submit</h3>';

    var approvalStateLabel = data.approval_state || "none";
    var approvalStateClass = "pending";
    if (approvalStateLabel === "active") approvalStateClass = "ready";
    else if (approvalStateLabel === "consumed") approvalStateClass = "completed";
    else if (approvalStateLabel === "revoked") approvalStateClass = "blocked";
    html += '<div class="uaa-submit-field"><strong>Approval State:</strong> <span class="uaa-submit-state-pill ' + approvalStateClass + '">' + esc(approvalStateLabel) + '</span></div>';

    if (data.active_approval_id) {
      html += '<div class="uaa-submit-field"><strong>Approval ID:</strong> <code>' + esc(data.active_approval_id) + '</code></div>';
    }
    if (data.approved_snapshot_hash) {
      html += '<div class="uaa-submit-field"><strong>Approved Snapshot:</strong> <code>' + esc(data.approved_snapshot_hash) + '</code></div>';
    }

    if (isStale) {
      html += '<div class="uaa-submit-warning">&#9888; Approval is STALE — the form state has changed since approval. Revoke and re-approve the new snapshot.</div>';
    }

    if (data.approve_blocking_reason) {
      html += '<div class="uaa-submit-blocking-reason">Approve blocked: ' + esc(data.approve_blocking_reason) + '</div>';
    }
    if (data.submit_blocking_reason) {
      html += '<div class="uaa-submit-blocking-reason">Submit blocked: ' + esc(data.submit_blocking_reason) + '</div>';
    }

    html += '<div class="uaa-submit-field"><strong>Can Approve:</strong> ' + (data.can_approve ? 'Yes' : 'No') + '</div>';
    html += '<div class="uaa-submit-field"><strong>Can Submit:</strong> ' + (data.can_submit ? 'Yes' : 'No') + '</div>';

    // Latest submission result
    if (data.latest_submission_state) {
      var resultClass = "completed";
      if (data.latest_submission_state === "failed" || data.latest_submission_error) resultClass = "blocked";
      html += '<div class="uaa-submit-field"><strong>Latest Submission:</strong> <span class="uaa-submit-state-pill ' + resultClass + '">' + esc(data.latest_submission_state) + '</span></div>';
      if (data.latest_submission_timestamp) {
        html += '<div class="uaa-submit-field"><strong>Submission Timestamp:</strong> ' + esc(fmtDate(data.latest_submission_timestamp)) + '</div>';
      }
      if (data.latest_submission_error) {
        html += '<div class="uaa-submit-error"><strong>Submission Error:</strong> ' + esc(data.latest_submission_error) + '</div>';
      }
    }
    html += '</div>';

    display.innerHTML = html;
    controls.style.display = "flex";
    highRiskControls.style.display = "flex";

    // Wire up high-risk checkboxes
    var checkboxes = display.querySelectorAll(".uaa-hr-checkbox");
    checkboxes.forEach(function (cb) {
      cb.addEventListener("change", updateConfirmHighRiskButton);
    });
    updateConfirmHighRiskButton();

    // Button enable/disable rules
    approveBtn.disabled = !data.can_approve;
    revokeBtn.disabled = (data.approval_state !== "active");
    if (data.can_submit) {
      executeBtn.disabled = false;
      gateStatus.textContent = "Gates passed";
      gateStatus.className = "uaa-pill uaa-pill-success";
    } else {
      executeBtn.disabled = true;
      gateStatus.textContent = "Gates blocked";
      gateStatus.className = "uaa-pill uaa-pill-danger";
    }

    // Disable submit if stale
    if (isStale) {
      executeBtn.disabled = true;
    }

    // Disable submit if already submitted
    if (data.latest_submission_state && data.latest_submission_state !== "failed") {
      executeBtn.disabled = true;
      gateStatus.textContent = "Already submitted";
      gateStatus.className = "uaa-pill uaa-pill-completed";
    }

    _lastButtonStates = {
      approveDisabled: approveBtn.disabled,
      revokeDisabled: revokeBtn.disabled,
      executeDisabled: executeBtn.disabled,
      confirmDisabled: submitConfirmHighRiskBtn.disabled,
    };
  }

  function updateConfirmHighRiskButton() {
    var checked = document.querySelectorAll(".uaa-hr-checkbox:checked:not(:disabled)");
    submitConfirmHighRiskBtn.disabled = checked.length === 0;
  }

  function getSelectedHighRiskTokens() {
    var checked = document.querySelectorAll(".uaa-hr-checkbox:checked:not(:disabled)");
    return Array.from(checked).map(function (cb) { return cb.getAttribute("data-field-token"); });
  }

  function formatError(err) {
    return err.message || String(err);
  }

  // ---- Event handlers ----
  if (submitLoadBtn) {
    submitLoadBtn.addEventListener("click", function () { loadSubmitState(false); });
  }
  if (submitRefreshBtn) {
    submitRefreshBtn.addEventListener("click", function () { loadSubmitState(true); });
  }

  if (submitConfirmHighRiskBtn) {
    submitConfirmHighRiskBtn.addEventListener("click", async function () {
      if (!submitCurrentJobId || submitRequestInFlight) return;
      var tokens = getSelectedHighRiskTokens();
      if (tokens.length === 0) return;

      // Get the current snapshot hash from the displayed state
      var hashEl = document.querySelector("#submit-state-display .uaa-submit-field code");
      if (!hashEl) return;
      // Find the snapshot hash - it's in the Safety section
      var allFields = document.querySelectorAll("#submit-state-display .uaa-submit-field");
      var snapshotHash = "";
      allFields.forEach(function (f) {
        var txt = f.textContent || "";
        if (txt.indexOf("Snapshot Hash:") !== -1) {
          var code = f.querySelector("code");
          if (code) snapshotHash = code.textContent || "";
        }
      });
      if (!snapshotHash) return;

      setSubmitBusy(true);
      try {
        var result = await postJSON("/api/submit/" + encodeURIComponent(submitCurrentJobId) + "/confirm-high-risk", {
          snapshot_hash: snapshotHash,
          field_tokens: tokens,
          confirm: true,
        });
        var data = result.snapshot || result;
        renderSubmitState(data);
        announce("High-risk answers confirmed");
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          '<p class="uaa-error">Confirm high-risk error: ' + esc(formatError(err)) + '</p>';
        announce("Error confirming high-risk answers");
      } finally {
        setSubmitBusy(false);
      }
    });
  }

  if (submitApproveBtn) {
    submitApproveBtn.addEventListener("click", async function () {
      if (!submitCurrentJobId || submitRequestInFlight) return;

      // Extract snapshot hash from rendered state.
      var allFields = document.querySelectorAll("#submit-state-display .uaa-submit-field");
      var snapshotHash = "";
      allFields.forEach(function (f) {
        var txt = f.textContent || "";
        if (txt.indexOf("Snapshot Hash:") !== -1) {
          var code = f.querySelector("code");
          if (code) snapshotHash = code.textContent || "";
        }
      });
      if (!snapshotHash) return;

      setSubmitBusy(true);
      try {
        var result = await postJSON("/api/submit/" + encodeURIComponent(submitCurrentJobId) + "/approve", {
          snapshot_hash: snapshotHash,
          confirm: true,
        });
        await loadSubmitState(false);
        announce("Snapshot approved");
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          '<p class="uaa-error">Approve error: ' + esc(formatError(err)) + '</p>';
        announce("Error approving snapshot");
      } finally {
        setSubmitBusy(false);
      }
    });
  }

  if (submitRevokeBtn) {
    submitRevokeBtn.addEventListener("click", async function () {
      if (!submitCurrentJobId || submitRequestInFlight) return;
      setSubmitBusy(true);
      try {
        await postJSON("/api/submit/" + encodeURIComponent(submitCurrentJobId) + "/revoke", {});
        await loadSubmitState(false);
        announce("Approval revoked");
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          '<p class="uaa-error">Revoke error: ' + esc(formatError(err)) + '</p>';
        announce("Error revoking approval");
      } finally {
        setSubmitBusy(false);
      }
    });
  }

  if (submitExecuteBtn) {
    submitExecuteBtn.addEventListener("click", function () {
      document.getElementById("submit-confirm-dialog").style.display = "block";
    });
  }

  if (submitConfirmNoBtn) {
    submitConfirmNoBtn.addEventListener("click", function () {
      document.getElementById("submit-confirm-dialog").style.display = "none";
    });
  }

  if (submitConfirmYesBtn) {
    submitConfirmYesBtn.addEventListener("click", async function () {
      document.getElementById("submit-confirm-dialog").style.display = "none";
      if (!submitCurrentJobId || submitRequestInFlight) return;

      setSubmitBusy(true);
      try {
        var statusResp = await fetchJSON("/api/submit/" + encodeURIComponent(submitCurrentJobId) + "/status");
        var statusData = statusResp.snapshot || statusResp;
        var approvalId = statusData.active_approval_id;
        if (!approvalId) {
          throw new Error("No active approval");
        }
        var result = await postJSON("/api/submit/" + encodeURIComponent(submitCurrentJobId) + "/submit", {
          approval_id: approvalId,
          confirm: true,
        });
        await loadSubmitState(false);
        announce("Submission completed");
      } catch (err) {
        document.getElementById("submit-state-display").innerHTML =
          '<p class="uaa-error">Submit error: ' + esc(formatError(err)) + '</p>';
        announce("Error during submission");
      } finally {
        setSubmitBusy(false);
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

/* UniversalAutoApplier dashboard shell.
 *
 * Vanilla JS, no framework. One shared polling controller with backoff and
 * cancellation, per TECHNICAL_BASELINE.md -> Dashboard Frontend.
 *
 * The bootstrap phase only renders health. Phase 6 will add views for Queue,
 * Interventions, History, Job Detail, Logs, and Settings.
 */

(() => {
  "use strict";

  const POLL_INTERVAL_MS = 5000;
  const POLL_BACKOFF_MAX_MS = 30000;

  const overall = document.getElementById("overall-status");
  const version = document.getElementById("uaa-version");
  const submitMode = document.getElementById("uaa-submit-mode");
  const componentList = document.getElementById("component-list");

  const pillClassFor = (state) => `uaa-pill uaa-pill-${state}`;

  const renderReport = (report) => {
    if (overall) {
      overall.textContent = report.status;
      overall.className = pillClassFor(report.status);
    }
    if (version) {
      version.textContent = report.version;
    }
    if (submitMode) {
      // Submit mode is rendered from window.__UAA_SETTINGS__ if present, set
      // by the server in a future phase. For now it stays "—" because the
      // bootstrap dashboard does not expose a settings endpoint.
      submitMode.textContent = window.__UAA_SUBMIT_MODE__ || "review";
    }
    if (!componentList) return;

    if (!Array.isArray(report.components) || report.components.length === 0) {
      componentList.innerHTML = "<li>No components reported.</li>";
      return;
    }

    componentList.innerHTML = "";
    for (const component of report.components) {
      const li = document.createElement("li");

      const left = document.createElement("span");
      left.className = "uaa-component-name";
      left.textContent = component.name;

      const detail = document.createElement("span");
      detail.className = "uaa-component-detail";
      detail.textContent = component.detail || "";

      const pill = document.createElement("span");
      pill.className = pillClassFor(component.state);
      pill.textContent = component.state;

      left.appendChild(detail);
      li.appendChild(left);
      li.appendChild(pill);
      componentList.appendChild(li);
    }
  };

  const fetchHealth = async () => {
    const response = await fetch("/api/health", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`health endpoint returned ${response.status}`);
    }
    return response.json();
  };

  const startPolling = () => {
    let interval = POLL_INTERVAL_MS;
    let timer = null;
    let stopped = false;

    const tick = async () => {
      try {
        const report = await fetchHealth();
        renderReport(report);
        interval = POLL_INTERVAL_MS;
      } catch (err) {
        console.error("[UAA] health poll failed", err);
        interval = Math.min(interval * 2, POLL_BACKOFF_MAX_MS);
      } finally {
        if (!stopped) {
          timer = setTimeout(tick, interval);
        }
      }
    };

    const stop = () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (timer) clearTimeout(timer);
      } else if (!stopped) {
        timer = setTimeout(tick, 0);
      }
    });

    // Kick off immediately.
    tick();
    return stop;
  };

  startPolling();
})();

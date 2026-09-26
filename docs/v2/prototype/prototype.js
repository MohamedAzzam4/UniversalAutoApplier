(function () {
  "use strict";
  var viewLabels = { overview: "Overview", applications: "Applications", attention: "Needs your input", knowledge: "Answers & documents" };
  var views = Array.prototype.slice.call(document.querySelectorAll(".view"));
  var navLinks = Array.prototype.slice.call(document.querySelectorAll(".primary-nav [data-view]"));
  var toastTimer = null, activeDialog = null, returnFocus = null;
  var saveFailureNext = false, conflictNext = false, saved = false, currentDetail = "maple";

  var jobs = [
    { id:"northstar", company:"Northstar Analytics", initial:"N", avatar:"avatar-gold", role:"Senior Data Analyst", location:"Berlin", kind:"attention", state:"Needs your input", statusClass:"status-warning", phase:"Questionnaire · postal code", next:"Enter a verified answer", title:"What is your postal code?", reason:"No verified profile value exists. UAA paused before entering an answer.", time:"Paused · 4 min ago", stages:[["Queue preflight","Documents verified · 10:20"],["Application start","Page identity matched · 10:24"],["Questionnaire","Paused before required postal-code field"]], evidence:[["Postal code","No verified fact found · profile revision 3"],["CV","Bundle rev 7 · available locally"],["Cover letter","Bundle rev 7 · available locally"]], callout:"Paused safely · owner answer required", calloutText:"The answer is missing. Nothing was guessed or typed. Save a scoped correction to resume only this application.", action:"Resolve missing answer", actionKind:"correction" },
    { id:"welltide", company:"Welltide Software", initial:"W", avatar:"avatar-blue", role:"Product Analyst", location:"Remote", kind:"active", state:"Preparing", statusClass:"status-blue", phase:"Questionnaire · 3 of 5 verified", next:"Continue at next safe checkpoint", title:"Application preparation", reason:"Worker is active and rechecking the current questionnaire step.", time:"Last verified · 12 sec ago", stages:[["Queue preflight","Documents verified · 10:04"],["Application start","Page identity matched · 10:08"],["Questionnaire","3 of 5 required steps read back · 10:39"],["Next step","Pending observation"]], evidence:[["Required answers","3 verified · 1 optional blank · no guesses"],["CV","Bundle rev 7 · selection_verified"],["Cover letter","Bundle rev 7 · selection_verified"]], callout:"Preparing · questionnaire", calloutText:"The worker has not reached the final review boundary. No approval or final submit occurred.", action:"Follow progress", actionKind:"close" },
    { id:"maple", company:"Maple & Reed", initial:"M", avatar:"avatar-green", role:"Operations Coordinator", location:"Hamburg", kind:"review", state:"Review ready", statusClass:"status-success", phase:"Final review boundary verified", next:"Review snapshot · not submitted", title:"Complete application snapshot", reason:"Required fields were read back and the qualified final review boundary was observed.", time:"Last verified · 10:39", stages:[["Queue preflight","Documents verified · 10:31"],["Application form","4 required answers read back · 10:37"],["Document evidence","selection_verified · qualified native upload flow"],["Final review boundary","Snapshot complete · no submit request sent"]], evidence:[["Work authorization","Owner-confirmed profile fact · revision 3 · re-read verified"],["CV","Bundle rev 7 · selection_verified · local selection only"],["Cover letter","Bundle rev 7 · selection_verified · local selection only"]], callout:"Review ready · approval required", calloutText:"This application has not been submitted. Review the complete snapshot before entering the separate controlled-submission flow.", action:"Review snapshot", actionKind:"review" },
    { id:"alder", company:"Alder Biomed", initial:"A", avatar:"avatar-slate", role:"Research Associate", location:"Munich", kind:"attention", state:"Waiting for login", statusClass:"status-blue", phase:"Sign in · owner action required", next:"Open the bound application tab", title:"Sign in to continue", reason:"The bound application page redirected to sign-in. The worker acknowledged pause before entering data.", time:"Paused · 8 min ago", stages:[["Queue preflight","Documents verified · 10:13"],["Application start","Board identity matched · 10:16"],["Sign in","Redirect observed · owner action required"]], evidence:[["Page state","Sign-in page title observed · no form values entered"],["Browser owner","Worker acknowledged pause · session remains bound"],["Documents","Not selected · no upload attempted"]], callout:"Waiting for owner · login required", calloutText:"Open only this bound application session. Resume must re-observe the page and invalidate any stale review.", action:"Show owner steps", actionKind:"login" },
    { id:"cedar", company:"Cedar Point Studio", initial:"C", avatar:"avatar-gold", role:"Content Designer", location:"Berlin", kind:"eligible", state:"Eligible to prepare", statusClass:"status-neutral", phase:"Preflight passed · not scheduled", next:"Review queue to schedule", title:"Ready for preparation", reason:"Queue and document preflight passed. No browser has opened.", time:"Imported · 10:42", stages:[["Queue import","Source identity retained · 10:42"],["Preflight","CV and cover letter verified"]], evidence:[["CV","Bundle rev 7 · verified locally"],["Cover letter","Bundle rev 7 · verified locally"]], callout:"Eligible · not yet scheduled", calloutText:"This job is ready for preparation but no application worker has started it.", action:"Review queue preflight", actionKind:"preflight" },
    { id:"keystone", company:"Keystone Health", initial:"K", avatar:"avatar-blue", role:"People Operations Analyst", location:"Remote", kind:"eligible", state:"Eligible to prepare", statusClass:"status-neutral", phase:"Preflight passed · not scheduled", next:"Review queue to schedule", title:"Ready for preparation", reason:"Queue and document preflight passed. No browser has opened.", time:"Imported · 10:42", stages:[["Queue import","Source identity retained · 10:42"],["Preflight","CV and cover letter verified"]], evidence:[["CV","Bundle rev 7 · verified locally"],["Cover letter","Bundle rev 7 · verified locally"]], callout:"Eligible · not yet scheduled", calloutText:"This job is ready for preparation but no application worker has started it.", action:"Review queue preflight", actionKind:"preflight" },
    { id:"morrow", company:"Morrow Works", initial:"M", avatar:"avatar-purple", role:"Operations Associate", location:"Hamburg", kind:"eligible", state:"Eligible to prepare", statusClass:"status-neutral", phase:"Preflight passed · not scheduled", next:"Review queue to schedule", title:"Ready for preparation", reason:"Queue and document preflight passed. No browser has opened.", time:"Imported · 10:42", stages:[["Queue import","Source identity retained · 10:42"],["Preflight","CV and cover letter verified"]], evidence:[["CV","Bundle rev 7 · verified locally"],["Cover letter","Bundle rev 7 · verified locally"]], callout:"Eligible · not yet scheduled", calloutText:"This job is ready for preparation but no application worker has started it.", action:"Review queue preflight", actionKind:"preflight" },    { id:"papertrail", company:"Papertrail Robotics", initial:"P", avatar:"avatar-rose", role:"Project Manager", location:"Potsdam", kind:"blocked", state:"Blocked before start", statusClass:"status-danger", phase:"Preflight · cover letter missing", next:"Select an eligible document bundle", title:"Document bundle incomplete", reason:"No cover letter is present in the selected project-management bundle.", time:"Preflight · 10:42", stages:[["Queue import","Source record accepted · 10:42"],["Preflight","Blocked before browser start"]], evidence:[["CV","Bundle rev 2 · present"],["Cover letter","Bundle rev 2 · missing"],["Browser","Not opened · no application side effect"]], callout:"Blocked before start · no browser opened", calloutText:"Choose an approved bundle containing a cover letter or exclude this job. UAA will not invent a document path.", action:"Review queue preflight", actionKind:"preflight" }
  ];

  function announce(message) {
    var region = document.getElementById("toast-region");
    region.textContent = message;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { region.textContent = ""; }, 4500);
  }
  function setView(name) {
    if (!viewLabels[name]) return;
    views.forEach(function (view) {
      var active = view.id === "view-" + name;
      view.hidden = !active;
      view.classList.toggle("is-active", active);
    });
    navLinks.forEach(function (link) {
      if (link.dataset.view === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    document.getElementById("breadcrumb-current").textContent = viewLabels[name];
    var heading = document.querySelector("#view-" + name + " h1");
    if (heading) window.setTimeout(function () { heading.focus({ preventScroll: true }); }, 0);
    if (window.location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  }
  document.querySelectorAll("a[data-view]").forEach(function (link) {
    link.addEventListener("click", function (event) { event.preventDefault(); setView(link.dataset.view); });
  });

  function openDialog(dialog, opener) {
    if (!dialog) return;
    returnFocus = opener || document.activeElement;
    activeDialog = dialog;
    dialog.showModal();
    if (dialog.id === "correction-dialog") document.getElementById("postal-code").focus();
    else {
      var target = dialog.querySelector("button:not(.dialog-close), input, summary");
      if (target) target.focus();
    }
  }
  function closeDialog(dialog) {
    if (dialog && dialog.open) dialog.close();
  }
  document.querySelectorAll("[data-close-dialog], .dialog-close").forEach(function (button) {
    button.addEventListener("click", function () { closeDialog(button.closest("dialog")); });
  });
  document.querySelectorAll("dialog").forEach(function (dialog) {
    dialog.addEventListener("close", function () {
      if (activeDialog === dialog) activeDialog = null;
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
      else {
        var heading = document.querySelector(".view.is-active h1");
        if (heading) heading.focus();
      }
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && activeDialog && activeDialog.open) activeDialog.close();
  });

  function openPreflight(opener) { openDialog(document.getElementById("preflight-dialog"), opener); }
  document.getElementById("open-preflight").addEventListener("click", function () { openPreflight(this); });
  document.getElementById("open-preflight-bottom").addEventListener("click", function () { openPreflight(this); });
  document.getElementById("applications-preflight").addEventListener("click", function () { openPreflight(this); });
  document.querySelectorAll("[data-open-correction]").forEach(function (button) {
    button.addEventListener("click", function () { openDialog(document.getElementById("correction-dialog"), button); });
  });

  function cardMarkup(job) {
    return '<article class="application-card" data-kind="' + job.kind + '">' +
      '<div class="application-card-heading"><span class="company-avatar ' + job.avatar + '" aria-hidden="true">' + job.initial + '</span><div><h3>' + job.company + '</h3><p>' + job.role + ' · ' + job.location + '</p></div><span class="status-label ' + job.statusClass + '">' + job.state + '</span></div>' +
      '<div class="application-state"><span>Current step</span><strong>' + job.phase + '</strong></div>' +
      '<div class="application-next"><span>Next action</span><strong>' + job.next + '</strong><button type="button" class="small-arrow" aria-label="Open ' + job.company + ' details" data-open-detail="' + job.id + '">→</button></div></article>';
  }
  function renderApplications() {
    var recent = document.getElementById("recent-applications");
    var all = document.getElementById("all-applications");
    recent.innerHTML = jobs.slice(0, 4).map(cardMarkup).join("");
    all.innerHTML = jobs.map(cardMarkup).join("");
    all.querySelectorAll(".application-card").forEach(function (card) {
      var filter = document.querySelector(".filter-chip.is-selected").dataset.filter;
      card.hidden = filter !== "all" && card.dataset.kind !== filter;
    });
  }
  renderApplications();

  document.querySelectorAll("[data-filter]").forEach(function (button) {
    button.addEventListener("click", function () {
      var filter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach(function (other) {
        var selected = other === button;
        other.classList.toggle("is-selected", selected);
        other.setAttribute("aria-pressed", String(selected));
      });
      document.querySelectorAll("#all-applications .application-card").forEach(function (card) {
        card.hidden = filter !== "all" && card.dataset.kind !== filter;
      });
    });
  });

  function renderDetail(job) {
    currentDetail = job.id;
    document.getElementById("detail-company").textContent = job.company.toUpperCase();
    document.getElementById("detail-title").textContent = job.title;
    document.getElementById("detail-role").textContent = job.role + " · " + job.location + " · " + job.time;
    document.getElementById("detail-status").innerHTML = '<span class="status-label ' + job.statusClass + '">' + job.state + '</span><span>' + job.time + '</span>';
    var timeline = document.querySelector("#detail-dialog .detail-timeline ol");
    timeline.innerHTML = job.stages.map(function (stage, index) {
      return '<li class="' + (index < job.stages.length - 1 ? "timeline-done" : "timeline-current") + '"><span>' + stage[0] + '</span><small>' + stage[1] + '</small></li>';
    }).join("");
    var evidence = document.querySelector("#detail-dialog .evidence-section");
    evidence.innerHTML = '<h3>Answer and document evidence</h3>' + job.evidence.map(function (row) {
      return '<div class="evidence-row"><span>' + row[0] + '</span><strong>' + row[1] + '</strong></div>';
    }).join("") + (job.id === "maple" ? '<p class="warning-copy">Local file selection is not proof of remote ATS acceptance. This flow qualifies native submit-time file inputs.</p>' : "");
    var callout = document.querySelector("#detail-dialog .review-callout");
    callout.innerHTML = '<strong>' + job.callout + '</strong><span>' + job.calloutText + '</span>';
    var action = document.getElementById("review-snapshot");
    action.textContent = job.action;
    action.dataset.kind = job.actionKind;
    action.disabled = false;
  }
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-open-detail]");
    if (!trigger) return;
    var job = jobs.find(function (item) { return item.id === trigger.dataset.openDetail; });
    if (!job) return;
    var preflight = document.getElementById("preflight-dialog");
    var opener = trigger;
    if (preflight.open) { if (returnFocus && document.contains(returnFocus)) opener = returnFocus; preflight.close(); }
    renderDetail(job);
    openDialog(document.getElementById("detail-dialog"), opener);
  });

  document.querySelectorAll('input[name="scope"]').forEach(function (input) {
    input.addEventListener("change", function () {
      document.getElementById("scope-summary").innerHTML = input.value === "profile"
        ? "<strong>Destination:</strong> Postal code · profile fact · used for matching address questions."
        : "<strong>Destination:</strong> Northstar Analytics · this application only.";
    });
  });
  function setSaveStatus(message, kind) {
    var status = document.getElementById("save-progress");
    status.textContent = message;
    status.className = "save-progress" + (kind ? " is-" + kind : "");
  }
  document.getElementById("correction-form").addEventListener("submit", function (event) {
    event.preventDefault();
    if (saved) { announce("This prototype answer has already been saved."); return; }
    var input = document.getElementById("postal-code");
    var error = document.getElementById("postal-error");
    var value = input.value.trim();
    if (value.length < 3 || value.length > 12) {
      error.textContent = "Enter a postal code between 3 and 12 characters.";
      error.hidden = false; input.focus(); return;
    }
    error.hidden = true;
    var button = document.getElementById("save-resume");
    button.disabled = true; button.textContent = "Saving…";
    setSaveStatus("Saving correction…", "active");
    window.setTimeout(function () {
      if (conflictNext) {
        document.getElementById("conflict-box").hidden = false;
        setSaveStatus("Conflict · your draft is preserved. Nothing was saved or resumed.", "error");
        button.disabled = false; button.textContent = "Save answer & resume this job";
        document.getElementById("apply-draft").dataset.draft = value;
        conflictNext = false; return;
      }
      if (saveFailureNext) {
        setSaveStatus("Could not save · your draft is still here. Resume was not queued.", "error");
        error.textContent = "Save failed. Check the local store and try again.";
        error.hidden = false; button.disabled = false; button.textContent = "Retry save & resume";
        saveFailureNext = false; input.focus(); return;
      }
      saved = true;
      var scope = document.querySelector('input[name="scope"]:checked').value;
      setSaveStatus("Saved to " + (scope === "profile" ? "profile revision 4" : "this application") + ".", "success");
      window.setTimeout(function () {
        setSaveStatus("Resume queued · only Northstar Analytics will resume.", "success");
        window.setTimeout(function () {
          setSaveStatus("Rechecking · verifying the changed answer and current form state.", "active");
          var northstar = jobs.find(function (item) { return item.id === "northstar"; });
          northstar.state = "Rechecking"; northstar.statusClass = "status-blue";
          northstar.phase = "Rechecking · answer and form state"; northstar.next = "Follow progress";
          northstar.callout = "Rechecking · correction saved";
          northstar.calloutText = "The answer was saved. UAA is re-reading the current form before it continues.";
          northstar.evidence[0] = ["Postal code", "Owner entered · profile revision 4 · awaiting form read-back"];
          document.getElementById("metric-attention").textContent = "1";
          document.getElementById("filter-attention-count").textContent = "1";
          document.getElementById("nav-count").textContent = "1";
          document.getElementById("attention-count").textContent = "1";
          document.getElementById("postal-card").innerHTML = '<div class="attention-card-top"><span class="status-label status-blue">Rechecking</span><span class="time-label">Updated just now</span></div><h3>Northstar Analytics</h3><p class="job-line">Senior Data Analyst</p><p class="reason-copy">Your answer was saved. UAA is re-reading the current form before it continues.</p><div class="evidence-line"><span class="evidence-mark" aria-hidden="true">i</span><span>Profile revision 4 · resume command queued once</span></div>';
          var attentionFirst = document.querySelector("#view-attention .attention-expanded");
          if (attentionFirst) attentionFirst.innerHTML = '<div class="attention-card-top"><span class="status-label status-blue">Rechecking</span><span class="time-label">Updated just now</span></div><h2>Northstar Analytics is rechecking</h2><p class="job-line">Senior Data Analyst · Attempt 2</p><p class="reason-copy">The correction is saved, but it is not treated as verified until read back from the application.</p><div class="evidence-box"><strong>Resume state</strong><span>Profile revision 4 · resume command queued once</span><span>Next: verify postal code and current page state</span></div>';
          renderApplications();
          announce("Answer saved. Resume queued for Northstar Analytics. Rechecking the form.");
        }, 600);
      }, 500);
    }, 350);
  });
  document.getElementById("simulate-save-failure").addEventListener("click", function () {
    saveFailureNext = true; conflictNext = false;
    document.getElementById("conflict-box").hidden = true;
    setSaveStatus("Next save will simulate a local store failure. Your draft will remain.", "");
  });
  document.getElementById("simulate-conflict").addEventListener("click", function () {
    conflictNext = true; saveFailureNext = false;
    document.getElementById("conflict-box").hidden = true;
    setSaveStatus("Next save will simulate another tab changing the revision.", "");
  });
  document.getElementById("reload-current").addEventListener("click", function () {
    document.getElementById("postal-code").value = "10115";
    document.getElementById("conflict-box").hidden = true;
    setSaveStatus("Current revision loaded. Review it before continuing.", "active");
    document.getElementById("postal-code").focus();
  });
  document.getElementById("apply-draft").addEventListener("click", function () {
    document.getElementById("conflict-box").hidden = true;
    setSaveStatus("Draft applied to the current revision. Review and save again to queue resume.", "active");
    document.getElementById("save-resume").disabled = false;
    document.getElementById("save-resume").textContent = "Save answer & resume this job";
    saved = false; document.getElementById("postal-code").focus();
  });

  document.getElementById("start-preparation").addEventListener("click", function () {
    closeDialog(document.getElementById("preflight-dialog"));
    document.getElementById("run-title").textContent = "Preparation started";
    jobs.filter(function (job) { return ["cedar", "keystone", "morrow"].indexOf(job.id) >= 0; }).forEach(function (job) { job.kind = "queued"; job.state = "Queued"; job.phase = "Waiting for worker"; job.next = "Waiting in current run"; job.statusClass = "status-neutral"; job.callout = "Queued · awaiting a worker"; job.calloutText = "Preflight passed. No browser or application form has opened for this job yet."; job.time = "Queued just now"; });
    document.getElementById("metric-eligible").textContent = "0";
    document.querySelector(".import-copy p:last-child").textContent = "Latest queue · 0 eligible · 1 blocked · 1 duplicate skipped · 3 queued in current run";
    document.getElementById("preflight-eligible").textContent = "0 jobs";
    document.getElementById("preflight-detail-count").textContent = "0 eligible · 1 blocked · 1 duplicate skipped · 3 queued";
    document.getElementById("start-preparation").textContent = "Queue already added";
    document.getElementById("start-preparation").disabled = true;
    document.querySelectorAll("[data-preflight-id]").forEach(function (row) { row.querySelector(".status-label").textContent = "Queued"; row.querySelector(".status-label").className = "status-label status-neutral"; });
    renderApplications();
    document.querySelector(".run-badge").innerHTML = '<span class="run-dot" aria-hidden="true"></span>Active · 1 worker';
    document.querySelector(".run-summary").innerHTML = "<strong>1</strong><span>application preparing</span><span class='run-divider' aria-hidden='true'></span><strong>3</strong><span>jobs waiting in current run</span>";
    announce("Preparation queued for 3 eligible applications. One worker is active. No submission occurred.");
  });
  document.getElementById("pause-run").addEventListener("click", function () {
    document.getElementById("run-title").textContent = "Pause requested";
    document.querySelector(".run-badge").textContent = "Pausing at safe checkpoint";
    this.disabled = true; this.textContent = "Pause requested";
    announce("Run will stop starting jobs and pause active work at its next safe checkpoint.");
  });
  document.getElementById("review-snapshot").addEventListener("click", function () {
    var job = jobs.find(function (item) { return item.id === currentDetail; });
    closeDialog(document.getElementById("detail-dialog"));
    if (job && job.actionKind === "correction") openDialog(document.getElementById("correction-dialog"));
    else if (job && job.actionKind === "preflight") openPreflight(returnFocus);
    else if (job && job.actionKind === "review") announce("Full snapshot review would open here. No approval or submission occurred.");
    else if (job && job.actionKind === "login") announce("Manual login would open only the bound application session after ownership is confirmed.");
    else announce("No further action is required in this prototype.");
  });
  document.getElementById("knowledge-add").addEventListener("click", function () { announce("Profile editing is shown as a future design surface; no data is changed."); });
  document.querySelectorAll("[data-prototype-notice]").forEach(function (button) { button.addEventListener("click", function () { announce(button.dataset.prototypeNotice); }); });
  document.getElementById("open-state-key").addEventListener("click", function () { openDialog(document.getElementById("state-dialog"), this); });

  var initialView = window.location.hash.slice(1);
  if (viewLabels[initialView]) setView(initialView);
})();

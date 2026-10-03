const conversationId = `browser-${crypto.randomUUID()}`;
const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const badge = document.querySelector("#active-badge");
const historyContainer = document.querySelector("#dataset-history");
const storageKey = "bng-dataset-history-v1";
let progressCard = null;
let datasetHistory = [];
let capabilityMap = new Map();
let activeResultJobId = null;

function addMessage(text, role = "assistant") {
  const item = document.createElement("article");
  item.className = role;

  if (role === "assistant") {
    item.innerHTML = text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  } else {
    item.textContent = text;
  }

  messages.append(item);
  messages.scrollTop = messages.scrollHeight;
}
function setProgress(text) {
  if (!progressCard) { progressCard = document.createElement("article"); progressCard.className = "assistant progress"; progressCard.setAttribute("role", "status"); progressCard.setAttribute("aria-live", "polite"); const dot = document.createElement("span"); dot.className = "progress-dot"; dot.setAttribute("aria-hidden", "true"); const label = document.createElement("span"); label.className = "progress-text"; progressCard.append(dot, label); messages.append(progressCard); }
  progressCard.querySelector(".progress-text").textContent = text; messages.scrollTop = messages.scrollHeight;
}
function clearProgress() { progressCard?.remove(); progressCard = null; }
function setBadge(jobId) { if (badge) badge.textContent = jobId || "Ready"; }
function recipeLabel(recipe) {
  if (!recipe) return "Dataset";
  return capabilityMap.get(recipe)?.display_name || recipe.split("-").map((part) => part.toUpperCase() === "IPOE" ? "IPoE" : part[0].toUpperCase() + part.slice(1)).join(" ");
}
function formatTimestamp(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
function readStoredHistory() { try { const saved = JSON.parse(localStorage.getItem(storageKey) || "[]"); return Array.isArray(saved) ? saved : []; } catch { return []; } }
function saveHistory() { try { localStorage.setItem(storageKey, JSON.stringify(datasetHistory)); } catch { /* optional browser convenience */ } }
function mergeHistory(...lists) { const byId = new Map(); lists.flat().forEach((entry) => { if (entry?.job_id) byId.set(entry.job_id, { ...byId.get(entry.job_id), ...entry }); }); return [...byId.values()].sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0)); }
function renderHistory() {
  historyContainer.replaceChildren();
  if (!datasetHistory.length) { const empty = document.createElement("span"); empty.className = "history-empty"; empty.textContent = "No completed datasets yet"; historyContainer.append(empty); return; }
  datasetHistory.forEach((entry) => { const item = document.createElement("button"); item.type = "button"; item.className = "dataset-item"; item.setAttribute("aria-label", `Open ${recipeLabel(entry.recipe)} dataset ${entry.job_id}`); const title = document.createElement("span"); title.className = "dataset-title"; title.textContent = recipeLabel(entry.recipe); const meta = document.createElement("span"); meta.className = "dataset-meta"; meta.textContent = `${entry.sessions || "—"} sessions${formatTimestamp(entry.timestamp) ? ` · ${formatTimestamp(entry.timestamp)}` : ""}`; item.append(title, meta); item.addEventListener("click", () => void showDataset(entry.job_id)); historyContainer.append(item); });
}
async function initialiseHistory() { let seed = []; try { const response = await fetch("/static/dataset-history.json", { cache: "no-store" }); if (response.ok) seed = await response.json(); } catch { /* optional seed */ } datasetHistory = mergeHistory(seed, readStoredHistory()); saveHistory(); renderHistory(); }
function metric(label, value) { const item = document.createElement("div"), heading = document.createElement("span"), result = document.createElement("strong"); heading.textContent = label; result.textContent = value; item.append(heading, result); return item; }
async function showDataset(jobId) {
  activeResultJobId = jobId;showingWorkspace = false; form.hidden = false; setActiveWorkspace("generation"); clearProgress(); setBadge(jobId); messages.replaceChildren(); setProgress("Loading dataset result…"); try { const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/result`, { cache: "no-store" }); if (!response.ok) throw new Error(`The saved result is unavailable (${response.status}).`); renderResult(await response.json(), jobId, true); } catch (error) { clearProgress(); addMessage(error.message || "Unable to load the saved dataset result."); } }
function stageMessage(stage) { return ({ queued: "Preparing testbed…", preparing_testbed: "Preparing testbed…", running_experiment: "Running experiment…", packaging_dataset: "Packaging dataset…", finalizing_dataset: "Finalizing dataset…" })[stage] || "Preparing dataset…"; }
async function poll(jobId) {
  try { const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" }); if (!response.ok) throw new Error("Unable to refresh job status."); const job = await response.json(); if (["completed", "partial"].includes(job.status)) { activeResultJobId = jobId;clearProgress(); const result = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/result`, { cache: "no-store" }); if (!result.ok) throw new Error("Dataset completed, but its result could not be loaded."); const detail = await result.json(); rememberDataset(detail, jobId); renderResult(detail, jobId); return; } if (["failed", "rejected", "cancelled"].includes(job.status)) { clearProgress(); addMessage(job.reason || `Dataset generation ${job.status}.`); return; } setProgress(stageMessage(job.stage)); window.setTimeout(() => void poll(jobId), 1500); } catch (error) { setProgress(error.message || "Unable to refresh job status."); }
}
function addChoice(label, callback) { const button = document.createElement("button"); button.type = "button"; button.className = "choice"; button.textContent = label; button.addEventListener("click", callback); messages.append(button); }
async function send(text) {
  activateConversation();
  addMessage(text, "user"); input.value = "";
  try { const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, conversation_id: conversationId,result_job_id: activeResultJobId }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Request failed."); if (data.type === "accepted") { setBadge(data.job_id); setProgress("Preparing testbed…"); void poll(data.job_id); return; } addMessage(data.assistant_message || "I could not understand that request."); if (data.type === "confirmation") { addChoice("Yes", () => void send("yes")); addChoice("No", () => void send("no")); } } catch (error) { addMessage(error.message || "Request failed."); }
}
function resultContext(detail) {
  const metadata = detail.metadata || {};
  const config = metadata.config || {};
  const parameters = detail.parameters || {};
  const summary = detail.summary || {};
  const timestampStart = metadata.timestamp_start || detail.timestamp_start;
  const timestampEnd = metadata.timestamp_end || metadata.timestamp || detail.timestamp_end || detail.timestamp_start;
  const started = timestampStart ? new Date(timestampStart) : null;
  const ended = timestampEnd ? new Date(timestampEnd) : null;
  const durationSeconds = started && ended && !Number.isNaN(started.valueOf()) && !Number.isNaN(ended.valueOf())
    ? Math.max(0, Math.round((ended - started) / 1000)) : undefined;
  return {
    recipe: metadata.recipe || detail.recipe || "ipoe-bind",
    sessions: config.session_count || config.sessions || detail.session_count || parameters.sessions || detail.requested_sessions,
    timestamp: timestampEnd,
    success: summary.success_rate ?? detail.success_rate ?? (detail.failed_sessions === 0 ? 100 : (detail.established_sessions != null && (config.session_count || config.sessions || detail.session_count || parameters.sessions || detail.requested_sessions) ? Math.round((detail.established_sessions / (config.session_count || config.sessions || detail.session_count || parameters.sessions || detail.requested_sessions)) * 100) : undefined)),
    p50: summary.dhcp_p50_ms ?? detail.dhcp_p50_ms ?? detail.setup_p50_ms,
    p95: summary.dhcp_p95_ms ?? detail.dhcp_p95_ms ?? detail.setup_p95_ms,
    cpu: summary.peak_cpu_percent ?? detail.peak_cpu_percent ?? detail.peak_bng_cpu,
    established: detail.established_sessions_blaster ?? detail.established_sessions,
    failed: detail.failed_sessions,
    offeredRate: parameters.offered_rate ?? config.offered_rate,
    durationSeconds,
    parameters,
    topology: metadata.topology || detail.topology || detail.normalized_intent?.topology,
    faults: detail.fault_labels_observed || summary.fault_labels_observed || [],
    artifacts: detail.artifact_paths || metadata.artifact_paths || {},
    status: detail.status || metadata.status,
    reason: detail.reason || metadata.reason,
    scalePointsCompleted: detail.scale_points_completed ?? summary.scale_points_completed,
    completedScalePoints: detail.completed_scale_points ?? summary.completed_scale_points ?? [],
    scaleTimeseries: detail.scale_timeseries ?? summary.scale_timeseries,
    accountingPackets: detail.accounting_packets ?? detail.accounting_packet_count ?? summary.accounting_packets ?? summary.accounting_packet_count,
    raw: detail
  };
}

function hasResultValue(value) {
  return value !== undefined && value !== null && value !== "";
}

function statusLabel(status) {
  return status ? status[0].toUpperCase() + status.slice(1) : undefined;
}

function establishedLabel(result) {
  return hasResultValue(result.established) && hasResultValue(result.sessions)
    ? `${result.established} / ${result.sessions}`
    : undefined;
}

function scaleProgressLabel(result) {
  if (Array.isArray(result.completedScalePoints) && result.completedScalePoints.length) {
    return result.completedScalePoints.map((point) => `${point} sessions`).join(" → ");
  }
  return hasResultValue(result.scalePointsCompleted)
    ? `${result.scalePointsCompleted} completed`
    : undefined;
}

function recipeMetricSpecs(result) {
  const shared = {
    success: () => hasResultValue(result.success) ? ["Success", `${result.success}%`] : null,
    established: () => hasResultValue(establishedLabel(result)) ? ["Established", establishedLabel(result)] : null,
    cpu: () => hasResultValue(result.cpu) ? ["Peak CPU", `${result.cpu}%`] : null,
    duration: () => hasResultValue(result.durationSeconds) ? ["Duration", `${result.durationSeconds} sec`] : null,
    status: () => hasResultValue(statusLabel(result.status)) ? ["Status", statusLabel(result.status)] : null,
    sessions: () => hasResultValue(result.sessions) ? ["Sessions", String(result.sessions)] : null,
    faults: () => Array.isArray(result.faults) && result.faults.length ? ["Faults", result.faults.join(", ")] : null,
  };
  const byRecipe = {
    "ipoe-bind": [
      shared.success,
      () => hasResultValue(result.p50) ? ["DHCP p50", `${result.p50} ms`] : null,
      () => hasResultValue(result.p95) ? ["DHCP p95", `${result.p95} ms`] : null,
      shared.cpu,
      shared.established,
      shared.duration,
    ],
    "ipoe-scale": [
      shared.success,
      shared.established,
      shared.cpu,
      shared.duration,
      () => hasResultValue(scaleProgressLabel(result)) ? ["Scale progression", scaleProgressLabel(result)] : null,
    ],
    "ipoe-flap": [
      shared.status,
      shared.sessions,
      () => hasResultValue(result.parameters.cycles) ? ["Flap cycles", String(result.parameters.cycles)] : null,
      shared.duration,
      shared.cpu,
      shared.faults,
    ],
    "radius-acct": [
      shared.status,
      shared.sessions,
      () => hasResultValue(result.accountingPackets) ? ["Accounting packets", String(result.accountingPackets)] : null,
      shared.duration,
      () => result.artifacts.pcap ? ["PCAP", "Available"] : null,
    ],
  };
  return (byRecipe[result.recipe] || [shared.status, shared.duration]).map((spec) => spec()).filter(Boolean);
}

function recipeSummary(result) {
  if (result.recipe === "ipoe-scale" && Array.isArray(result.completedScalePoints) && result.completedScalePoints.length) {
    return `Completed scale points: ${result.completedScalePoints.join(" → ")} sessions`;
  }
  const parts = [];
  if (hasResultValue(establishedLabel(result))) parts.push(`${establishedLabel(result)} established`);
  if (hasResultValue(result.offeredRate) && result.recipe === "ipoe-bind") parts.push(`${result.offeredRate} sessions/sec`);
  return parts.join("  ·  ");
}

function rememberDataset(detail, jobId) {
  const result = resultContext(detail);
  datasetHistory = mergeHistory(datasetHistory, [{ job_id: jobId, recipe: result.recipe, sessions: result.sessions, timestamp: result.timestamp || new Date().toISOString() }]);
  saveHistory();
  renderHistory();
}

function renderResult(detail, jobId, replace = false) {
  clearProgress();
  if (replace) messages.replaceChildren();
  const result = resultContext(detail);
  const card = document.createElement("section");
  card.className = `completion${result.status === "partial" ? " completion-partial" : ""}`;
  const title = document.createElement("strong");
  title.textContent = result.status === "partial" ? "Dataset generation partially completed" : "Dataset generation completed";
  const description = document.createElement("p");
  description.textContent = `${recipeLabel(result.recipe)} · ${result.sessions || "—"} sessions`;
  const completedAt = document.createElement("span");
  completedAt.className = "completion-time";
  const completedTime = formatTimestamp(result.timestamp);
  completedAt.textContent = completedTime ? `Completed · ${completedTime}` : "Completed";
  const reason = result.reason ? workspaceElement("p", "completion-reason", result.reason) : null;
  const metrics = document.createElement("div");
  metrics.className = "metrics";
  recipeMetricSpecs(result).forEach(([label, value]) => metrics.append(metric(label, value)));
  const runSummary = document.createElement("p");
  runSummary.className = "run-summary";
  runSummary.textContent = recipeSummary(result);
  const actions = document.createElement("div");
  actions.className = "completion-actions";
  [["Download dataset", `/api/jobs/${encodeURIComponent(jobId)}/dataset`], ["Download artifacts", `/api/jobs/${encodeURIComponent(jobId)}/artifacts`]].forEach(([label, href]) => {
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    link.setAttribute("download", "");
    actions.append(link);
  });
  const detailsButton = document.createElement("button");
  detailsButton.type = "button";
  detailsButton.className = "details-toggle";
  detailsButton.textContent = "View details";
  detailsButton.setAttribute("aria-expanded", "false");
  const details = document.createElement("section");
  details.className = "run-details";
  details.hidden = true;
  const detailsTitle = document.createElement("h2");
  detailsTitle.textContent = "Run details";
  const detailGrid = document.createElement("dl");
  const addDetail = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    const term = document.createElement("dt");
    const definition = document.createElement("dd");
    term.textContent = label;
    definition.textContent = value;
    detailGrid.append(term, definition);
  };
  addDetail("Recipe", recipeLabel(result.recipe));
  addDetail("Topology", result.topology);
  addDetail("Requested sessions", result.sessions);
  addDetail("Established sessions", result.established);
  addDetail("Failed sessions", result.failed);
  addDetail("Offered rate", result.offeredRate === undefined ? undefined : `${result.offeredRate}/s`);
  addDetail("Peak CPU", result.cpu === undefined ? undefined : `${result.cpu}%`);
  addDetail("DHCP p50", result.p50 === undefined ? undefined : `${result.p50} ms`);
  addDetail("DHCP p95", result.p95 === undefined ? undefined : `${result.p95} ms`);
  if (Array.isArray(result.faults) && result.faults.length) addDetail("Faults observed", result.faults.join(", "));
  addDetail("Run ID", jobId);
  const artifacts = Object.keys(result.artifacts).length ? Object.keys(result.artifacts).map((name) => name.replaceAll("_", " ")).join(", ") : undefined;
  addDetail("Artifacts", artifacts);
  details.append(detailsTitle, detailGrid);
  detailsButton.addEventListener("click", () => {
    const expanded = detailsButton.getAttribute("aria-expanded") === "true";
    detailsButton.setAttribute("aria-expanded", String(!expanded));
    detailsButton.textContent = expanded ? "View details" : "Hide details";
    details.hidden = expanded;
  });
  actions.append(detailsButton);
  card.append(title, description, completedAt, ...(reason ? [reason] : []), metrics, ...(runSummary.textContent ? [runSummary] : []), actions, details);
  messages.append(card);
  messages.scrollTop = messages.scrollHeight;
}

form.addEventListener("submit", (event) => { event.preventDefault(); const text = input.value.trim(); if (text) void send(text); });
const progressStyles = document.createElement("link");
progressStyles.rel = "stylesheet";
progressStyles.href = "/static/progress.css?v=1";
document.head.append(progressStyles);
const resultDetailsStyles = document.createElement("link");
resultDetailsStyles.rel = "stylesheet";
resultDetailsStyles.href = "/static/result-details.css?v=1";
document.head.append(resultDetailsStyles);
const workspaceStyles = document.createElement("link");
workspaceStyles.rel = "stylesheet";
workspaceStyles.href = "/static/workspace.css?v=4";
document.head.append(workspaceStyles);

let showingWorkspace = false;

function workspaceElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function getWorkspaceData(view) {
  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Request failed (${response.status}).`);
    }

    return response.json();
  }

  if (view === "testbed") {
    const health = await getJson("/api/health");
    return { health };
  }

  if (view === "experiments") {
    const experiments = await getJson("/api/experiments");
    return { experiments };
  }

  if (view === "dashboard") {
  const experiments = await getJson("/api/experiments");
  return {
    experiments,
    health: { testbed_configured: true },
  };
}

  const [health, recipes, experiments] = await Promise.all([
    getJson("/api/health"),
    getJson("/api/capabilities"),
    getJson("/api/experiments"),
  ]);

  return { health, recipes, experiments };
}

function statusPill(status) {
  const normalized = status || "unknown";
  const labels = { implemented: "Ready", implementation_required: "In development", not_available: "Not available", configuration_incomplete: "Configuration incomplete" };
  const label = labels[normalized] || normalized[0].toUpperCase() + normalized.slice(1);
  return workspaceElement(
  "span",
  `status-pill status-${normalized.replaceAll("_", "-")}${normalized === "available" ? " status-configured" : ""}`,
  label
);
}

function sessionSummary(experiment) {
  const parameters = experiment.parameters || {};
  if (experiment.recipe === "ipoe-scale" && parameters.start_sessions != null && parameters.max_sessions != null) return `${parameters.start_sessions} → ${parameters.max_sessions}`;
  const sessions = experiment.requested_sessions ?? parameters.sessions ?? parameters.session_count ?? parameters.max_sessions;
  return sessions == null ? "—" : `${sessions} sessions`;
}
function renderDashboard(data) {
  const shell = workspaceElement("section", "workspace-panel");

  const experiments = Array.isArray(data.experiments)
  ? data.experiments
  : (data.experiments?.experiments || []);

  const total = experiments.length;
  const completed = experiments.filter((experiment) => experiment.status === "completed").length;
  const partial = experiments.filter((experiment) => experiment.status === "partial").length;
  const failed = experiments.filter((experiment) => experiment.status === "failed").length;
  const running = experiments.filter((experiment) =>
    ["queued", "preparing_testbed", "running_experiment", "packaging_dataset", "finalizing_dataset", "running"].includes(experiment.status)
  ).length;

  const testbedConfigured = data.health?.testbed_configured;

  shell.append(
    workspaceElement("p", "eyebrow", "WORKSPACE"),
    workspaceElement("h1", "workspace-heading", "Dashboard"),
    workspaceElement("p", "workspace-empty", "Overview of your BNG dataset generation workspace.")
  );

  const cards = workspaceElement("div", "metrics");

  cards.append(
  metric("Total experiments", total),
  metric("Completed", completed),
  metric("Partial", partial),
  metric("Failed", failed),
  metric("Running", running),
  metric("Testbed", testbedConfigured ? "Configured" : "Not configured")
);
shell.append(cards);
shell.append(
  workspaceElement(
    "p",
    "workspace-empty",
    "Completed and partial experiments can be opened to view their generated dataset details."
  )
);

  const recentTitle = workspaceElement("h2", "workspace-heading", "Recent experiments");
  recentTitle.style.marginTop = "32px";
  shell.append(recentTitle);

  if (!experiments.length) {
    shell.append(
      workspaceElement(
        "p",
        "workspace-empty",
        "No experiments have been recorded yet."
      )
    );
    return shell;
  }

  const recent = workspaceElement("div", "experiment-list");
  const recentExperiments = [...experiments]
    .sort(
      (a, b) =>
        new Date(b.timestamp_end || b.timestamp_start || 0) -
        new Date(a.timestamp_end || a.timestamp_start || 0)
    )
    .slice(0, 5);

  const header = workspaceElement("div", "experiment-table-header");

  ["Recipe", "Status", "Sessions", "Date / time"].forEach((label) => {
    header.append(workspaceElement("span", "", label));
  });

  recent.append(header);

  recentExperiments.forEach((experiment) => {
    const terminal = ["completed", "partial"].includes(experiment.status);

    const row = document.createElement(terminal ? "button" : "div");

    if (terminal) {
      row.type = "button";
      row.addEventListener(
        "click",
        () => void showDataset(experiment.job_id)
      );
    }

    row.className = `experiment-row${terminal ? " is-openable" : ""}`;

    row.append(
      workspaceElement(
        "strong",
        "experiment-recipe",
        recipeLabel(experiment.recipe)
      ),
      statusPill(experiment.status),
      workspaceElement(
        "span",
        "experiment-sessions",
        sessionSummary(experiment)
      ),
      workspaceElement(
        "time",
        "experiment-time",
        formatTimestamp(
          experiment.timestamp_end || experiment.timestamp_start
        ) || "—"
      )
    );

    recent.append(row);
  });

  shell.append(recent);

  return shell;
}


function renderExperiments(data) {
  const shell = workspaceElement("section", "workspace-panel");

  const experiments = Array.isArray(data.experiments)
    ? data.experiments
    : (data.experiments?.experiments || []);

  shell.append(
    workspaceElement("p", "eyebrow", "EXPERIMENT HISTORY"),
    workspaceElement("h1", "workspace-heading", "Experiments")
  );

  if (!experiments.length) {
    shell.append(
      workspaceElement(
        "p",
        "workspace-empty",
        "No experiments have been recorded yet."
      )
    );
    return shell;
  }

  const list = workspaceElement("div", "experiment-list");
  const header = workspaceElement("div", "experiment-table-header");

  ["Recipe", "Status", "Sessions", "Date / time"].forEach((label) =>
    header.append(workspaceElement("span", "", label))
  );

  list.append(header);

  experiments.forEach((experiment) => {
    const terminal = ["completed", "partial"].includes(experiment.status);

    const row = document.createElement(terminal ? "button" : "div");

    if (terminal) {
      row.type = "button";
      row.addEventListener(
        "click",
        () => void showDataset(experiment.job_id)
      );
    }

    row.className = `experiment-row${terminal ? " is-openable" : ""}`;

    row.append(
      workspaceElement(
        "strong",
        "experiment-recipe",
        recipeLabel(experiment.recipe)
      ),
      statusPill(experiment.status),
      workspaceElement(
        "span",
        "experiment-sessions",
        sessionSummary(experiment)
      ),
      workspaceElement(
        "time",
        "experiment-time",
        formatTimestamp(
          experiment.timestamp_end || experiment.timestamp_start
        ) || "—"
      )
    );

    list.append(row);
  });

  shell.append(list);
  return shell;
}

function renderTestbed(data) {
  const shell = workspaceElement("section", "workspace-panel testbed-panel");

  const connectionStatus = data.health.testbed_configured
    ? "configured"
    : "configuration_incomplete";

  const connectionLabel = data.health.testbed_configured
    ? "Configured"
    : "Configuration incomplete";

  shell.append(
    workspaceElement("p", "eyebrow", "TESTBED")
  );

  const heading = workspaceElement("div", "testbed-heading");

  heading.append(
    workspaceElement("h1", "workspace-heading", "osVBNG testbed"),
    workspaceElement(
      "span",
      `status-pill status-${connectionStatus.replaceAll("_", "-")}`,
      connectionLabel
    )
  );

  const checks = workspaceElement("div", "testbed-checks");

  const components = data.health.components || {};

  const componentList = [
    ["BNG", "bng"],
    ["BNG Blaster", "bng_blaster"],
    ["Prometheus", "prometheus"],
    ["Grafana", "grafana"],
    ["FRR / Core Router", "frr"],
  ];

  componentList.forEach(([name, key]) => {
    const row = workspaceElement("div", "testbed-check");

    let status = components[key] || "not_available";

    if (status === "running" || status === "available") {
      status = "available";
    } else {
      status = "not_available";
    }

    row.append(
      workspaceElement("span", "", name),
      statusPill(status)
    );

    checks.append(row);
  });

  shell.append(
    heading,
    checks,
    workspaceElement(
      "p",
      "last-checked",
      `Last checked: ${formatTimestamp(new Date().toISOString())}`
    )
  );

  return shell;
}

function setActiveWorkspace(view) {
  document.querySelectorAll(".workspace-nav").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
}

function parameterLabel(name) {
  const labels = {
    sessions: "the number of sessions required",
    start_sessions: "the starting session count",
    max_sessions: "the maximum session count",
    cpu_limit: "the CPU safety limit",
    cycles: "the flap/reconnect requirement",
  };
  return labels[name] || name.replaceAll("_", " ");
}

function selectionPrompt(recipe) {
  const parameters = recipe.parameters || {};
  const names = Object.keys(parameters);
  const selected = names.filter((name) => parameters[name]?.required);
  const requested = selected.length ? selected : names;
  const labels = requested.map(parameterLabel);
  const request = labels.length === 0
    ? "Please describe the parameters required."
    : labels.length === 1
      ? `Please enter ${labels[0]}.`
      : `Please enter ${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}.`;
  return `${recipe.display_name || recipeLabel(recipe.id)} selected. ${request}`;
}

function hideRecipeSelector() {
  const selector = document.querySelector("#recipe-selector");
  const toggle = document.querySelector("#recipes-toggle");
  selector.hidden = true;
  toggle.setAttribute("aria-expanded", "false");
}

async function persistRecipeSelection(recipeId = null) {
  const response = await fetch("/api/conversations/recipe-selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, recipe: recipeId }),
  });
  if (!response.ok) throw new Error("Unable to save the selected recipe context.");
}

async function selectRecipe(recipe) {
  try {
    await persistRecipeSelection(recipe.id);
    hideRecipeSelector();
    activateConversation(true, false, false);
    addMessage(selectionPrompt(recipe));
  } catch (error) {
    hideRecipeSelector();
    addMessage(error.message || "Unable to select that recipe.");
  }
}

function renderRecipeSelector(recipes) {
  const selector = document.querySelector("#recipe-selector");
  selector.replaceChildren();
  recipes.forEach((recipe) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "recipe-selector-option";
    option.setAttribute("role", "menuitem");
    option.textContent = recipe.display_name || recipeLabel(recipe.id);
    option.addEventListener("click", () => selectRecipe(recipe));
    selector.append(option);
  });
  if (!recipes.length) selector.append(workspaceElement("span", "recipe-selector-loading", "Recipes unavailable"));
}

async function toggleRecipeSelector() {
  const selector = document.querySelector("#recipe-selector");
  const toggle = document.querySelector("#recipes-toggle");
  const willOpen = selector.hidden;

  if (!willOpen) {
    selector.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    return;
  }

  selector.replaceChildren(
    workspaceElement("span", "recipe-selector-loading", "Loading recipes…")
  );
  selector.hidden = false;
  toggle.setAttribute("aria-expanded", "true");

  try {
    const response = await fetch("/api/capabilities", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Request failed (${response.status}).`);
    }

    const data = await response.json();
    const recipes = Array.isArray(data) ? data : (data.recipes || []);

    capabilityMap = new Map(
      recipes.map((recipe) => [recipe.id, recipe])
    );

    renderRecipeSelector(recipes);
  } catch (error) {
    selector.replaceChildren(
      workspaceElement(
        "span",
        "recipe-selector-loading",
        "Recipes unavailable"
      )
    );
  }
}

function activateConversation(reset = false, includeIntro = true, clearRecipeContext = true) {
  if (reset) activeResultJobId = null;
  if (!showingWorkspace && !reset) return;
  if (clearRecipeContext) void persistRecipeSelection().catch(() => { /* the empty chat remains usable */ });
  clearProgress();
  messages.replaceChildren();
  showingWorkspace = false;
  form.hidden = false;
  hideRecipeSelector();
  setActiveWorkspace("generation");
  if (includeIntro) addMessage("Describe the BNG dataset you need in plain English.");
  setBadge("Ready");
  input.focus();
}
let workspaceRequestId = 0;
async function showWorkspace(view) {
  const requestId = ++workspaceRequestId;
  if (view === "generation") { activateConversation(true); return; }
  showingWorkspace = true;
  hideRecipeSelector();
  clearProgress();
  messages.replaceChildren();
  form.hidden = true;
  setActiveWorkspace(view);
  const loading = workspaceElement("p", "workspace-loading", "Loading workspace…");
  messages.append(loading);
  try {
    const data = await getWorkspaceData(view);
    if (requestId !== workspaceRequestId) {
  return;
}
    const renderer = {
  dashboard: renderDashboard,
  experiments: renderExperiments,
  testbed: renderTestbed
}[view];
    messages.replaceChildren(renderer(data));
  } catch (error) {
    messages.replaceChildren(workspaceElement("p", "workspace-empty", error.message || "Workspace data is unavailable."));
  }
}

document.querySelector("#new-request").addEventListener("click", () => activateConversation(true));
document.querySelector("#recipes-toggle").addEventListener("click", toggleRecipeSelector);
document.querySelectorAll(".workspace-nav:not(#recipes-toggle)").forEach((button) => button.addEventListener("click", () => void showWorkspace(button.dataset.view)));
void initialiseHistory();


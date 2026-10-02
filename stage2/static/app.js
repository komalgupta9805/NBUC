const conversationId = `browser-${crypto.randomUUID()}`;
const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const badge = document.querySelector("#active-badge");
const historyContainer = document.querySelector("#dataset-history");
const storageKey = "bng-dataset-history-v1";
let progressCard = null;
let datasetHistory = [];

function addMessage(text, role = "assistant") { const item = document.createElement("article"); item.className = role; item.textContent = text; messages.append(item); messages.scrollTop = messages.scrollHeight; }
function setProgress(text) {
  if (!progressCard) { progressCard = document.createElement("article"); progressCard.className = "assistant progress"; progressCard.setAttribute("role", "status"); progressCard.setAttribute("aria-live", "polite"); const dot = document.createElement("span"); dot.className = "progress-dot"; dot.setAttribute("aria-hidden", "true"); const label = document.createElement("span"); label.className = "progress-text"; progressCard.append(dot, label); messages.append(progressCard); }
  progressCard.querySelector(".progress-text").textContent = text; messages.scrollTop = messages.scrollHeight;
}
function clearProgress() { progressCard?.remove(); progressCard = null; }
function setBadge(jobId) { if (badge) badge.textContent = jobId || "Ready"; }
function recipeLabel(recipe) { return recipe === "ipoe-bind" ? "IPoE Bind" : (recipe || "Dataset"); }
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
function rememberDataset(detail, jobId) { const metadata = detail.metadata || {}, config = metadata.config || {}; datasetHistory = mergeHistory(datasetHistory, [{ job_id: jobId, recipe: metadata.recipe || detail.recipe || "ipoe-bind", sessions: config.session_count || config.sessions || detail.session_count, timestamp: metadata.timestamp_end || metadata.timestamp || new Date().toISOString() }]); saveHistory(); renderHistory(); }
function metric(label, value) { const item = document.createElement("div"), heading = document.createElement("span"), result = document.createElement("strong"); heading.textContent = label; result.textContent = value ?? "—"; item.append(heading, result); return item; }
function renderResult(detail, jobId, replace = false) {
  clearProgress(); if (replace) messages.replaceChildren(); const summary = detail.summary || {}, metadata = detail.metadata || {}, config = metadata.config || {}; const card = document.createElement("section"); card.className = "completion"; const title = document.createElement("strong"); title.textContent = "Dataset generation completed"; const description = document.createElement("p"); description.textContent = `${recipeLabel(metadata.recipe || detail.recipe)} · ${config.session_count || config.sessions || detail.session_count || "—"} sessions`; const metrics = document.createElement("div"); metrics.className = "metrics"; metrics.append(metric("Success", summary.success_rate ? `${summary.success_rate}%` : "—"), metric("DHCP p50", summary.dhcp_p50_ms ? `${summary.dhcp_p50_ms} ms` : "—"), metric("DHCP p95", summary.dhcp_p95_ms ? `${summary.dhcp_p95_ms} ms` : "—"), metric("Peak CPU", summary.peak_cpu_percent ? `${summary.peak_cpu_percent}%` : "—")); const actions = document.createElement("div"); actions.className = "completion-actions"; [["Download dataset", `/api/jobs/${encodeURIComponent(jobId)}/dataset`], ["Download artifacts", `/api/jobs/${encodeURIComponent(jobId)}/artifacts`]].forEach(([label, href]) => { const link = document.createElement("a"); link.href = href; link.textContent = label; link.setAttribute("download", ""); actions.append(link); }); card.append(title, description, metrics, actions); messages.append(card); messages.scrollTop = messages.scrollHeight;
}
async function showDataset(jobId) { clearProgress(); setBadge(jobId); messages.replaceChildren(); setProgress("Loading dataset result…"); try { const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/result`, { cache: "no-store" }); if (!response.ok) throw new Error(`The saved result is unavailable (${response.status}).`); renderResult(await response.json(), jobId, true); } catch (error) { clearProgress(); addMessage(error.message || "Unable to load the saved dataset result."); } }
function stageMessage(stage) { return ({ queued: "Preparing testbed…", preparing_testbed: "Preparing testbed…", running_experiment: "Running experiment…", packaging_dataset: "Packaging dataset…", finalizing_dataset: "Finalizing dataset…" })[stage] || "Preparing dataset…"; }
async function poll(jobId) {
  try { const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" }); if (!response.ok) throw new Error("Unable to refresh job status."); const job = await response.json(); if (job.status === "completed") { clearProgress(); const result = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/result`, { cache: "no-store" }); if (!result.ok) throw new Error("Dataset completed, but its result could not be loaded."); const detail = await result.json(); rememberDataset(detail, jobId); renderResult(detail, jobId); return; } if (["failed", "rejected", "cancelled"].includes(job.status)) { clearProgress(); addMessage(job.reason || `Dataset generation ${job.status}.`); return; } setProgress(stageMessage(job.stage)); window.setTimeout(() => void poll(jobId), 1500); } catch (error) { setProgress(error.message || "Unable to refresh job status."); }
}
function addChoice(label, callback) { const button = document.createElement("button"); button.type = "button"; button.className = "choice"; button.textContent = label; button.addEventListener("click", callback); messages.append(button); }
async function send(text) {
  addMessage(text, "user"); input.value = "";
  try { const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, conversation_id: conversationId }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Request failed."); if (data.type === "accepted") { setBadge(data.job_id); setProgress("Preparing testbed…"); void poll(data.job_id); return; } addMessage(data.assistant_message || "I could not understand that request."); if (data.type === "confirmation") { addChoice("Yes", () => void send("yes")); addChoice("No", () => void send("no")); } } catch (error) { addMessage(error.message || "Request failed."); }
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
    success: summary.success_rate ?? detail.success_rate ?? (detail.failed_sessions === 0 ? 100 : undefined),
    p50: summary.dhcp_p50_ms ?? detail.dhcp_p50_ms ?? detail.setup_p50_ms,
    p95: summary.dhcp_p95_ms ?? detail.dhcp_p95_ms ?? detail.setup_p95_ms,
    cpu: summary.peak_cpu_percent ?? detail.peak_cpu_percent ?? detail.peak_bng_cpu,
    established: detail.established_sessions_blaster ?? detail.established_sessions,
    failed: detail.failed_sessions,
    offeredRate: parameters.offered_rate ?? config.offered_rate,
    durationSeconds,
    topology: metadata.topology || detail.topology || detail.normalized_intent?.topology,
    faults: detail.fault_labels_observed || summary.fault_labels_observed || [],
    artifacts: detail.artifact_paths || metadata.artifact_paths || {}
  };
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
  card.className = "completion";
  const title = document.createElement("strong");
  title.textContent = "Dataset generation completed";
  const description = document.createElement("p");
  description.textContent = `${recipeLabel(result.recipe)} · ${result.sessions || "—"} sessions`;
  const completedAt = document.createElement("span");
  completedAt.className = "completion-time";
  const completedTime = formatTimestamp(result.timestamp);
  completedAt.textContent = completedTime ? `Completed · ${completedTime}` : "Completed";
  const metrics = document.createElement("div");
  metrics.className = "metrics";
  metrics.append(
    metric("Success", result.success !== undefined ? `${result.success}%` : "—"),
    metric("DHCP p50", result.p50 ? `${result.p50} ms` : "—"),
    metric("DHCP p95", result.p95 ? `${result.p95} ms` : "—"),
    metric("Peak CPU", result.cpu ? `${result.cpu}%` : "—")
  );
  const runSummary = document.createElement("p");
  runSummary.className = "run-summary";
  const summaryBits = [];
  if (result.established !== undefined && result.sessions) summaryBits.push(`${result.established}/${result.sessions} established`);
  if (result.durationSeconds !== undefined) summaryBits.push(`${result.durationSeconds} sec`);
  if (result.offeredRate !== undefined) summaryBits.push(`${result.offeredRate} sessions/sec`);
  runSummary.textContent = summaryBits.length ? summaryBits.join("  ·  ") : "Run summary unavailable";
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
  addDetail("Faults observed", Array.isArray(result.faults) && result.faults.length ? result.faults.join(", ") : "None observed");
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
  card.append(title, description, completedAt, metrics, runSummary, actions, details);
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
void initialiseHistory();

# PRD — Stage 2: Conversational Dataset-Generation Platform for BNG Experiments
**Project:** Nokia–Bangalore University Collaboration — Generate customized quality datasets for computer networking projects
**Stage:** 2 (automation layer over the already-proven Stage 1 manual pipeline)
**Target audience of this document:** an AI coding agent (Codex) with repo + testbed access, executing with no further clarification
---

## 0. Non-negotiable ground rules (read first)

1. **Do not rewrite Stage 1.** `experiment-a`, `cupsbngresearch`, and `osvbng monitoring` (the folders the user uploads) contain working, testbed-proven scripts. Stage 2 **wraps and calls** them. It does not reimplement DHCP session logic, PCAP capture, or Prometheus export from scratch unless Phase-0 inspection proves a given recipe has no reusable equivalent.
2. **The LLM never executes anything.** It only produces a structured intent JSON. A deterministic validator checks that JSON against an allowlist before anything runs. No LLM output is ever interpolated into a shell command.
3. **This is a web chat application, not a CLI.** The user interacts entirely through a browser-based chat UI. A CLI entrypoint may exist underneath for testing, but it is never the product surface shown to the user or the mentor.
4. **No secrets or hosts in source code.** The testbed host (`98.87.8.109`, provided by the mentor) goes in an env var (`TESTBED_HOST`), never hardcoded, never committed.
5. **No fake demos.** If a recipe isn't implemented, the system says so explicitly (`IMPLEMENTATION_REQUIRED`) — it never fabricates results.
6. **Every non-success outcome must explain itself.** Partial, failed, and rejected results always carry a human-readable reason — never a bare status code with no explanation (Section 15).
7. **Phase 0 is mandatory and blocking.** Before writing a single line of Stage 2 code, Codex must inspect the uploaded repository and produce an inventory (Section 13) mapping existing files to the four recipes. Implementation does not start until this inventory exists.

---

## 1. Problem Statement

Networking researchers need datasets reflecting specific topologies, traffic patterns, and protocol behavior. Generic dataset repos (Kaggle, Google Dataset Search) don't fit. Physical testbeds are expensive and slow. Stage 1 proved that a virtualized Containerlab + osVBNG + BNG Blaster testbed can generate real, reproducible BNG datasets — but running it requires a human who knows Containerlab, Docker, BNG Blaster CLI flags, Prometheus queries, and PCAP tooling, and who manually triggers and babysits each run.

## 2. Project Goal

Let a user describe the dataset they want in plain English, through a chat window in a browser, and receive — with no manual intervention and no exposure to underlying tooling — a structured, reproducible dataset generated from a real BNG Containerlab experiment.

```
NATURAL LANGUAGE REQUEST (typed in a chat UI)
  → AUTOMATIC TOPOLOGY + RECIPE SELECTION
  → AUTOMATIC EXPERIMENT EXECUTION ON THE REAL TESTBED
  → AUTOMATIC ARTIFACT + METRIC COLLECTION
  → AUTOMATIC DATASET PACKAGING
  → CLEAN RESULT SHOWN IN THE CHAT UI, WITH A DOWNLOAD LINK
```

The chatbot UI is the interface. **The product is the dataset pipeline behind it.**

## 3. Users

- **Primary (demo 2):** the student team and the mentor, driving the web UI live in a demo from a browser.
- **Secondary (future):** networking researchers at Nokia/the university who want a BNG dataset without learning the toolchain.

## 4. User Journey (target experience)

The user opens a browser to the application's URL (served from the testbed, see Section 12.1) and sees a single-page chat interface.

```
┌──────────────────────────────────────────────────────────┐
│  BNG Dataset Generator                                     │
├──────────────────────────────────────────────────────────┤
│  You: I want a dataset for 30 IPoE dual-stack subscribers. │
│                                                              │
│  Assistant: I understood this as ipoe-bind (IPoE DHCP       │
│  dual-stack session binding), 30 sessions — within the      │
│  supported range (10–700). Shall I start? [Yes] [No]        │
│                                                              │
│  You: Yes                                                   │
│                                                              │
│  Assistant: ⏳ Running experiment… (preparing testbed)       │
│  Assistant: ⏳ Running experiment… (collecting artifacts)    │
│  Assistant: ✅ Dataset generation completed.                 │
│                                                              │
│    Recipe: IPoE DHCP Dual-Stack                             │
│    Requested sessions: 30                                   │
│    Established: 30/30                                       │
│    Success: 100%                                             │
│    DHCP setup p50: 184 ms                                    │
│    DHCP setup p95: 310 ms                                    │
│    Peak BNG CPU: 42%                                         │
│                                                              │
│    [⬇ Download dataset]   [⬇ Download artifacts]            │
├──────────────────────────────────────────────────────────┤
│  [ Type your request...                        ] [ Send ]  │
└──────────────────────────────────────────────────────────┘
```

No Containerlab/Docker/BNG Blaster/Prometheus/PCAP/Linux command is ever shown in the chat. The "Yes/No" confirmation and "[Download …]" buttons are real clickable UI elements, not text the user has to type back. While a job runs, the UI shows a short, friendly progress indicator (e.g. a spinner with a one-line status that updates: "Preparing testbed…" → "Running experiment…" → "Collecting artifacts…" → "Packaging dataset…"), never a scrolling raw log. The full raw log still exists on disk for debugging (Section 16) — it is just never rendered in the chat.

## 5. Supported Topology (demo 2 scope)

Exactly **one** topology is recognized: `osvbng` — the existing monolithic osVBNG Containerlab topology already running on the testbed. The topology registry is a single-entry allowlist (Section 10), structured so more topologies (from `clab-topo` / containerlab.dev examples) can be added later without changing the architecture. Anything the LLM maps outside `osvbng` → chat response: **"That use case is outside the currently supported scope."**

## 6. Supported Recipes

| ID | User intent (examples) | What actually runs | Session range | Artifacts produced |
|---|---|---|---|---|
| `ipoe-bind` | "IPoE DHCP dual-stack sessions", "bind N subscribers" | N sessions, no heavy traffic streams | 10–700 | `report.json`, access-side `.pcap`, osVBNG session dump |
| `ipoe-scale` | "how many sessions can it handle", "setup rate sweep" | sweep from a start count up toward a target, stop early if BNG CPU ≥ `cpu_limit` | start 10–700, target up to 700 | time series CSV: sessions-established vs CPU vs setup rate |
| `ipoe-flap` | "disconnect and reconnect", "session flap test" | establish → disconnect → reconnect cycle | 10–700 | `.pcap`, counters CSV, session timeline CSV |
| `radius-acct` | "radius accounting at scale" | bring sessions up, then capture RADIUS interim-accounting packets | 10–700 | RADIUS accounting `.pcap` |

Any request that matches `osvbng` but not one of these four → **"That use case is currently not implemented (future)."** — never silently defaulted to the nearest recipe.

### 6.1 Implementation order

Recipes are built **one at a time**, in this order, each gated by an exit condition rather than a fixed time budget — move to the next recipe as soon as the current one's exit condition (Section 22) is genuinely met, not on a calendar:

1. `ipoe-bind` — this is the hard gate for demo 2; the UI, API, LLM parser, validator, and job pipeline are all proven through this recipe first.
2. `ipoe-scale`
3. `ipoe-flap`
4. `radius-acct`

If a later recipe isn't reached before demo 2, it stays registered in the recipe registry with status `implementation_required` and the UI reports that status honestly (chat message: *"ipoe-scale is recognized but not yet implemented — this will be added in a future update."*) instead of pretending it ran.

## 7. Chat UI Behavior

- The user never needs to know recipe IDs, topology names, or parameter names — they type in plain English and the UI/LLM do the translation.
- Conversation loop: user message → LLM intent extraction → validator → (ask clarification | show confirm buttons | reject with reason) → on **Yes**, execute job in the background → chat shows a live progress indicator → chat shows the final clean summary card (Section 4 format) with download buttons.
- A user can keep chatting after a job completes — the conversation and chat history persist in the same session; starting a new request while a job is running is handled per Section 12.2 step 5 (one job at a time, with a clear "an experiment is already running" chat message if they try).
- The chat never shows raw tool output. All of that goes to a per-job log file (Section 16).

## 8. Clarification Flow

The LLM asks a clarification question **only** when intent confidence is genuinely ambiguous — not on every message. Rules:

- If topology is not `osvbng` with reasonable confidence → do not ask; reject immediately with the out-of-scope message (Section 5).
- If topology = `osvbng` but recipe is ambiguous between two of the four (e.g. "IPoE dataset" could be `ipoe-bind` or `ipoe-scale`) → the UI shows one short disambiguating question, ideally as clickable option buttons (e.g. `[Session binding] [Scaling] [Disconnect/reconnect]`) so the user doesn't have to retype.
- If recipe is resolved but a required parameter is missing or out of range → the UI asks exactly one follow-up for that parameter, stating the valid range, e.g.:
  > "How many sessions? Supported range for ipoe-bind is 10–700."
- Maximum **one open question per turn**. Never stack multiple questions in one message.
- After clarification resolves recipe + parameters, the chat shows the full structured request as a confirmation card with **Yes/No** buttons (Section 4) before any execution starts. This is the one and only confirmation gate.

## 9. Intent Schema (LLM output contract)

The LLM is prompted to return **only** this JSON shape (nothing else — no prose, no markdown fences). This is enforced by a JSON-schema validation step immediately after the LLM call; a malformed response triggers one retry with an error-correction prompt, then falls back to a clarification question to the user.

```json
{
  "topology": "osvbng",
  "recipe": "ipoe-bind",
  "confidence": "high",
  "parameters": {
    "sessions": 30,
    "offered_rate": 5,
    "duration": 60
  },
  "needs_clarification": false,
  "clarification_question": null,
  "out_of_scope": false
}
```

Rules enforced on this object by the validator (never trusted from the LLM alone):
- `topology` must be exactly `"osvbng"` or the request is rejected as out-of-scope — no other string is accepted even if the LLM invents one.
- `recipe` must be one of the four IDs in Section 6, or `null` if not yet resolved.
- `parameters` keys must be a subset of the allowed parameter set for that specific recipe (Section 9.1). Any unknown key is **dropped**, not passed through.
- If `needs_clarification` is true, `clarification_question` must be non-empty and the executor is never invoked.
- If `out_of_scope` is true, the executor is never invoked regardless of any other field.

### 9.1 Per-recipe parameter allowlist + validation ranges

| Recipe | Parameter | Type | Default | Valid range | Required? |
|---|---|---|---|---|---|
| `ipoe-bind` | `sessions` | int | 20 | 10–700 | yes |
| `ipoe-bind` | `offered_rate` | int (sessions/sec) | 5 | 1–50 | no |
| `ipoe-bind` | `duration` | int (seconds) | 60 | 30–600 | no |
| `ipoe-scale` | `start_sessions` | int | 50 | 10–700 | no |
| `ipoe-scale` | `max_sessions` | int | 200 | ≤ 700, must be ≥ `start_sessions` | no |
| `ipoe-scale` | `cpu_limit` | int (%) | 80 | 50–95 | no |
| `ipoe-flap` | `sessions` | int | 20 | 10–700 | no |
| `ipoe-flap` | `cycles` | int | 1 | 1–5 | no |
| `radius-acct` | `sessions` | int | 20 | 10–700 | no |
| `radius-acct` | `capture_duration` | int (seconds) | 120 | 30–600 | no |

These ranges are hardcoded in the **validator**, not left to the LLM's judgment. If a user-requested value is out of range, the validator rejects with a clarification turn stating the valid range, it never silently clamps without telling the user.

## 10. Topology / Recipe Registry (deterministic allowlists)

Two plain Python structures (dicts, or a small YAML file loaded at startup — Codex's choice, pick one and be consistent), never dynamically generated from LLM output:

```python
TOPOLOGY_REGISTRY = {
    "osvbng": {
        "containerlab_topology_file": "<path to existing .clab.yml, found in Phase 0>",
        "status": "supported",
    }
}

RECIPE_REGISTRY = {
    "ipoe-bind":   {"status": "implemented",            "module": "recipes.ipoe_bind"},
    "ipoe-scale":  {"status": "implementation_required", "module": "recipes.ipoe_scale"},
    "ipoe-flap":   {"status": "implementation_required", "module": "recipes.ipoe_flap"},
    "radius-acct": {"status": "implementation_required", "module": "recipes.radius_acct"},
}
```

`status` flips to `"implemented"` as each recipe (Section 6.1) actually completes and is tested on the real testbed — never flipped speculatively.

## 11. Validation Pipeline

```
LLM JSON  →  schema check  →  topology allowlist check  →  recipe allowlist check
          →  parameter key allowlist (drop unknown keys)  →  parameter range check
          →  recipe status check (reject with IMPLEMENTATION_REQUIRED if not "implemented")
          →  VALID STRUCTURED REQUEST → orchestrator
```

Every rejection at any stage produces a specific, honest, human-readable reason (Section 15) shown in the chat — never a generic error.

## 12. Architecture & Orchestration

### 12.1 Where it runs

The entire Stage 2 application (web server, chat UI, LLM client, validator, orchestrator, recipe executors) runs **directly on the testbed host** (`TESTBED_HOST`, currently `98.87.8.109`) and is reached by opening a browser to `http://<TESTBED_HOST>:<PORT>/`. Recipe executors call the existing Stage 1 scripts as **local subprocesses** on that same machine — the same way a human runs them today, just automated. **Decision and rationale:** this avoids building and securing an SSH-based remote-execution layer, which adds real complexity (key management, remote process monitoring, file transfer back) for no benefit when the web server can simply live where the experiment already runs. A separate control-plane-over-SSH architecture is noted as explicit future work (Section 20) if the app ever needs to be hosted off the testbed.

### 12.2 Stack

- **Backend:** FastAPI (Python). Chosen because it's lightweight, has built-in request validation (pairs naturally with the Pydantic-style intent schema in Section 9), and serves both the JSON API and the static chat UI from one process — minimal moving parts.
- **Frontend:** a single HTML page with vanilla JavaScript/CSS, served by FastAPI (`templates/` + `static/`). No React/Vue/heavy build step — the goal is a clean, working chat UI, not a frontend framework exercise. If the existing repo already uses a frontend framework elsewhere, Codex may reuse it instead; otherwise plain HTML/JS is the default.
- **Job execution:** background tasks (FastAPI `BackgroundTasks` or a simple in-process thread/queue — Codex's choice, document whichever is used) so the HTTP request that starts a job returns immediately and the UI polls or listens for progress rather than blocking.
- **Progress updates:** polling `GET /api/jobs/{job_id}` every 1–2 seconds is sufficient for the MVP; Server-Sent Events are optional nice-to-have, not required.

### 12.3 Backend API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | `{ "status": "ok", "llm_configured": true, "testbed_configured": true }` — never exposes secrets |
| `POST /api/chat` | Body: `{ "conversation_id": "...", "message": "..." }`. Returns one of: `needs_clarification` (with the question and optional button `options`), `confirm` (echoes the structured request for Yes/No), `accepted` (job started, returns `job_id`), `out_of_scope`, `not_implemented` — each with an `assistant_message` string to render directly in the chat |
| `GET /api/jobs/{job_id}` | Current status: `queued \| running \| completed \| partial \| failed \| rejected`, a short `stage` label (e.g. `"collecting_artifacts"`), and `progress_pct` |
| `GET /api/jobs/{job_id}/result` | Full structured result once terminal (Section 15.1 fields) |
| `GET /api/jobs/{job_id}/dataset` | Downloads `dataset.tar.gz` |
| `GET /api/jobs/{job_id}/artifacts` | Downloads the raw artifacts directory (zipped) |

### 12.4 Repository placement

Stage 2 code lives **inside the same existing project folder** the user uploads (e.g. as a `stage2/` subdirectory alongside `experiment-a/`), not in a separate new repository. This keeps everything Codex needs — existing scripts and new code — in one place it can reference with relative paths. Proposed layout (Codex may adapt names to match what Phase 0 finds, but should preserve this separation of concerns):

```
<project-root>/                 # the folder(s) the user uploads
├── experiment-a/                # existing, untouched
├── cupsbngresearch/...          # existing, untouched
├── osvbng-monitoring/...        # existing, untouched
└── stage2/                      # NEW — everything built for Stage 2
    ├── app.py                   # FastAPI entrypoint
    ├── config.py                # loads env vars, Section 18
    ├── llm_client.py            # Groq client wrapper
    ├── intent_parser.py         # Section 9
    ├── validator.py             # Section 11
    ├── conversation.py          # chat-session/clarification state
    ├── registry.py              # Section 10
    ├── job_manager.py           # Section 14
    ├── recipes/
    │   ├── ipoe_bind.py
    │   ├── ipoe_scale.py
    │   ├── ipoe_flap.py
    │   └── radius_acct.py
    ├── dataset/
    │   ├── collector.py
    │   ├── packaging.py
    │   └── metadata.py
    ├── templates/index.html
    ├── static/app.js, app.css
    ├── stage2-runs/              # job output directory, Section 15
    ├── .env.example
    └── requirements-stage2.txt
```

### 12.5 Job lifecycle (every recipe follows this; recipe-specific logic only fills in step 7)

1. Generate job ID: `JOB-<YYYYMMDD-HHMMSS>-<4 hex chars>`.
2. Persist the structured request + job ID to `stage2-runs/<job_id>/request.json`.
3. Re-validate parameters defensively (already validated before this point).
4. **Preflight / testbed health check** — reuse the existing `preflight.sh` wherever it already checks: containers up, Prometheus reachable, OSPF/OSPFv3 state, no BNG Blaster process already running. Do not duplicate checks that script already performs.
5. **Conflict check** — if another job is `running` (tracked via a lock file), reject the new job with a reasoned message: *"An experiment is already running (`<job_id>`), please wait for it to finish."* No two jobs ever run concurrently on the shared testbed.
6. Reset/prepare topology if the recipe requires a clean starting state (reuse existing reset logic if present; write it only if it doesn't exist).
7. **Execute recipe** — call the recipe module, which calls the existing Experiment-A scripts (for `ipoe-bind`) or newly-written equivalents (for the other three, once reached).
8. Monitor execution against a timeout (Section 14.3); if exceeded, kill the run, mark job `failed` with reason `"execution exceeded the configured timeout"`, and still collect whatever partial artifacts exist.
9. Collect artifacts into `stage2-runs/<job_id>/artifacts/` (PCAP, logs, Prometheus CSV, session dumps — reusing existing exporters).
10. Run the existing DHCP latency extractor and summary generator against the collected artifacts to produce `report.json` / `run_summary.csv`.
11. Build `stage2-runs/<job_id>/metadata.json` (Section 15.2), including a `reason` field if status is not `completed`.
12. Package everything into `stage2-runs/<job_id>/dataset.tar.gz`.
13. Clean up: release the lock, reset transient session state so the testbed is ready for the next job.
14. Mark final job status in `request.json`/`metadata.json`.
15. The chat UI's polling picks up the terminal status and renders the final summary card (Section 4) or the reasoned failure message (Section 15).

### 12.6 Safety limits (enforced by the orchestrator, independent of what the LLM/user requested)

- Hard cap: no recipe may request more than **700 sessions** regardless of parameter validation above (defense in depth).
- Hard execution timeout: 10 minutes per job for `ipoe-bind`/`ipoe-flap`/`radius-acct`, 20 minutes for `ipoe-scale` (sweeps take longer, and run longer still at higher session counts — both are configurable via env vars, not hardcoded as magic numbers in logic).
- CPU safety stop in `ipoe-scale`: if BNG container CPU reported by Prometheus crosses `cpu_limit` (default 80%) mid-sweep, stop increasing load immediately, finish collecting the current data point, then proceed to artifact collection as a `partial` result with reason `"stopped early: BNG CPU reached the configured safety limit"` — this is a graceful stop, not a crash.
- Only one job runs at a time (Section 12.5 step 5).

## 13. Phase 0 Deliverable — Existing Code Inventory (must be produced before any new code is written)

Codex must inspect the uploaded folders (`cupsbngresearch`, `experiment-a`, `osvbng monitoring`) and produce `PHASE0_INVENTORY.md` containing, at minimum:

- Full file tree of all uploaded folders.
- For each of: `experiment-a.env`, `preflight.sh`, `run_experiment_a.sh`, `export_prometheus.py`, `extract_dhcp_latency.py`, `summarize_experiment.py` — confirm it exists, note its exact CLI interface (args/env vars it reads), and note what it outputs.
- The exact path to the existing `.clab.yml` Containerlab topology file for osVBNG, and the BNG/subscriber/core-router container and image names in use.
- The exact structure of a real `experiment-a-results/<timestamp>/rate_XXX/rep_XX/` directory from a prior run, with real field names from `metadata.json`, `blaster-report.json`, `session_dataset.csv`.
- An explicit mapping table: **recipe → existing script(s) that already implement all or part of it → what's missing**. For `ipoe-bind` this should show it maps almost 1:1 onto `run_experiment_a.sh` at a configurable session count. For the other three, state plainly what doesn't exist yet.
- Whether the repo is under git (for the `script_version` metadata field in Section 15.2) — if yes, record how to read the current commit hash programmatically; if no, that field is omitted, not faked.

This file is itself a required deliverable, not just an internal note — it's what makes every other section of this PRD grounded rather than assumed.

## 14. Dataset Generation & Metrics

### 14.1 Job status values (must distinguish these explicitly, never collapse to a single "success/fail")

- `completed` — recipe ran fully, all expected artifacts collected, no fault conditions detected.
- `partial` — recipe ran but some sessions failed / some artifacts missing / execution was cut short by timeout or CPU safety stop; dataset is still kept and returned, clearly labeled, **with a reason**.
- `failed` — infrastructure failure (testbed unreachable, container down, Prometheus down) or the recipe process crashed before producing usable data; **with a reason**.
- `rejected` — never reached execution (out-of-scope topology, not-implemented recipe, out-of-range parameters after clarification); **with a reason**.

### 14.2 Critical correctness rule (explicitly called out in the project brief — do not violate)

`osvbng_subscriber_sessions_active` (the Prometheus counter) **must never be treated as "successfully established dual-stack sessions."** It can include partially-created sessions. The authoritative count of established sessions comes from **BNG Blaster's own session counters / `blaster-report.json`**. `report.json` in every recipe's output must carry both numbers separately (`active_sessions_prometheus` and `established_sessions_blaster`) rather than conflating them.

### 14.3 Anomalies are data, not noise

Known anomaly classes (incomplete session establishment, stale sessions, VPP reply mismatch errors, DHCP latency retry tails, active-vs-established counter divergence) must be preserved in the output artifacts with a `fault_label` field per the existing per-session schema (Section 14.4) — never filtered out or silently dropped.

### 14.4 Per-session dataset fields (reuse existing schema, extend only if Phase 0 shows gaps)

`run_id, timestamp, session_id, subscriber_id, outer_vlan, inner_vlan, stack_type, setup_ms, success, dhcp_nak, active_sessions_at_start, bng_cpu_at_start, bng_memory_at_start, offered_rate, scenario, fault_label`

### 14.5 Run-summary fields (reuse existing schema)

`run_id, offered_rate, requested_sessions, established_sessions, failed_sessions, success_percent, setup_p50_ms, setup_p95_ms, peak_active_sessions, peak_bng_cpu, duration_seconds, status`

## 15. Reasoned Outcomes (new requirement — applies to every non-`completed` status)

Every API response and every chat message that reports `partial`, `failed`, or `rejected` must include a plain-English `reason` — never just the status word. This applies at both the API level (`metadata.json` / `GET /api/jobs/{job_id}/result`) and the chat UI level (the `assistant_message` shown to the user).

### 15.1 Required fields on every job result

```json
{
  "status": "partial",
  "reason": "stopped early: BNG CPU reached the configured safety limit (80%) at 540 sessions",
  "...": "plus all other fields from Section 15.2"
}
```

### 15.2 Example reasons by status (Codex should use specific, concrete wording like these — not generic placeholders)

| Status | Example reason |
|---|---|
| `rejected` (out of scope) | `"osvbng is the only supported topology; this request did not match it."` |
| `rejected` (not implemented) | `"ipoe-scale is recognized but not yet implemented for this demo."` |
| `rejected` (out of range) | `"Requested 900 sessions, which exceeds the supported maximum of 700 for ipoe-bind."` |
| `failed` (infra) | `"Preflight check failed: Prometheus was unreachable at job start."` |
| `failed` (timeout) | `"Execution exceeded the 10-minute timeout and was terminated; partial logs preserved."` |
| `partial` (CPU stop) | `"Stopped early: BNG CPU reached the configured safety limit (80%) at 540 of 700 requested sessions."` |
| `partial` (session failures) | `"27 of 30 sessions established; 3 failed DHCPv6 binding — see fault_label in session_dataset.csv for details."` |

## 16. Artifact Management

### 16.1 `metadata.json` (reproducibility record — required fields)

```json
{
  "request_id": "JOB-20261001-143200-ab12",
  "user_request_text": "I want a dataset for 30 IPoE dual-stack subscribers.",
  "normalized_intent": { "...": "the validated structured request from Section 9" },
  "topology": "osvbng",
  "recipe": "ipoe-bind",
  "parameters": { "sessions": 30, "offered_rate": 5, "duration": 60 },
  "timestamp_start": "2026-10-01T14:32:00Z",
  "timestamp_end": "2026-10-01T14:34:12Z",
  "testbed_host_env_var": "TESTBED_HOST",
  "experiment_version": "experiment-a",
  "script_git_commit": "<if available from Phase 0, else omitted>",
  "offered_rate": 5,
  "requested_sessions": 30,
  "established_sessions": 30,
  "active_sessions_prometheus": 31,
  "established_sessions_blaster": 30,
  "failed_sessions": 0,
  "setup_p50_ms": 184,
  "setup_p95_ms": 310,
  "peak_bng_cpu": 42,
  "status": "completed",
  "reason": null,
  "fault_labels_observed": [],
  "artifact_paths": { "dataset": "dataset.tar.gz", "artifacts_dir": "artifacts/" }
}
```

### 16.2 Directory layout (extends the existing `experiment-a-results` convention — does not replace it)

```
stage2/stage2-runs/
  JOB-20261001-143200-ab12/
    request.json
    metadata.json
    artifacts/
      report.json
      blaster-report.json
      access.pcap
      osvbng_session_dump.json
      session_dataset.csv
      run_summary.csv
      prometheus/*.csv
      logs/                 # full raw logs — never shown in chat, only referenced
    dataset.tar.gz           # "[Download dataset]" button target
```

- Failed/partial datasets are **never deleted** — same rule as Stage 1.

## 17. Logging

- Every job gets one log file: `stage2-runs/<job_id>/artifacts/logs/job.log`, containing the full verbose trace (every subprocess call, every check). This is what a developer debugs from.
- The chat UI shows only: the short progress indicator during execution (Section 4), then the final clean summary or the reasoned failure message (Section 15) — never raw log content.

## 18. Security

- LLM is never permitted to produce or trigger shell commands — its only output is the JSON in Section 9, and that JSON's only path to execution is through the fixed allowlist in Sections 10–11.
- Recipe executors call a **fixed, small set of pre-approved scripts/functions** per recipe — never a dynamically-constructed command string built from user or LLM text.
- All secrets (`GROQ_API_KEY`, `TESTBED_HOST`, `TESTBED_USER`, `TESTBED_PROJECT_PATH`) come from environment variables / a `.env` file that is **git-ignored**, never from source.
- `GET /api/health` reports configuration status only (`true`/`false`), never the actual values of secrets.
- No SSH/remote-execution mode is built for demo 2 (Section 12.1) — explicit future work if the app needs to run off-testbed.

## 19. Configuration (env vars — define in `stage2/.env.example`, real `.env` is git-ignored)

```
LLM_PROVIDER=groq
GROQ_API_KEY=
LLM_MODEL=llama-3.3-70b-versatile

TESTBED_HOST=98.87.8.109
TESTBED_USER=
TESTBED_PROJECT_PATH=/home/ubuntu/cupsbngresearch

MAX_SESSIONS_HARD_CAP=700
DEFAULT_JOB_TIMEOUT_SECONDS=600
SCALE_JOB_TIMEOUT_SECONDS=1200

APP_HOST=0.0.0.0
APP_PORT=8000

JOBS_OUTPUT_DIR=./stage2-runs
```

## 20. MVP Scope (demo 2) vs Future Scope

**In scope for demo 2:**
- Web chat UI (FastAPI + plain HTML/JS), Groq LLM integration, intent schema + validator, topology=osvbng only, recipe registry with all 4 recipes registered.
- `ipoe-bind` fully implemented end-to-end on the real testbed (hard requirement).
- `ipoe-scale`, `ipoe-flap`, `radius-acct` implemented as far as time allows, each gated by its own exit condition, not a time budget; anything not reached stays honestly marked `implementation_required` in the UI.
- Clean final summary card matching Section 4, with working download buttons.
- Every non-`completed` outcome shown with a concrete reason (Section 15).

**Explicitly out of scope for demo 2 (future work):**
- Remote orchestration over SSH from a separate control-plane host.
- Multi-topology support beyond `osvbng` (the broader `clab-topo` / containerlab.dev universe).
- Multi-tenant / concurrent job execution.
- Cloud upload / shareable links for datasets (S3 etc.) — browser download from the app's own host is sufficient for now.
- User accounts / authentication on the chat UI.

## 21. Acceptance Criteria (demo 2)

1. Opening the app's URL in a browser and typing a natural-language IPoE dual-stack request results in a real `ipoe-bind` job executing on the real testbed (not a simulated/mocked result).
2. A request for Kubernetes, OSPF, or any non-BNG topology is correctly rejected with a reason, via the chat UI, no execution attempted.
3. A request for a BNG recipe outside the 4 supported ones is correctly rejected as not-implemented, with a reason.
4. An ambiguous IPoE request triggers exactly one clarifying question, not a wall of questions.
5. A request for more than 700 sessions (or otherwise out of a recipe's range) triggers a clarification/rejection stating the valid range, not a silent clamp or a crash.
6. On completion, the chat shows **only** the clean summary card from Section 4 plus working download buttons — no raw tool logs rendered in the UI.
7. `stage2-runs/<job_id>/` contains a valid `metadata.json`, a `dataset.tar.gz`, and the artifacts listed in Section 16.2.
8. `report.json` correctly distinguishes `active_sessions_prometheus` from `established_sessions_blaster` (Section 14.2).
9. A deliberately-failed run (e.g. kill the BNG Blaster process mid-run) still produces a preserved, clearly-labeled `failed` or `partial` dataset with a specific, human-readable `reason` — nothing silently deleted, nothing reported as a bare status with no explanation.
10. No testbed IP, credential, or secret appears anywhere in the committed source code, and `/api/health` never leaks secret values.

## 22. Implementation Phases (for Codex to follow in order — each phase begins only once the previous phase's exit condition is met; no phase is time-boxed)

| Phase | Deliverable | Exit condition |
|---|---|---|
| 0 | `PHASE0_INVENTORY.md` (Section 13) | Every existing script's interface and output documented; recipe→script mapping table complete |
| 1 | FastAPI skeleton + static chat UI page; LLM client (Groq); intent schema validator; topology/recipe registries; clarification loop — **no execution wired yet**, `/api/chat` tested with a mocked executor that just echoes the structured request back in the chat | Opening the app in a browser and typing 10+ sample requests (in-scope and out-of-scope) correctly renders the right chat response for each, with zero unhandled exceptions |
| 2 | `ipoe-bind` recipe executor wired to real `run_experiment_a.sh` + collectors; full job lifecycle (Section 12.5); dataset packaging; progress polling wired into the UI | A real job run, started from the browser, produces a correct `dataset.tar.gz` from a live testbed run, and the chat shows the Section 4 summary card with working download buttons; acceptance criteria 1, 6–8, 10 pass |
| 3 | Failure handling + safety limits (Sections 12.6, 15, 17); acceptance criteria 5, 9 verified with deliberately broken runs | Partial/failed/rejected runs always show a specific reason in the chat, and artifacts are preserved |
| 4 | `ipoe-scale` recipe | Sweep runs to completion or CPU-safety-stops gracefully with a `partial` + reason, time series produced |
| 5 | `ipoe-flap` recipe | Flap cycle produces a session timeline, shown and downloadable from the chat |
| 6 | `radius-acct` recipe | RADIUS accounting PCAP captured after session bring-up, shown and downloadable from the chat |

---

*End of PRD. Phase 0 output (`PHASE0_INVENTORY.md`) should be the very next artifact produced, before any Stage 2 code is written.*

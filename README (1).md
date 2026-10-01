# NBUC — Natural-Language BNG Dataset Generation

NBUC turns a plain-English request into a real, reproducible BNG (Broadband Network Gateway) dataset. A user types what they need — *"Create a dual-stack IPoE dataset with 20 sessions"* — and the system interprets the request, runs the corresponding experiment on a live Containerlab/osVBNG testbed, and returns a packaged dataset with no manual Containerlab/Docker/BNG Blaster/Prometheus/PCAP commands required.

This is a **Stage 2** project: it automates an already-proven manual experiment pipeline (Stage 1, "Experiment A") rather than replacing it. All real experiment execution still runs through the original Stage 1 scripts — Stage 2 adds the natural-language front end, safety validation, and automatic dataset packaging around them.

## How it works

```
User (browser, natural language)
  │
  ▼
Chat UI  ──────────────────────────────  stage2/templates, stage2/static
  │  POST /api/chat
  ▼
LLM Intent Parser (Groq)  ─────────────  stage2/intent_parser.py
  │  structured intent JSON
  ▼
Deterministic Validator  ──────────────  stage2/validator.py
  │  topology + recipe allowlist, parameter range checks
  ▼
Job Manager  ───────────────────────────  stage2/job_manager.py
  │  job lifecycle: preflight → execute → collect → package
  ▼
Remote BNG Testbed (over SSH)
  │
  ├── Containerlab (osvbng01 topology)
  ├── osVBNG
  ├── BNG Blaster
  ├── FRR core router
  ├── Prometheus / Grafana
  └── PCAP capture
  │
  ▼
Dataset package (tar.gz + artifacts) ──  stage2/stage2-runs/<job-id>/
  │
  ▼
Chat UI — result summary + download links
```

The LLM **never** executes anything directly — it only produces a structured intent (topology, recipe, parameters). A deterministic validator checks that intent against a fixed allowlist before any experiment runs. All execution goes through predefined, reviewed recipe code calling the existing Stage 1 scripts.

### Architecture note

The Stage 2 application runs as a local control plane (currently a developer's machine) and reaches the remote testbed over SSH, rather than running directly on the testbed itself. This was decided during implementation once it was clear a trusted SSH key was only available locally — it keeps the testbed itself untouched by application deployment and makes it easy to point the same codebase at a different testbed later by changing `.env`.

## Current Recipes

| Recipe | Purpose | Status |
|---|---|---|
| `ipoe-bind` | Dual-stack IPoE session establishment dataset | ✅ Implemented, tested end-to-end on the real testbed |
| `ipoe-scale` | Session setup-rate scaling, swept toward a target load | 🔜 Planned |
| `ipoe-flap` | Session disconnect/reconnect behaviour | 🔜 Planned |
| `radius-acct` | RADIUS interim accounting capture at scale | 🔜 Planned |

Any request that doesn't map to the `osvbng` topology or one of the four recipes above is rejected with a clear, specific reason — the system never guesses or fabricates a result for something it doesn't support. Supported session counts: **10–700** per recipe.

Every completed job reports two distinct session counts that must not be confused: `active_sessions_prometheus` (can include partially-created sessions) and `established_sessions_blaster` (the authoritative count from BNG Blaster). Non-successful jobs (`partial`, `failed`, `rejected`) always include a concrete, human-readable reason, not just a status code.

## Technology Stack

Python · FastAPI · HTML/CSS/JavaScript (no frontend framework) · Groq LLM API · Containerlab · osVBNG · BNG Blaster · FRRouting · Prometheus · Grafana · PCAP · SSH

## Project Structure

```
NBUC/
├── README.md
├── RUNBOOK.md
├── PRD.md
├── PHASE0_INVENTORY.md
├── osvbng01.clab.yml
├── bng1/
├── corerouter1/
├── subscribers/
├── experiment-a/                 # existing Stage 1 automation — reused, not rewritten
├── experiment-a-results/         # raw Stage 1 experiment output (git-ignored)
└── stage2/
    ├── app.py                    # FastAPI entrypoint
    ├── config.py                 # env var / .env loading
    ├── conversation.py           # chat session + clarification state
    ├── intent_parser.py          # LLM call + intent schema
    ├── validator.py              # deterministic allowlist + parameter range checks
    ├── registry.py               # topology/recipe registries
    ├── job_manager.py            # job lifecycle, preflight, locking
    ├── recipes/
    │   └── ipoe_bind.py          # (ipoe_scale.py, ipoe_flap.py, radius_acct.py — planned)
    ├── templates/                # chat UI HTML
    ├── static/                   # chat UI CSS/JS
    ├── stage2-runs/              # per-job output (git-ignored)
    ├── .env.example
    └── requirements-stage2.txt
```

## Stage 2 API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Reports whether the LLM and testbed are configured — never returns secret values |
| `POST /api/chat` | Send a natural-language message, get back a clarification question, a confirmation, a started job, or a rejection with reason |
| `GET /api/jobs/{job_id}` | Poll current job status/progress |
| `GET /api/jobs/{job_id}/result` | Full structured result once the job reaches a terminal state |
| `GET /api/jobs/{job_id}/dataset` | Download the packaged dataset (`dataset.tar.gz`) |
| `GET /api/jobs/{job_id}/artifacts` | Download the raw artifacts (zipped) |

The chat UI is served by the same FastAPI application — there is no separate frontend server.

## Example

```
Create a dual-stack IPoE dataset with 20 sessions.
```

1. The LLM parses this as `topology=osvbng`, `recipe=ipoe-bind`, `sessions=20`.
2. The validator confirms the topology/recipe are supported and the session count is in range.
3. The chat shows the parsed intent and asks for confirmation.
4. On confirmation, the job manager runs preflight checks on the remote testbed, executes the experiment via the existing Stage 1 scripts, and collects artifacts.
5. The dataset is packaged and the chat shows a result summary (sessions requested/established, success %, DHCP setup p50/p95, peak BNG CPU) with download links.

See `RUNBOOK.md` for the exact commands to run this yourself.

## Documentation

- [`PRD.md`](./PRD.md) — full product requirements and system specification.
- [`RUNBOOK.md`](./RUNBOOK.md) — setup, running experiments, testbed checks, recovery, and troubleshooting.
- [`PHASE0_INVENTORY.md`](./PHASE0_INVENTORY.md) — inventory of the existing Stage 1 scripts this project builds on.

## Security

Never commit:

```
.env
*.pem
*.key
.venv/
.DS_Store
experiment-a-results/
stage2/stage2-runs/
```

All testbed hosts, paths, SSH key locations, and API keys are configured through `.env` (see `stage2/.env.example`), never hardcoded in source.

## Development Workflow

New recipes are developed in feature branches and merged into `main` after they're verified against the real testbed:

```
feature/ipoe-scale
feature/ipoe-flap
feature/radius-acct
```

## Project Goal

```
Natural language
      │
      ▼
Validated recipe + parameters
      │
      ▼
Automated experiment on the real BNG testbed
      │
      ▼
Reproducible dataset, with full metadata on how it was generated
```

The goal is reproducible BNG dataset generation without anyone having to hand-construct Containerlab, Docker, or BNG Blaster commands for each run.

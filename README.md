# NBUC — Natural-Language BNG Dataset Generation

NBUC turns a plain-English request into a real, reproducible BNG (Broadband Network Gateway) dataset. A user types what they need — _"Create a dual-stack IPoE dataset with 20 sessions"_ — and the system interprets the request, runs the corresponding experiment on a live Containerlab/osVBNG testbed, and returns a packaged dataset with no manual Containerlab/Docker/BNG Blaster/Prometheus/PCAP commands required.

## How it works

```
User (browser, natural language)
  │
  ▼
Chat UI  ──────────────────────────────  stage2/templates, stage2/static
  │  POST /api/chat
  ▼
LLM Intent Parser (Groq + fallback) ────  stage2/intent_parser.py
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
Chat UI — results, downloads + analysis assistant
```

### Architecture note

The application reaches the remote testbed over SSH, rather than running directly on the testbed itself. This was decided during implementation once it was clear a trusted SSH key was only available locally — it keeps the testbed itself untouched by application deployment and makes it easy to point the same codebase at a different testbed later by changing `.env`.

## Current Recipes

| Recipe       | Purpose                                                                        | Status                                |
| ------------ | ------------------------------------------------------------------------------ | ------------------------------------- |
| `ipoe-bind`  | Dual-stack IPoE session establishment dataset                                  | tested end-to-end on the real testbed |
| `ipoe-scale` | Session setup-rate scaling, swept toward a target load with a CPU safety limit |
| `ipoe-flap`  | Session disconnect/reconnect behaviour                                         |

Any request that doesn't map to the `osvbng` topology or one of the three implemented recipes above is rejected with a clear, specific reason — the system never guesses or fabricates a result for something it doesn't support. Supported session counts: **10 and above** for implemented recipes (subject to the configured hard cap). Scale additionally accepts a CPU limit of **50–95%**;

Every completed job reports two distinct session counts that must not be confused: `active_sessions_prometheus` (can include partially-created sessions) and `established_sessions_blaster` (the authoritative count from BNG Blaster). Non-successful jobs (`partial`, `failed`, `rejected`) always include a concrete, human-readable reason, not just a status code.

## Technology Stack

Python · FastAPI · HTML/CSS/JavaScript (no frontend framework) · Groq LLM API (with rule-based fallback) · Containerlab · osVBNG · BNG Blaster · FRRouting · Prometheus · Grafana · PCAP · SSH

## Project Structure

```
NBUC/
├── README.md
├── doc/
│   ├── RUNBOOK.md
│   ├── NBUC_Stage2_PRD.md
│   └── PHASE0_INVENTORY.md
├── osvbng01.clab.yml
├── bng1/
├── clab-osvbng01/
├── corerouter1/
├── subscribers/
├── stage2/
│   ├── app.py                    # FastAPI entrypoint
│   ├── config.py                 # env var / .env loading
│   ├── conversation.py           # chat session + clarification state
│   ├── intent_parser.py          # LLM + deterministic fallback
│   ├── validator.py              # deterministic allowlist + parameter checks
│   ├── registry.py               # topology/recipe registries
│   ├── job_manager.py            # job lifecycle, preflight, locking
│   ├── result_analyzer.py        # recipe-aware results analysis
│   ├── recipes/
│   │   ├── ipoe_bind.py
│   │   ├── ipoe_scale.py
│   │   └── ipoe_flap.py
│   ├── templates/                # UI HTML
│   ├── static/                   # UI CSS/JS
│   ├── stage2-runs/              # per-job output (git-ignored)
│   ├── .env.example
│   └── requirements-stage2.txt
└── tests/                        # automated tests
```

The experiment scripts are expected on the remote testbed; they are not included in this repository archive.

## API

| Endpoint                                   | Purpose                                                                                                                       |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/health`                          | Reports application configuration and testbed health without exposing secrets                                                 |
| `GET /api/capabilities`                    | Lists supported topology, recipes, and parameters                                                                             |
| `GET /api/experiments`                     | Lists experiment records                                                                                                      |
| `GET /api/metrics/subscribers`             | Retrieves subscriber monitoring metrics                                                                                       |
| `POST /api/conversations/recipe-selection` | Selects a recipe in a conversation                                                                                            |
| `POST /api/chat`                           | Send a natural-language message, get back a clarification question, a confirmation, a started job, or a rejection with reason |
| `GET /api/jobs/{job_id}`                   | Poll current job status/progress                                                                                              |
| `GET /api/jobs/{job_id}/result`            | Full structured result once the job reaches a terminal state                                                                  |
| `GET /api/jobs/{job_id}/dataset`           | Download the packaged dataset (`dataset.tar.gz`)                                                                              |
| `GET /api/jobs/{job_id}/artifacts`         | Download the raw artifacts (zipped)                                                                                           |

## Example

```
Create a dual-stack IPoE dataset with 20 sessions.
```

1. The LLM parses this as `topology=osvbng`, `recipe=ipoe-bind`, `sessions=20`.
2. The validator confirms the topology/recipe are supported and the session count is in hardcap range.
3. The chat shows the parsed intent and asks for confirmation.
4. On confirmation, the job manager runs preflight checks on the remote testbed, executes the experiment via the existing scripts, and collects artifacts.
5. The dataset is packaged and the chat shows a result summary (sessions requested/established, success %, DHCP setup p50/p95, peak BNG CPU) with download dataset links as csv and json.

The UI also supports recipe selection, experiment history, monitoring views, and follow-up questions about recorded results. Generated artifacts depend on the recipe: Bind produces session datasets and access captures; Scale produces load/CPU time-series data; Flap produces a session timeline, counters, and initial/per-cycle packet captures.

See [`doc/RUNBOOK.md`](./doc/RUNBOOK.md) for setup, virtual environment, `.env`, SSH, and startup instructions.

## Documentation

- [`NBUC_Stage2_PRD.md`](./doc/NBUC_Stage2_PRD.md) — full product requirements and system specification.
- [`RUNBOOK.md`](./doc/RUNBOOK.md) — setup, running experiments, testbed checks, recovery, and troubleshooting.
- [`PHASE0_INVENTORY.md`](./doc/PHASE0_INVENTORY.md) — inventory of the existing Stage 1 scripts this project builds on.

## Security

All testbed hosts, paths, SSH key locations, and API keys are configured through `.env` (see `stage2/.env.example`), never hardcoded in source.

## Development Workflow

New recipes and improvements can be developed in feature branches and merged into `main` after appropriate validation and testing:

```
feature/<recipe-or-improvement>
```

## Testing

Run the repository tests with:

```bash
python -m unittest discover -s tests -v
```

Some tests use mocks and do not replace live testbed verification.

## Future Scope — Beyond BNG

The current implementation demonstrates the framework on an AWS-hosted, virtualized BNG/IPoE testbed. Its core pipeline—**natural-language intent parsing, deterministic validation, controlled recipe execution, and automated dataset collection**—is designed to be reusable across other network environments.

Potential extensions include:

- **5G Core and RAN experiments:** Add 5G-specific topologies, recipes, validation rules, and telemetry collectors to study UE registration, PDU session establishment and recovery, mobility/handover, and network slicing, including the behavior of components such as AMF, SMF, and UPF.
- **Larger and multi-topology networks:** Extend orchestration and monitoring to support multiple gateways, distributed network components, and higher subscriber loads, with appropriate resource management and safety controls.
- **Broader experiment coverage:** Introduce additional validated recipes and artifact collectors as new network technologies and research requirements are supported.

These are **future directions, not currently implemented capabilities**. Each new environment would require its own testbed integration and tested recipes, while retaining the existing application pipeline.

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

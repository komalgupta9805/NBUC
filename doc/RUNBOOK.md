# NBUC Runbook

Everything needed to set up, run, test, troubleshoot, and recover the NBUC Stage 2 system end-to-end.

---

## 1. Environments

NBUC has two separate environments:

1. **Local control plane** — your development machine. Runs the Stage 2 FastAPI application and the chat UI.
2. **Remote BNG testbed** — the mentor-provided host. Runs Containerlab, osVBNG, BNG Blaster, FRR, Prometheus/Grafana, and the original Stage 1 (Experiment A) scripts.

The Stage 2 application runs locally and reaches the testbed over SSH to run preflight checks and trigger experiments. It never requires you to SSH in manually for a normal run — only for setup, inspection, and recovery.

| What | Where |
|---|---|
| Local project root | `/Users/<you>/Downloads/cupsbngresearch` (adjust to your actual path) |
| Local Stage 2 app | `<project-root>/stage2` |
| Remote project root | `/home/ubuntu/cupsbngresearch` |
| Remote Experiment A | `/home/ubuntu/cupsbngresearch/experiment-a` |
| Remote topology file | `/home/ubuntu/cupsbngresearch/osvbng01.clab.yml` |

---

## 2. One-Time Setup

### 2.1 Local Python environment

```bash
cd <project-root>
python3 -m venv .venv
source .venv/bin/activate
pip install -r stage2/requirements-stage2.txt
```

### 2.2 Environment configuration

Copy the template and fill in real values — **never commit the result**:

```bash
cp stage2/.env.example stage2/.env
```

```text
# LLM
LLM_PROVIDER=groq
GROQ_API_KEY=<your Groq API key — console.groq.com, free tier>
LLM_MODEL=llama-3.3-70b-versatile

# Testbed
TESTBED_HOST=<testbed IP>
TESTBED_USER=ubuntu
TESTBED_SSH_KEY_PATH=<path to the .pem file you were given>
TESTBED_PROJECT_PATH=/home/ubuntu/cupsbngresearch

# Safety limits
MAX_SESSIONS_HARD_CAP=700
```

If you rotate the Groq key for any reason (e.g. it was accidentally shared somewhere), revoke the old one at console.groq.com and update `.env` — nothing else needs to change.

### 2.3 Confirm SSH access to the testbed

```bash
ssh -i <path-to-pem> ubuntu@<testbed-ip>
```

If this fails, nothing downstream will work — fix SSH access before doing anything else. Once confirmed, exit back to your local shell; the app will use the same key path from `.env`.

---

## 3. Starting the Application

```bash
cd <project-root>
source .venv/bin/activate
cd stage2
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open the app in a browser at:

```
http://localhost:8000
```

**Important:** always open the real running address above. Opening a local HTML file directly (a `file://...` path, or a downloaded copy of the page) will load unstyled, non-functional markup — the CSS and the chat's API calls both depend on being served by the running FastAPI process.

### Health check

```bash
curl http://localhost:8000/api/health
```

Expect:

```json
{ "status": "ok", "llm_configured": true, "testbed_configured": true }
```

If either flag is `false`, fix the corresponding `.env` value before trying to run an experiment — the UI will reject job requests until both are `true`.

---

## 4. Remote Testbed Checks

### 4.1 Preflight (run before every experiment, or let the app run it automatically)

```bash
ssh -i <path-to-pem> ubuntu@<testbed-ip>
cd /home/ubuntu/cupsbngresearch/experiment-a
./preflight.sh
```

A healthy testbed ends with:

```
Preflight passed.
```

Preflight checks: host commands, Containerlab topology state, BNG container, subscriber container, Prometheus reachability and targets, BNG Blaster idle state, OSPF, OSPFv3. **Do not start an experiment if preflight fails** — the Stage 2 job manager enforces this automatically, but it's worth knowing what it's checking when troubleshooting.

### 4.2 Confirm the Experiment A script is in place

```bash
ssh -i <path-to-pem> ubuntu@<testbed-ip> \
  'cd /home/ubuntu/cupsbngresearch/experiment-a && ls -lah run_experiment_a.sh && test -x run_experiment_a.sh && echo SCRIPT_EXECUTABLE'
```

---

## 5. Running an Experiment (normal path)

1. Open `http://localhost:8000`.
2. Type a request in plain English, e.g.:
   ```
   Create a dual-stack IPoE dataset with 20 sessions.
   ```
3. Confirm the parsed intent when the UI asks (Yes/No).
4. The UI progresses through: *Preparing testbed… → Running experiment… → Collecting artifacts… → Packaging dataset… → Completed.*
5. On completion, the chat shows a result card (requested/established sessions, success %, DHCP setup p50/p95, peak BNG CPU) with **Download dataset** / **Download artifacts** buttons.

### Known-good `ipoe-bind` examples

```
Create a dual-stack IPoE dataset with 10 sessions.
Create a dual-stack IPoE dataset with 20 sessions.
```

Start new/unfamiliar changes with a small session count (10–20) as a pilot before running larger ones — it's the cheapest way to catch a wiring or config issue before it costs a long run.

---

## 6. Manual Experiment A Run (bypass Stage 2 — troubleshooting only)

Use this only to isolate whether a problem is in Stage 2 or in the underlying Stage 1 script itself. The normal workflow should always go through the Stage 2 UI.

```bash
ssh -i <path-to-pem> ubuntu@<testbed-ip>
cd /home/ubuntu/cupsbngresearch/experiment-a

./run_experiment_a.sh \
  --yes \
  --rates 5 \
  --repetitions 1 \
  --sessions 20 \
  --hold-seconds 60 \
  --cooldown-seconds 0 \
  --scenario stage2_ipoe_bind \
  --fault-label none
```

Results land under `/home/ubuntu/cupsbngresearch/experiment-a-results/`, with artifacts like `access.pcap`, `blaster-report.json`, `session_dataset.csv`, `run_summary.csv`, `metadata.json`.

---

## 7. Inspecting Results and Logs

### Stage 2 job output (local)

```
stage2/stage2-runs/<JOB-ID>/
├── request.json
├── metadata.json
└── artifacts/
    ├── report.json
    ├── session_dataset.csv
    ├── dataset.tar.gz
    └── logs/job.log
```

```bash
job=stage2/stage2-runs/<JOB-ID>
sed -n '1,260p' "$job/artifacts/logs/job.log"
```

Completed jobs and their downloads remain available even after restarting the app — job state is persisted to disk, not held only in memory.

### Reading a result honestly

`metadata.json` always distinguishes `active_sessions_prometheus` from `established_sessions_blaster` — treat the Prometheus number as a loose upper bound, not proof of success. Any job that isn't `completed` carries a `reason` field explaining exactly why (timeout, CPU safety stop, a specific failed check, an out-of-range parameter) — if you ever see a bare `failed`/`partial` status with no reason attached, that's a bug worth reporting, not expected behavior.

A `fault_label` on an individual session (e.g. `dhcpv4_discover_retry`) is evidence-based — cross-check it against the underlying BNG Blaster counters in `blaster-report.json` before trusting a summary number like p95 at face value, especially on small session counts where one retried session can dominate the percentile.

---

## 8. Testbed Recovery

### 8.1 OSPF / OSPFv3 not Full, or preflight otherwise failing

Do **not** assume the requested session count caused it. Redeploy the full topology:

```bash
ssh -i <path-to-pem> ubuntu@<testbed-ip>

sudo containerlab redeploy \
  --topo /home/ubuntu/cupsbngresearch/osvbng01.clab.yml \
  --keep-mgmt-net \
  --graceful
```

Wait ~20–30 seconds, then:

```bash
cd /home/ubuntu/cupsbngresearch/experiment-a
./preflight.sh
```

Retry the experiment only after preflight passes again.

### 8.2 Never restart individual Containerlab nodes

```bash
# Don't do this:
sudo docker restart clab-osvbng01-corerouter1
```

Individually restarting a node can desync it from the rest of the topology in ways `containerlab redeploy` avoids. Always use the full redeploy command in §8.1 instead.

### 8.3 Stale or already-running BNG Blaster

Preflight checks for this automatically and will block a new run if one is detected. If an experiment is genuinely still running, let it finish rather than forcing a new one — Stage 2's job manager also enforces one-job-at-a-time at the application level.

### 8.4 Preserving evidence during recovery

If a job fails and you need to recover the testbed, the failed job's artifacts and logs stay on disk under `stage2/stage2-runs/<JOB-ID>/` — recovery (redeploy) never deletes them. Don't manually clean these up; they're often the only record of what went wrong.

---

## 9. Git Workflow

### Never commit

```
.env
*.pem
*.key
.venv/
.DS_Store
experiment-a-results/
stage2/stage2-runs/
```

```bash
git status
git status --short --ignored    # double-check nothing secret is staged
```

### Feature branches

```bash
git checkout -b feature/ipoe-scale
# ...implement, test against the real testbed...
git add .
git commit -m "Implement ipoe-scale"
git push -u origin feature/ipoe-scale
```

Open a pull request into `main` once the recipe has been verified end-to-end against the real testbed — not just unit-tested against a mock.

---

## 10. Current Status

### Completed

- [x] Stage 2 FastAPI application + chat UI
- [x] Natural-language intent parsing (Groq)
- [x] Deterministic validation (topology/recipe allowlist, parameter ranges, 700-session cap)
- [x] Remote testbed execution over SSH
- [x] `ipoe-bind` — implemented and tested end-to-end
- [x] Dataset packaging + result/artifact downloads
- [x] Evidence-based fault labeling (e.g. DHCP retry detection)
- [x] Restart-safe job persistence

### Planned

- [ ] `ipoe-scale`
- [ ] `ipoe-flap`
- [ ] `radius-acct`

---

## 11. Quick Demo Script

```bash
# 1. Confirm the testbed
ssh -i <path-to-pem> ubuntu@<testbed-ip>
cd /home/ubuntu/cupsbngresearch/experiment-a
./preflight.sh        # must say: Preflight passed.
exit

# 2. Start Stage 2 locally
cd <project-root>
source .venv/bin/activate
cd stage2
uvicorn app:app --host 0.0.0.0 --port 8000

# 3. Open http://localhost:8000 and submit:
#    "Create a dual-stack IPoE dataset with 20 sessions."
```

---

## 12. Troubleshooting Checklist

| Symptom | Check |
|---|---|
| `GET /api/health` shows `llm_configured: false` | `GROQ_API_KEY` missing/invalid in `stage2/.env` |
| `GET /api/health` shows `testbed_configured: false` | `TESTBED_HOST`/`TESTBED_SSH_KEY_PATH` missing or wrong in `stage2/.env` |
| UI loads unstyled, Send does nothing | You're viewing a local file, not `http://localhost:8000` — open the real served URL |
| Job stuck/fails at preflight | SSH into the testbed and run `./preflight.sh` manually; if OSPF/OSPFv3 isn't Full, see §8.1 |
| A session's setup time looks anomalous | Check `fault_label` in `session_dataset.csv` against `blaster-report.json` before assuming a bug (see §7) |
| "An experiment is already running" | Expected — Stage 2 only runs one job at a time; wait for it to finish or check for a stuck BNG Blaster process (§8.3) |

---

## 13. Execution Principle

```
LLM
 │  interprets language, never executes
 ▼
Structured intent
 │
 ▼
Deterministic validator
 │  topology + recipe allowlist, parameter bounds
 ▼
Allowlisted recipe
 │  fixed, reviewed code only
 ▼
Controlled experiment on the real testbed
```

The LLM never generates or executes arbitrary shell commands. Only validated, predefined recipes are permitted to run against the BNG testbed.

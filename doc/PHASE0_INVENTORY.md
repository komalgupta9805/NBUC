# Phase 0 — Existing Code Inventory

This inventory was produced before Stage 2 application code. It records only
what is present in the uploaded folders on 2026-10-01.

## Uploaded file tree

```text
cupsbngresearch/
├── osvbng01.clab.yml
├── bng1/osvbng.yaml
├── corerouter1/daemons
├── corerouter1/frr.conf
├── subscribers/config.json
└── clab-osvbng01/
    ├── ansible-inventory.yml
    ├── authorized_keys
    ├── nornir-simple-inventory.yml
    ├── topology-data.json
    └── bng1/osvbng.yaml

experiment-a/
├── README.md
├── INTERN_EXPERIMENT_A_TEST_PLAN.md
├── experiment-a.env
├── preflight.sh
├── run_experiment_a.sh
├── export_prometheus.py
├── extract_dhcp_latency.py
└── summarize_experiment.py

osvbng-monitoring/
├── docker-compose.yml
├── prometheus.yml
├── grafana-dashboards.yml
├── grafana-datasource.yml
└── dashboards/osvbng.json
```

## Existing Stage 1 automation

| File | Exists | Interface / environment read | Output / responsibility |
|---|---:|---|---|
| `experiment-a/experiment-a.env` | Yes | Shell variables: `CLAB_TOPOLOGY`, `BNG_CONTAINER`, `BLASTER_CONTAINER`, `BLASTER_CONFIG`, `PROMETHEUS_URL`, `SESSION_COUNT`, `RATES`, `REPETITIONS`, `RUN_TIMEOUT_SECONDS`, `SETUP_GRACE_SECONDS`, `HOLD_SECONDS`, `COOLDOWN_SECONDS`, `SCRAPE_STEP_SECONDS`, `PROMETHEUS_PADDING_SECONDS`, `ENABLE_PCAP`, `RESULTS_DIR`, `SCENARIO`, `FAULT_LABEL`. | Default configuration consumed by the scripts below. It is a Stage 1 file and contains testbed-specific values; Stage 2 must not copy those values into committed source. |
| `experiment-a/preflight.sh` | Yes | No arguments. Reads `ENV_FILE` (default: sibling `experiment-a.env`). | Validates commands, topology/config readability, BNG/Blaster/Prometheus/cAdvisor/node-exporter containers, absence of an active BNG Blaster, Prometheus health/targets, and OSPF/OSPFv3 Full neighbors. Exits non-zero with `FAIL:` reasons. |
| `experiment-a/run_experiment_a.sh` | Yes | Options: `--dry-run`, `--yes`, `--unpaced`, `--instrumentation on\|off`, `--rates "..."`, `--repetitions N`, `--sessions N`, `--hold-seconds N`, `--cooldown-seconds N`, `--scenario LABEL`, `--fault-label LABEL`; reads `ENV_FILE`. | Runs Experiment A via BNG Blaster, invokes preflight before each run, captures PCAP/logs/Blaster JSON, exports Prometheus, derives the session CSV and appends `run_summary.csv`. Its `--sessions` and fixed script options make it the reusable `ipoe-bind` executor. |
| `experiment-a/export_prometheus.py` | Yes | Required: `--url`, `--start`, `--end`, `--step`, `--output-dir`. | Writes `queries.json` plus CSVs for active/total/released sessions, setup rate, osVBNG CPU/memory, and host CPU/memory. Every CSV begins `timestamp,value` followed by series labels. |
| `experiment-a/extract_dhcp_latency.py` | Yes | Required: `--pcap`, `--metadata`, `--prometheus-dir`, `--output`. | Parses PCAP/PCAPNG DHCPv4 Discover-to-ACK timing and writes `session_dataset.csv`. Fields: `run_id,timestamp,session_id,subscriber_id,outer_vlan,inner_vlan,stack_type,setup_ms,success,dhcp_nak,active_sessions_at_start,bng_cpu_at_start,bng_memory_at_start,offered_rate,scenario,fault_label`. |
| `experiment-a/summarize_experiment.py` | Yes | Required: `--run-dir`, `--summary-csv`. | Reads Stage 1 `metadata.json`, BNG Blaster report, Prometheus exports, and session CSV; appends `run_summary.csv` with `run_id,offered_rate,requested_sessions,established_sessions,failed_sessions,success_percent,setup_p50_ms,setup_p95_ms,peak_active_sessions,peak_bng_cpu,duration_seconds,status`. |

`run_experiment_a.sh` already uses BNG Blaster session counters to decide when
the requested count is established. Its summary script currently has a
fallback to the Prometheus active-session metric if Blaster data is missing;
Stage 2 must not use that fallback as authoritative establishment success.

## Topology and monitoring

The osVBNG Containerlab file is
`/Users/komalgupta/Downloads/cupsbngresearch/osvbng01.clab.yml`.

| Node | Container name used by Stage 1 | Image |
|---|---|---|
| BNG | `clab-osvbng01-bng1` | `veesixnetworks/osvbng:v0.3.1` |
| Subscribers / BNG Blaster | `clab-osvbng01-subscribers` | `veesixnetworks/bngblaster:0.9.30` |
| Core router | `clab-osvbng01-corerouter1` (Containerlab naming convention; not referenced directly by Stage 1) | `frrouting/frr:v8.4.1` |

`subscribers/config.json` configures IPoE access with DHCP and DHCPv6 enabled,
including IA-NA, IA-PD, and IPv6. `osvbng-monitoring/docker-compose.yml`
defines `mon-prometheus`, `mon-cadvisor`, and `mon-node-exporter`, which are
the monitoring containers checked by preflight.

## Prior-run structure

No `experiment-a-results/` directory or prior-run artifact was included in
the accessible uploads, so actual prior values cannot be claimed as observed.
The following structure and fields are derived from the producing scripts and
README, and must be confirmed against the first live testbed run:

```text
experiment-a-results/<UTC timestamp>/
├── rate_005/rep_01/
│   ├── metadata.json
│   ├── blaster-config.json
│   ├── blaster-report.json
│   ├── blaster.log
│   ├── blaster.stdout.log
│   ├── osvbng.log
│   ├── access.pcap
│   ├── session_dataset.csv
│   └── prometheus/
│       ├── queries.json
│       └── *.csv
└── run_summary.csv
```

Produced `metadata.json` fields are `run_id`, `experiment`, `scenario`,
`fault_label`, `offered_rate`, `requested_sessions`, `repetition`,
`start_epoch`, `start_utc`, `hold_seconds`, `timeout_seconds`,
`instrumentation`, `pcap_enabled`, `status`, `bng_container`,
`blaster_container`, and after execution `end_epoch`, `end_utc`, and
`duration_seconds`. The Blaster report is generated with `-J` and contains
the BNG Blaster counters/session details consumed by the summary script;
its exact JSON shape requires a real run artifact to document.

## Recipe mapping

| Stage 2 recipe | Reusable implementation | Gap to complete recipe |
|---|---|---|
| `ipoe-bind` | `run_experiment_a.sh` runs configurable IPoE dual-stack session counts and rates; `preflight.sh`, Prometheus exporter, DHCP extractor, and summary generator supply artifacts. | Stage 2 needs a fixed-argument safe wrapper, artifact normalization/packaging, report that keeps Prometheus active sessions separate from Blaster-established sessions, job lifecycle, and web UI. |
| `ipoe-scale` | Experiment A can run a static matrix of rates/session counts and already exports CPU/setup metrics. | No controlled incremental sweep, Prometheus CPU safety stop, or scale time-series orchestration exists. |
| `ipoe-flap` | Experiment A safely starts and stops sessions and retains PCAP/counters. | No establish-disconnect-reconnect cycle counter or session timeline collector exists. |
| `radius-acct` | Subscriber configuration and general PCAP capture exist. | No RADIUS accounting configuration, interim-accounting capture, or accounting-specific artifact collector exists. |

## Git status

Each uploaded folder is inside a Git worktree rooted at
`/Users/komalgupta/Downloads`, but that worktree has no `HEAD` commit yet
(`git rev-parse HEAD` fails). Therefore Stage 2 must omit `script_git_commit`
instead of inventing it. Once a commit exists, it can be read with
`git -C <project-root> rev-parse HEAD`.

## Implementation implication

Phase 1 can be implemented locally without testbed access. Phase 2 must be
validated on the testbed because this local workspace contains neither the
live Docker/Containerlab runtime nor a prior artifact fixture.

from __future__ import annotations

import os
import csv
import json
from pathlib import Path

from groq import Groq


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []

    try:
        with path.open(newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file))
    except (OSError, csv.Error):
        return []


def load_result_evidence(job: dict) -> dict:
    directory = Path(job["directory"])
    artifacts = directory / "artifacts"

    recipe = job.get("intent", {}).get("recipe")

    evidence = {
        "recipe": recipe,
        "report": _load_json(artifacts / "report.json"),
        "blaster_report": {},
        "sessions": [],
    }

    if recipe == "ipoe-bind":
        evidence["blaster_report"] = _load_json(
            artifacts / "blaster-report.json"
        )
        evidence["sessions"] = _load_csv(
            artifacts / "session_dataset.csv"
        )

    elif recipe == "ipoe-flap":
        evidence["timeline"] = _load_csv(
            artifacts / "session_timeline.csv"
        )

        evidence["counters"] = _load_csv(
            artifacts / "counters.csv"
        )

        evidence["blaster_reports"] = {}

        initial_report = artifacts / "initial-blaster-report.json"

        if initial_report.exists():
            evidence["blaster_reports"]["initial"] = _load_json(
                initial_report
            )

        for cycle in range(1, 100):
            report_path = artifacts / f"cycle-{cycle}-blaster-report.json"

            if not report_path.exists():
                break

            evidence["blaster_reports"][f"cycle-{cycle}"] = _load_json(
                report_path
            )
    elif recipe == "ipoe-scale":
        evidence["scale_timeseries"] = _load_csv(
            artifacts / "scale_timeseries.csv"
        )

        evidence["scale_points"] = {}

        scale_points_dir = artifacts / "scale-points"

        if scale_points_dir.exists():
            for point_dir in scale_points_dir.iterdir():
                if not point_dir.is_dir():
                    continue

                run_summary = next(
                    point_dir.glob("attempt-*/run_summary.csv"),
                    None,
                )

                if run_summary:
                    rows = _load_csv(run_summary)
                    if rows:
                        evidence["scale_points"][point_dir.name] = rows[0]
    return evidence


def analyze_ipoe_bind(evidence: dict) -> dict:
    sessions = evidence["sessions"]
    blaster_report = evidence["blaster_report"]

    session_details = []

    blaster_sessions = (
        blaster_report
        .get("report", {})
        .get("sessions", [])
    )

    blaster_by_id = {
        str(session.get("session-id")): session
        for session in blaster_sessions
    }

    for row in sessions:
        session_id = str(row.get("session_id", ""))

        try:
            setup_ms = float(row.get("setup_ms", 0))
        except (TypeError, ValueError):
            continue

        detail = {
            "session_id": session_id,
            "setup_ms": setup_ms,
            "fault_label": row.get("fault_label", "none"),
        }

        blaster_session = blaster_by_id.get(session_id)

        if blaster_session:
            detail["dhcp_tx_discover"] = blaster_session.get(
                "dhcp-tx-discover"
            )
            detail["dhcp_rx_nak"] = blaster_session.get(
                "dhcp-rx-nak"
            )

        session_details.append(detail)

    session_details.sort(
        key=lambda session: session["setup_ms"],
        reverse=True,
    )

    report = evidence["report"]

    return {
        "experiment": {
            "requested_sessions": report.get("requested_sessions"),
            "established_sessions": report.get("established_sessions"),
            "failed_sessions": report.get("failed_sessions"),
            "success_percent": report.get("success_percent"),
            "setup_p50_ms": report.get("setup_p50_ms"),
            "setup_p95_ms": report.get("setup_p95_ms"),
            "achieved_setup_rate": report.get("achieved_setup_rate"),
            "peak_active_sessions": report.get("peak_active_sessions"),
            "status": report.get("status"),
        },
        "session_count": len(session_details),
        "slowest_sessions": session_details[:5],
    }
def analyze_ipoe_flap(evidence: dict) -> dict:
    report = evidence["report"]
    timeline = evidence.get("timeline", [])
    counters = evidence.get("counters", [])

    cycles = []

    from datetime import datetime

    for cycle in range(1, int(report.get("cycles", 0)) + 1):

        disconnected = next(
            (
                event
                for event in timeline
                if str(event.get("cycle")) == str(cycle)
                and event.get("event") == "disconnected"
            ),
            None,
        )

        reconnected = next(
            (
                event
                for event in timeline
                if str(event.get("cycle")) == str(cycle)
                and event.get("event") == "reconnected"
            ),
            None,
        )

        if not disconnected or not reconnected:
            continue

        disconnected_time = datetime.fromisoformat(
            disconnected["timestamp"]
        )

        reconnected_time = datetime.fromisoformat(
            reconnected["timestamp"]
        )

        reconnect_time = (
            reconnected_time - disconnected_time
        ).total_seconds()

        cycles.append(
            {
                "cycle": cycle,
                "disconnected_sessions": int(
                    disconnected.get("established_sessions", 0)
                ),
                "reconnected_sessions": int(
                    reconnected.get("established_sessions", 0)
                ),
                "reconnect_time_seconds": round(
                    reconnect_time,
                    3,
                ),
            }
        )

    return {
        "experiment": {
            "requested_sessions": report.get(
                "requested_sessions"
            ),
            "cycles_requested": report.get("cycles"),
            "status": report.get("status"),
        },
        "cycles": cycles,
        "cycle_count": len(cycles),
    }

def analyze_ipoe_scale(evidence: dict) -> dict:
    report = evidence["report"]
    scale_points = evidence.get("scale_points", {})

    points = []

    for sessions, summary in scale_points.items():
        try:
            point = {
                "sessions": int(sessions),
                "requested_sessions": int(
                    summary.get("requested_sessions", 0)
                ),
                "established_sessions": int(
                    summary.get("established_sessions", 0)
                ),
                "failed_sessions": int(
                    summary.get("failed_sessions", 0)
                ),
                "success_percent": float(
                    summary.get("success_percent", 0)
                ),
                "setup_p50_ms": float(
                    summary.get("setup_p50_ms", 0)
                ),
                "setup_p95_ms": float(
                    summary.get("setup_p95_ms", 0)
                ),
                "peak_active_sessions": int(
                    summary.get("peak_active_sessions", 0)
                ),
                "peak_bng_cpu": float(
                    summary.get("peak_bng_cpu", 0)
                ),
                "duration_seconds": float(
                    summary.get("duration_seconds", 0)
                ),
                "status": summary.get("status"),
            }
        except (TypeError, ValueError):
            continue

        points.append(point)

    points.sort(key=lambda point: point["sessions"])

    return {
        "experiment": {
            "start_sessions": report.get("start_sessions"),
            "max_sessions": report.get("max_sessions"),
            "cpu_limit": report.get("cpu_limit"),
            "points_completed": report.get("points_completed"),
            "completed_scale_points": report.get(
                "completed_scale_points", []
            ),
            "failed_scale_point": report.get("failed_scale_point"),
            "peak_bng_cpu": report.get("peak_bng_cpu"),
            "established_sessions": report.get(
                "established_sessions_blaster"
            ),
            "active_sessions": report.get(
                "active_sessions_prometheus"
            ),
            "status": report.get("status"),
            "preflight_failures": report.get("preflight_failures", 0),
            "recovery_attempts": report.get("recovery_attempts", 0),
            "recovery_outcome": report.get("recovery_outcome"),
        },
        "scale_points": points,
    }
def analyze_result(evidence: dict) -> dict:
    recipe = evidence.get("recipe")

    if recipe == "ipoe-bind":
        return analyze_ipoe_bind(evidence)

    if recipe == "ipoe-flap":
        return analyze_ipoe_flap(evidence)

    if recipe == "ipoe-scale":
        return analyze_ipoe_scale(evidence)

    return {
        "session_count": len(
            evidence.get("sessions", [])
        ),
        "slowest_sessions": [],
    }


def explain_result(
    evidence: dict,
    analysis: dict,
    api_key: str,
    question: str,
) -> str:

    client = Groq(api_key=api_key)

    prompt = f"""
You are analyzing a BNG/IPoE experiment.

Answer the user's question using ONLY the derived evidence below.

IMPORTANT:
You must distinguish between:
1. What the evidence directly shows.
2. What can reasonably be associated with an observation.
3. What the evidence does NOT establish.

Never turn an observed correlation into a causal explanation.

User's question:
{question}

Derived experiment analysis:
{json.dumps(analysis, indent=2)}

Rules for evidence:

- If a session has dhcp_tx_discover = 2 and fault_label =
  dhcpv4_discover_retry, this establishes that the session transmitted
  two DHCP DISCOVER messages and was classified as a DHCP DISCOVER retry.

- This does NOT establish why the first DISCOVER was retried.

- This does NOT establish that the retry itself caused the setup delay.

- If a session has a high setup time and also has a DHCP retry,
  say that the retry is associated with the high setup time.

- Do NOT say that the retry "caused", "resulted in", "led to", or
  "caused the delay" unless the supplied evidence explicitly establishes
  that causal relationship.

- Do NOT invent packet-level timing, timeout values, round-trip times,
  network behavior, or reasons for a retry.

- If the evidence does not establish the cause, explicitly say:
  "The available evidence does not establish the cause."

- Do not infer a cause merely because one event happened alongside another.

P50 and P95:

- p50 is the median session setup time.
- p95 is the setup time below which approximately 95% of session
  setup times fall.
- Do not describe p95 as "the slowest 5%".
- A small number of unusually slow sessions can make p95 much higher
  than p50.

IPoE Flap experiments:

- For an ipoe-flap experiment, "reconnect time" means the elapsed
  time between the recorded disconnected event and the recorded
  reconnected event for that cycle.

- Describe reconnect_time_seconds as an observed recovery duration,
  not as setup latency.

- disconnected_sessions is the number of established sessions
  recorded at the disconnect event.

- reconnected_sessions is the number of established sessions
  recorded at the reconnect event.

- If all requested sessions are reconnected in every cycle, say that
  the evidence shows all requested sessions were re-established after
  each flap.

- If the question asks which cycle was slowest, compare the
  reconnect_time_seconds values and identify the cycle with the
  largest value.

- If the question asks how long a particular cycle took, give the
  reconnect duration for that cycle and mention how many sessions
  were re-established when relevant.

- Do not call reconnect_time_seconds "p50", "p95", "latency", or
  "setup time".

- Do not infer why a cycle took longer merely because its observed
  reconnect duration was larger.

- If the evidence only shows that one cycle took longer than another,
  explicitly say that the evidence does not establish why.

- Do not say that sessions "failed" merely because the timeline has
  a disconnected event. A flap experiment intentionally disconnects
  sessions as part of the test.

- Do not interpret a disconnected event with established_sessions = 0
  as evidence that sessions were permanently lost. Check the following
  reconnected event.

- When discussing the overall experiment, mention the requested
  session count, number of cycles, whether the requested sessions were
  re-established after each cycle, and completion status only when
  relevant to the user's question.

IIPoE Scale evidence:

- A scale point is supported only when its corresponding scale-point evidence
  is present.
- Use the actual completed scale points reported by the experiment.
- Use the scale point's own run summary for its success count, setup times,
  CPU, duration, and status.
- Use overall report fields only when discussing overall experiment facts.
- A recovered preflight failure must not be described as a failed scale point
  when the scale point subsequently completed successfully.
- If the evidence records a preflight failure and recovery, report exactly
  that sequence.
- Do not infer why a preflight failed or why recovery was required.
- Do not infer why setup time, CPU, duration, or any other metric changed
  between scale points.

When discussing slow sessions:

- Use the actual session IDs.
- Use the actual setup times.
- Use the actual fault labels.
- Use the actual BNG Blaster counters.
- Do not invent missing values.

When discussing retries:

- Count one retry for a session when dhcp_tx_discover = 2.
- If three sessions each have dhcp_tx_discover = 2, that means three
  retry attempts were observed.
- Do not call the retry the cause of the setup delay unless the evidence
  explicitly proves this.

Evidence boundaries:

- Use ONLY information contained in the supplied derived experiment analysis.
- Do not introduce facts, measurements, explanations, or assumptions that are
  not present in the derived evidence.
- Do not use general knowledge to fill an evidence gap.
- If the evidence does not contain the information needed to answer the
  question, explicitly say that the available evidence does not provide it.
- Do not infer a cause from correlation or from two events occurring together.
- Do not invent packet-level behavior, timing, retries, failures, resource
  bottlenecks, or network conditions.
- When describing a metric, use the actual value from the evidence.
- When comparing values, only compare values that are actually present.
- Preserve the meaning of the recorded metric; do not rename one metric as
  another.
- Do not claim that an experiment failed if the evidence says it completed.
- Do not claim that a session failed merely because it was intentionally
  disconnected during an IPoE flap experiment.
- If evidence is contradictory or insufficient, state that explicitly rather
  than resolving the contradiction by assumption.

Answer style:

- Start directly with the answer.
- Be conversational and technically precise.
- Explain things in simple technical language.
- Do NOT use Markdown tables.
- Do NOT use report-style headings.
- Prefer 1–3 short sentences unless asked so.
Use bullets only when listing multiple specific sessions or scale points.
- Answer only what the user asked.
- Do not give the entire experiment summary unless the user asks for it.
- Do not repeat information unnecessarily.
- Bold only important values or terms.
- Do not expose internal evidence field names such as
  "reconnect_time_seconds" in the answer.
- Use natural terms such as "reconnect time" or
  "recovery time".
-- Do not repeat the same observation or conclusion.
- For simple factual questions, prefer 1–2 sentences.
- If the cause is unknown, state that once and stop.
- Do not mention that you are an AI.

Most importantly:
If the evidence shows that two things happened together but does not
prove that one caused the other, explicitly state that distinction.
"""

    response = client.chat.completions.create(
        model=os.getenv(
            "LLM_MODEL",
            "openai/gpt-oss-120b",
        ),
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0,
    )

    return response.choices[0].message.content.strip()

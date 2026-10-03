from __future__ import annotations

import csv
import json
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from .ipoe_bind import (
    _ssh_base,
    recover_remote_testbed,
    run_remote_preflight,
    validate_remote_project,
)


SCALE_STEP = 50
ARTIFACT_COPY_ATTEMPTS = 3
ARTIFACT_COPY_RETRY_DELAY_SECONDS = 3
ARTIFACT_COPY_TIMEOUT_SECONDS = 120
PREFLIGHT_TIMEOUT_SECONDS = 90
RECOVERY_TIMEOUT_SECONDS = 300


def _sweep_points(start_sessions: int, max_sessions: int) -> list[int]:
    """Return deterministic scale points, always including max_sessions."""
    points = list(range(start_sessions, max_sessions + 1, SCALE_STEP))

    if not points or points[-1] != max_sessions:
        points.append(max_sessions)

    return points


def _cpu_limit_reached(peak_cpu: float, cpu_limit: int) -> bool:
    """Return True when the configured CPU safety threshold is reached."""
    return peak_cpu >= cpu_limit


def _remaining_timeout(deadline: float, cap: int) -> int:
    """Return the usable timeout without exceeding the sweep deadline."""
    return min(cap, max(0, int(deadline - time.monotonic())))


def _wait_for_recovery(seconds: int, deadline: float) -> bool:
    """Wait in short intervals so the global sweep deadline is respected."""
    wait_until = min(time.monotonic() + seconds, deadline)
    while time.monotonic() < wait_until:
        interval = min(30, max(0, wait_until - time.monotonic()))
        if interval <= 0:
            break
        time.sleep(interval)
    return time.monotonic() < deadline


def _ensure_point_preflight(
    settings: Settings,
    log_path: Path,
    sessions: int,
    deadline: float,
) -> tuple[bool, list[dict[str, Any]]]:
    """Run preflight and bounded recovery before a scale point is started."""
    events: list[dict[str, Any]] = []
    max_recoveries = max(0, settings.max_preflight_recovery_attempts)

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[scale] Starting {sessions}-session point\n")

    for recovery_attempt in range(max_recoveries + 1):
        timeout = _remaining_timeout(deadline, PREFLIGHT_TIMEOUT_SECONDS)
        if timeout <= 0:
            events.append({"sessions": sessions, "event": "preflight", "passed": False, "reason": "sweep_timeout"})
            return False, events

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("[scale] Running preflight\n")
        try:
            preflight = run_remote_preflight(settings, log_path, timeout)
        except subprocess.TimeoutExpired:
            events.append({"sessions": sessions, "event": "preflight", "passed": False, "reason": "timeout"})
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("[scale] Preflight FAILED: command timed out\n")
        else:
            if preflight.returncode == 0:
                events.append({"sessions": sessions, "event": "preflight", "passed": True, "recovery_attempt": recovery_attempt})
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write("[scale] Preflight PASSED\n")
                return True, events

            events.append({"sessions": sessions, "event": "preflight", "passed": False, "recovery_attempt": recovery_attempt})
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[scale] Preflight FAILED (exit {preflight.returncode})\n")

        if recovery_attempt == max_recoveries:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("[scale] Preflight recovery retry limit reached\n")
            return False, events

        timeout = _remaining_timeout(deadline, RECOVERY_TIMEOUT_SECONDS)
        if timeout <= 0:
            events.append({"sessions": sessions, "event": "recovery", "started": False, "reason": "sweep_timeout"})
            return False, events

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "[scale] Starting graceful Containerlab redeploy "
                f"(attempt {recovery_attempt + 1}/{max_recoveries})\n"
            )
        try:
            recovery = recover_remote_testbed(settings, log_path, timeout)
            recovery_ok = recovery.returncode == 0
        except subprocess.TimeoutExpired:
            recovery_ok = False
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("[scale] Graceful Containerlab redeploy timed out\n")

        events.append({
            "sessions": sessions,
            "event": "recovery",
            "attempt": recovery_attempt + 1,
            "completed": recovery_ok,
        })
        if not recovery_ok:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("[scale] Graceful Containerlab redeploy failed; retrying preflight policy\n")

        wait_seconds = max(0, settings.preflight_recovery_wait_seconds)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[scale] Waiting {wait_seconds} seconds for topology recovery\n")
        if not _wait_for_recovery(wait_seconds, deadline):
            events.append({"sessions": sessions, "event": "recovery_wait", "completed": False, "reason": "sweep_timeout"})
            return False, events

    return False, events


def _recovery_outcome(preflight_events: list[dict[str, Any]]) -> str:
    """Summarize whether any required recovery ultimately restored health."""
    recoveries = [
        event
        for event in preflight_events
        if event["event"] in {"recovery", "point_retry_recovery"}
    ]
    if not recoveries:
        return "not_required"
    last_recovery = recoveries[-1]
    for event in preflight_events:
        if last_recovery["event"] == "point_retry_recovery" and last_recovery["completed"]:
            return "recovered"
        if (
            event["event"] == "preflight"
            and event["sessions"] == last_recovery["sessions"]
            and event["passed"]
            and event.get("recovery_attempt", 0) >= last_recovery["attempt"]
        ):
            return "recovered"
    return "unhealthy_after_recovery_attempts"


def _recover_incomplete_point(
    settings: Settings,
    log_path: Path,
    sessions: int,
    attempt: int,
    deadline: float,
    preflight_events: list[dict[str, Any]],
) -> bool:
    """Gracefully reset a testbed after an incomplete point before retrying it."""
    timeout = _remaining_timeout(deadline, RECOVERY_TIMEOUT_SECONDS)
    if timeout <= 0:
        preflight_events.append({
            "sessions": sessions,
            "event": "point_retry_recovery",
            "attempt": attempt,
            "completed": False,
            "reason": "sweep_timeout",
        })
        return False

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[scale] Point established incompletely; starting graceful "
            f"Containerlab redeploy before retry {attempt}\n"
        )
    try:
        recovery = recover_remote_testbed(settings, log_path, timeout)
        recovered = recovery.returncode == 0
    except subprocess.TimeoutExpired:
        recovered = False
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("[scale] Graceful Containerlab redeploy timed out\n")

    event = {
        "sessions": sessions,
        "event": "point_retry_recovery",
        "attempt": attempt,
        "completed": recovered,
    }
    preflight_events.append(event)
    if not recovered:
        return False

    wait_seconds = max(0, settings.preflight_recovery_wait_seconds)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[scale] Waiting {wait_seconds} seconds before retrying point\n")
    if not _wait_for_recovery(wait_seconds, deadline):
        event["completed"] = False
        event["reason"] = "sweep_timeout"
        return False
    return True


def _latest_run_summary(run_directory: Path) -> dict[str, str]:
    """Read the final row from Experiment A's run_summary.csv."""
    summary_path = run_directory / "run_summary.csv"

    if not summary_path.is_file():
        raise RuntimeError("Scale point produced no run_summary.csv.")

    with summary_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise RuntimeError("Scale point produced an empty run_summary.csv.")

    return rows[-1]


def _write_outputs(
    artifacts: Path,
    timeseries: list[dict[str, Any]],
    start_sessions: int,
    max_sessions: int,
    cpu_limit: int,
    status: str,
    reason: str | None,
    failed_point: int | None,
    failed_point_established: int | None,
    preflight_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write Phase-4 time-series and report artifacts."""

    timeseries_path = artifacts / "scale_timeseries.csv"

    fieldnames = [
        "timestamp",
        "requested_sessions",
        "established_sessions",
        "active_sessions_prometheus",
        "bng_cpu_percent",
        "setup_rate",
    ]

    with timeseries_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(timeseries)

    peak_cpu_observed = max(
        (
            float(point["bng_cpu_percent"])
            for point in timeseries
        ),
        default=0.0,
    )

    final_established = (
        int(timeseries[-1]["established_sessions"])
        if timeseries
        else 0
    )
    final_active = timeseries[-1].get("active_sessions_prometheus") if timeseries else None

    report = {
        "recipe": "ipoe-scale",
        "status": status,
        "reason": reason,
        "start_sessions": start_sessions,
        "max_sessions": max_sessions,
        "cpu_limit": cpu_limit,
        "points_completed": len(timeseries),
        "completed_scale_points": [point["requested_sessions"] for point in timeseries],
        "failed_scale_point": failed_point,
        "failed_point_established_sessions": failed_point_established,
        "failed_point_failed_sessions": (
            failed_point - failed_point_established
            if failed_point is not None and failed_point_established is not None
            else None
        ),
        "peak_bng_cpu": peak_cpu_observed,
        "established_sessions_blaster": final_established,
        "active_sessions_prometheus": final_active,
        "timeseries_file": "scale_timeseries.csv",
        "preflight_failures": sum(
            1
            for event in preflight_events
            if event["event"] == "preflight" and not event["passed"]
        ),
        "recovery_attempts": sum(
            1
            for event in preflight_events
            if event["event"] in {"recovery", "point_retry_recovery"}
        ),
        "recovery_outcome": _recovery_outcome(preflight_events),
        "preflight_events": preflight_events,
    }

    (artifacts / "report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "peak_bng_cpu": peak_cpu_observed,
        "established_sessions": final_established,
        "active_sessions_prometheus": final_active,
        "scale_points_completed": len(timeseries),
        "completed_scale_points": [point["requested_sessions"] for point in timeseries],
    }


def execute(
    settings: Settings,
    job_directory: Path,
    parameters: dict[str, int],
    timeout_seconds: int,
) -> dict[str, Any]:
    """
    Run an incremental IPoE scale sweep using the existing
    Stage 1 Experiment A implementation.
    """

    artifacts = job_directory / "artifacts"
    logs = artifacts / "logs"

    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    log_path = logs / "job.log"

    # Reuse the existing Stage-2 remote-project verification.
    validate_remote_project(settings, log_path)

    if not settings.testbed_project_path:
        raise RuntimeError("TESTBED_PROJECT_PATH must be configured.")

    start_sessions = parameters["start_sessions"]
    max_sessions = parameters["max_sessions"]
    cpu_limit = parameters["cpu_limit"]

    experiment = f"{settings.testbed_project_path}/experiment-a"

    started = datetime.now(UTC)

    points = _sweep_points(start_sessions, max_sessions)

    timeseries: list[dict[str, Any]] = []

    final_status = "completed"
    reason: str | None = None
    failed_point: int | None = None
    preflight_events: list[dict[str, Any]] = []
    point_attempts: dict[int, int] = {}
    failed_point_established: int | None = None

    # The configured timeout applies to the COMPLETE scale sweep.
    deadline = time.monotonic() + timeout_seconds

    for point_index, sessions in enumerate(points):
        point_attempt = point_attempts.get(sessions, 0) + 1
        point_attempts[sessions] = point_attempt
        remaining_seconds = int(deadline - time.monotonic())

        if remaining_seconds <= 0:
            final_status = "partial" if timeseries else "failed"
            failed_point = sessions
            reason = (
                "Scale sweep reached the configured execution timeout; "
                "completed scale points were preserved."
            )
            break

        point_ready, point_events = _ensure_point_preflight(
            settings=settings,
            log_path=log_path,
            sessions=sessions,
            deadline=deadline,
        )
        preflight_events.extend(point_events)
        if not point_ready:
            final_status = "partial" if timeseries else "failed"
            failed_point = sessions
            if any(event.get("reason") in {"timeout", "sweep_timeout"} for event in point_events):
                reason = (
                    "Scale sweep partially completed. "
                    f"Successfully completed {len(timeseries)} of {len(points)} scale points. "
                    f"The {sessions}-session point could not start because the preflight "
                    "or recovery operation reached the sweep timeout."
                )
            else:
                reason = (
                    "Scale sweep partially completed. "
                    f"Successfully completed {len(timeseries)} of {len(points)} scale points. "
                    f"The {sessions}-session point could not start because testbed "
                    "preflight remained unhealthy after recovery attempts."
                )
            break

        remote_args = [
            f"{experiment}/run_experiment_a.sh",
            "--yes",
            "--rates",
            "5",
            "--repetitions",
            "1",
            "--sessions",
            str(sessions),
            "--hold-seconds",
            "60",
            "--cooldown-seconds",
            "0",
            "--scenario",
            "stage2_ipoe_scale",
            "--fault-label",
            "none",
        ]

        remote_command = (
            f"cd {shlex.quote(experiment)} && "
            + " ".join(shlex.quote(arg) for arg in remote_args)
        )

        command = _ssh_base(settings) + [remote_command]

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n=== Scale point: {sessions} sessions ===\n"
            )

            try:
                completed = subprocess.run(
                    command,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=remaining_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                final_status = "partial" if timeseries else "failed"
                failed_point = sessions
                reason = (
                    "Scale sweep reached the configured execution timeout "
                    f"while running the {sessions}-session point; "
                    "completed scale points were preserved."
                )
                break

        if completed.returncode != 0:
            final_status = "partial" if timeseries else "failed"
            failed_point = sessions
            reason = (
                f"Scale sweep stopped because the {sessions}-session "
                "Experiment A run failed; completed scale points "
                "were preserved."
            )
            break

        # Experiment A prints the generated results directory.
        lines = log_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()

        run_lines = [
            line
            for line in lines
            if line.startswith("Experiment A complete: ")
        ]

        if not run_lines:
            final_status = "partial" if timeseries else "failed"
            failed_point = sessions
            reason = (
                f"Scale sweep stopped at {sessions} sessions because "
                "Experiment A produced no results directory."
            )
            break

        remote_run_path = (
            run_lines[-1]
            .removeprefix("Experiment A complete: ")
            .strip()
        )

        expected_prefix = (
            f"{settings.testbed_project_path}/experiment-a-results/"
        )

        if not remote_run_path.startswith(expected_prefix):
            raise RuntimeError(
                "Remote Experiment A returned an unexpected results path."
            )

        point_directory = (
            artifacts
            / "scale-points"
            / str(sessions)
            / f"attempt-{point_attempt:02d}"
        )

        point_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        scp_command = [
            "scp",
            "-i",
            str(Path(settings.testbed_ssh_key_path or "")),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-r",
            (
                f"{settings.testbed_user}@{settings.testbed_host}:"
                f"{remote_run_path}/."
            ),
            str(point_directory),
        ]

        copied: subprocess.CompletedProcess[str] | None = None

        for copy_attempt in range(1, ARTIFACT_COPY_ATTEMPTS + 1):
            remaining_seconds = int(deadline - time.monotonic())

            if remaining_seconds <= 0:
                break

            copy_timeout = min(
                ARTIFACT_COPY_TIMEOUT_SECONDS,
                remaining_seconds,
            )

            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"Artifact collection attempt "
                    f"{copy_attempt}/{ARTIFACT_COPY_ATTEMPTS} "
                    f"for {sessions} sessions\n"
                )

                try:
                    copied = subprocess.run(
                        scp_command,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=copy_timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    copied = None
                    handle.write(
                        "Artifact collection attempt timed out.\n"
                    )

            if copied is not None and copied.returncode == 0:
                break

            if copy_attempt < ARTIFACT_COPY_ATTEMPTS:
                remaining_seconds = deadline - time.monotonic()

                if remaining_seconds <= 0:
                    break

                time.sleep(
                    min(
                        ARTIFACT_COPY_RETRY_DELAY_SECONDS,
                        remaining_seconds,
                    )
                )

        if copied is None or copied.returncode != 0:
            final_status = "partial" if timeseries else "failed"
            failed_point = sessions

            if time.monotonic() >= deadline:
                reason = (
                    "Scale sweep reached the configured execution timeout "
                    f"while collecting artifacts for {sessions} sessions; "
                    "completed scale points were preserved."
                )
            else:
                reason = (
                    f"Scale sweep reached {sessions} sessions but its "
                    f"artifacts could not be collected after "
                    f"{ARTIFACT_COPY_ATTEMPTS} attempts."
                )

            break

        # Reuse Stage 1's authoritative summary rather than
        # reimplementing its result-processing logic.
        summary = _latest_run_summary(point_directory)

        established = int(
            float(
                summary.get("established_sessions", 0)
                or 0
            )
        )

        peak_cpu = float(
            summary.get("peak_bng_cpu", 0)
            or 0
        )

        setup_rate = summary.get(
            "offered_rate",
            "5",
        )
        active_sessions = summary.get("peak_active_sessions")

        if established < sessions:
            failed_point_established = established
            retry_limit = max(0, settings.max_preflight_recovery_attempts)
            if point_attempt <= retry_limit and _recover_incomplete_point(
                settings,
                log_path,
                sessions,
                point_attempt,
                deadline,
                preflight_events,
            ):
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"[scale] Retrying {sessions}-session point after "
                        f"incomplete attempt {point_attempt}: {established}/{sessions} established\n"
                    )
                points.insert(point_index + 1, sessions)
                continue

            final_status = "partial"
            failed_point = sessions
            reason = (
                f"Scale sweep stopped at {sessions} sessions because only "
                f"{established} sessions were established after {point_attempt} attempt(s); "
                "completed points were preserved."
            )
            break

        timeseries.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "requested_sessions": sessions,
                "established_sessions": established,
                "active_sessions_prometheus": active_sessions,
                "bng_cpu_percent": peak_cpu,
                "setup_rate": setup_rate,
            }
        )
        failed_point_established = None

        # CPU safety gate:
        # preserve this point, then stop before increasing load.
        if _cpu_limit_reached(peak_cpu, cpu_limit):
            final_status = "partial"
            failed_point = sessions if sessions < max_sessions else None
            reason = (
                "Stopped early: BNG CPU reached the configured "
                f"safety limit ({cpu_limit}%) at {sessions} of "
                f"{max_sessions} requested sessions."
            )
            break

    output = _write_outputs(
        artifacts=artifacts,
        timeseries=timeseries,
        start_sessions=start_sessions,
        max_sessions=max_sessions,
        cpu_limit=cpu_limit,
        status=final_status,
        reason=reason,
        failed_point=failed_point,
        failed_point_established=failed_point_established,
        preflight_events=preflight_events,
    )

    result_requested_sessions = failed_point or max_sessions
    result_established_sessions = (
        failed_point_established
        if failed_point_established is not None
        else output["established_sessions"]
    )

    return {
        "status": final_status,
        "reason": reason,
        "timestamp_start": started.isoformat(),
        "requested_sessions": result_requested_sessions,
        "established_sessions": result_established_sessions,
        "established_sessions_blaster": result_established_sessions,
        "failed_sessions": result_requested_sessions - result_established_sessions,
        "active_sessions_prometheus": output[
            "active_sessions_prometheus"
        ],
        "peak_bng_cpu": output["peak_bng_cpu"],
        "scale_points_completed": output[
            "scale_points_completed"
        ],
        "completed_scale_points": output["completed_scale_points"],
        "failed_scale_point": failed_point,
        "preflight_failures": sum(
            1
            for event in preflight_events
            if event["event"] == "preflight" and not event["passed"]
        ),
        "recovery_attempts": sum(
            1
            for event in preflight_events
            if event["event"] in {"recovery", "point_retry_recovery"}
        ),
        "preflight_events": preflight_events,
        "scale_timeseries": "artifacts/scale_timeseries.csv",
    }

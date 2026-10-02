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
from .ipoe_bind import _ssh_base, validate_remote_project


SCALE_STEP = 50
ARTIFACT_COPY_ATTEMPTS = 3
ARTIFACT_COPY_RETRY_DELAY_SECONDS = 3
ARTIFACT_COPY_TIMEOUT_SECONDS = 120


def _sweep_points(start_sessions: int, max_sessions: int) -> list[int]:
    """Return deterministic scale points, always including max_sessions."""
    points = list(range(start_sessions, max_sessions + 1, SCALE_STEP))

    if not points or points[-1] != max_sessions:
        points.append(max_sessions)

    return points


def _cpu_limit_reached(peak_cpu: float, cpu_limit: int) -> bool:
    """Return True when the configured CPU safety threshold is reached."""
    return peak_cpu >= cpu_limit


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
) -> dict[str, Any]:
    """Write Phase-4 time-series and report artifacts."""

    timeseries_path = artifacts / "scale_timeseries.csv"

    fieldnames = [
        "timestamp",
        "requested_sessions",
        "established_sessions",
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

    report = {
        "recipe": "ipoe-scale",
        "status": status,
        "reason": reason,
        "start_sessions": start_sessions,
        "max_sessions": max_sessions,
        "cpu_limit": cpu_limit,
        "points_completed": len(timeseries),
        "peak_bng_cpu": peak_cpu_observed,
        "established_sessions_blaster": final_established,
        "timeseries_file": "scale_timeseries.csv",
    }

    (artifacts / "report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "peak_bng_cpu": peak_cpu_observed,
        "established_sessions": final_established,
        "scale_points_completed": len(timeseries),
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

    # The configured timeout applies to the COMPLETE scale sweep.
    deadline = time.monotonic() + timeout_seconds

    for sessions in points:
        remaining_seconds = int(deadline - time.monotonic())

        if remaining_seconds <= 0:
            final_status = "partial" if timeseries else "failed"
            reason = (
                "Scale sweep reached the configured execution timeout; "
                "completed scale points were preserved."
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
                reason = (
                    "Scale sweep reached the configured execution timeout "
                    f"while running the {sessions}-session point; "
                    "completed scale points were preserved."
                )
                break

        if completed.returncode != 0:
            final_status = "partial" if timeseries else "failed"
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

        for attempt in range(1, ARTIFACT_COPY_ATTEMPTS + 1):
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
                    f"{attempt}/{ARTIFACT_COPY_ATTEMPTS} "
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

            if attempt < ARTIFACT_COPY_ATTEMPTS:
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

        timeseries.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "requested_sessions": sessions,
                "established_sessions": established,
                "bng_cpu_percent": peak_cpu,
                "setup_rate": setup_rate,
            }
        )

        # CPU safety gate:
        # preserve this point, then stop before increasing load.
        if _cpu_limit_reached(peak_cpu, cpu_limit):
            final_status = "partial"
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
    )

    return {
        "status": final_status,
        "reason": reason,
        "timestamp_start": started.isoformat(),
        "requested_sessions": max_sessions,
        "established_sessions": output["established_sessions"],
        "established_sessions_blaster": output[
            "established_sessions"
        ],
        "peak_bng_cpu": output["peak_bng_cpu"],
        "scale_points_completed": output[
            "scale_points_completed"
        ],
        "scale_timeseries": "artifacts/scale_timeseries.csv",
    }
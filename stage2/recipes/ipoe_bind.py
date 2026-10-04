from __future__ import annotations

import csv
import json
import shutil
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings


def _ssh_base(settings: Settings) -> list[str]:
    if not all((settings.testbed_host, settings.testbed_user, settings.testbed_ssh_key_path)):
        raise RuntimeError("TESTBED_HOST, TESTBED_USER, and TESTBED_SSH_KEY_PATH must be configured for remote execution.")
    key = Path(settings.testbed_ssh_key_path)
    if not key.is_file():
        raise RuntimeError("TESTBED_SSH_KEY_PATH does not point to a readable SSH private-key file.")
    return ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", f"{settings.testbed_user}@{settings.testbed_host}"]


def validate_remote_project(settings: Settings, log_path: Path | None = None) -> None:
    """Read-only verification of the remote Experiment A project layout."""
    if not settings.testbed_project_path:
        raise RuntimeError("TESTBED_PROJECT_PATH must be the remote project root.")
    script = f"{settings.testbed_project_path}/experiment-a/run_experiment_a.sh"
    command = _ssh_base(settings) + [f"test -x {shlex.quote(script)}"]
    checked = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("Remote project verification exit code: " + str(checked.returncode) + "\n")
            if checked.stdout:
                handle.write(checked.stdout)
            if checked.stderr:
                handle.write(checked.stderr)
    if checked.returncode:
        detail = (checked.stderr or checked.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Remote project verification failed for {script}{suffix}")


def run_remote_preflight(
    settings: Settings,
    log_path: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run Experiment A's authoritative preflight on the remote testbed."""
    if not settings.testbed_project_path:
        raise RuntimeError("TESTBED_PROJECT_PATH must be configured.")
    experiment = f"{settings.testbed_project_path}/experiment-a"
    command = _ssh_base(settings) + [
        f"cd {shlex.quote(experiment)} && ./preflight.sh"
    ]
    with log_path.open("a", encoding="utf-8") as handle:
        return subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )


def recover_remote_testbed(
    settings: Settings,
    log_path: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Use the approved graceful Containerlab recovery procedure remotely."""
    if not settings.testbed_project_path:
        raise RuntimeError("TESTBED_PROJECT_PATH must be configured.")
    topology = f"{settings.testbed_project_path}/osvbng01.clab.yml"
    command = _ssh_base(settings) + [
        "sudo containerlab redeploy "
        f"--topo {shlex.quote(topology)} --keep-mgmt-net --graceful"
    ]
    with log_path.open("a", encoding="utf-8") as handle:
        return subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )


def _annotate_fault_labels(artifacts: Path) -> list[str]:
    """Add evidence-backed Blaster anomaly labels without changing Stage 1 data."""
    report_path = artifacts / "blaster-report.json"
    dataset_path = artifacts / "session_dataset.csv"
    if not report_path.is_file() or not dataset_path.is_file():
        return []
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    report = payload.get("report", payload)
    labels_by_session: dict[str, list[str]] = {}
    for session in report.get("sessions", []):
        labels: list[str] = []
        if int(session.get("dhcp-tx-discover", 0)) > 1:
            labels.append("dhcpv4_discover_retry")
        if int(session.get("dhcp-rx-nak", 0)) > 0:
            labels.append("dhcpv4_nak")
        if labels:
            labels_by_session[str(session.get("session-id"))] = labels
    with dataset_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        fields = handle.readline if False else list(rows[0]) if rows else []
    for row in rows:
        labels = labels_by_session.get(row.get("session_id", ""), [])
        if labels:
            previous = row.get("fault_label", "")
            row["fault_label"] = ";".join(([previous] if previous and previous != "none" else []) + labels)
    if rows:
        with dataset_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return sorted({label for labels in labels_by_session.values() for label in labels})


def execute(
    settings: Settings, job_directory: Path, parameters: dict[str, int], timeout_seconds: int
) -> dict[str, Any]:
    """Run the fixed Experiment A entrypoint on the configured testbed host."""
    logs = job_directory / "artifacts" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    validate_remote_project(settings, logs / "job.log")
    assert settings.testbed_project_path
    experiment = f"{settings.testbed_project_path}/experiment-a"
    started = datetime.now(UTC)
    remote_args = [f"{experiment}/run_experiment_a.sh", "--yes", "--rates", str(parameters["offered_rate"]), "--repetitions", "1", "--sessions", str(parameters["sessions"]), "--hold-seconds", str(parameters["duration"]), "--cooldown-seconds", "0", "--scenario", "stage2_ipoe_bind", "--fault-label", "none"]
    remote_command = f"cd {shlex.quote(experiment)} && " + " ".join(shlex.quote(arg) for arg in remote_args)
    command = _ssh_base(settings) + [remote_command]
    with (logs / "job.log").open("w") as handle:
        completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, text=True, timeout=timeout_seconds, check=False)
    run_line = next((line for line in (logs / "job.log").read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("Experiment A complete: ")), None)
    if not run_line:
        raise RuntimeError("Remote Experiment A exited before creating a results directory; see the private job log.")
    remote_run = run_line.removeprefix("Experiment A complete: ").strip()
    expected_prefix = f"{settings.testbed_project_path}/experiment-a-results/"
    if not remote_run.startswith(expected_prefix):
        raise RuntimeError("Remote Experiment A returned an unexpected results path.")
    run = job_directory / "source-results"
    run.mkdir(parents=True, exist_ok=True)
    scp = ["scp", "-i", str(Path(settings.testbed_ssh_key_path or "")), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-r", f"{settings.testbed_user}@{settings.testbed_host}:{remote_run}/.", str(run)]
    with (logs / "job.log").open("a") as handle:
        copied = subprocess.run(scp, stdout=handle, stderr=subprocess.STDOUT, text=True, timeout=120, check=False)
    if completed.returncode or copied.returncode:
        raise RuntimeError("Remote Experiment A or artifact collection failed; see the private job log.")
    artifacts = job_directory / "artifacts"
    for source in run.rglob("*"):
        if source.is_file():
            destination = artifacts / source.relative_to(run)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    # Preserve the original Experiment A hierarchy and expose the canonical
    # Stage 2 paths expected by download consumers.
    run_directories = sorted(
        {path.parent for path in run.rglob("blaster-report.json")},
        key=lambda path: path.stat().st_mtime,
    )
    if not run_directories:
        raise RuntimeError("Experiment A produced no BNG Blaster report.")
    source_run = run_directories[-1]
    for filename in ("access.pcap", "blaster-report.json", "session_dataset.csv", "osvbng.log"):
        source = source_run / filename
        if source.is_file():
            shutil.copy2(source, artifacts / filename)
    source_prometheus = source_run / "prometheus"
    if source_prometheus.is_dir():
        shutil.copytree(source_prometheus, artifacts / "prometheus", dirs_exist_ok=True)
    fault_labels = _annotate_fault_labels(artifacts)
    rows = list(csv.DictReader((run / "run_summary.csv").open())) if (run / "run_summary.csv").exists() else []
    summary = rows[-1] if rows else {}
    established = int(float(summary.get("established_sessions", 0) or 0))
    active = summary.get("peak_active_sessions")
    (artifacts / "report.json").write_text(json.dumps({"active_sessions_prometheus": active, "established_sessions_blaster": established, "fault_labels_observed": fault_labels, **summary}, indent=2) + "\n")
    status = "completed" if completed.returncode == 0 and established == parameters["sessions"] else "partial"
    reason = None if status == "completed" else f"{established} of {parameters['sessions']} sessions established; see fault_label in session_dataset.csv for details."
    return {"status": status, "reason": reason, "timestamp_start": started.isoformat(), "requested_sessions": parameters["sessions"], "established_sessions": established, "established_sessions_blaster": established, "active_sessions_prometheus": active, "failed_sessions": parameters["sessions"] - established, "setup_p50_ms": summary.get("setup_p50_ms"), "setup_p95_ms": summary.get("setup_p95_ms"), "peak_bng_cpu": summary.get("peak_bng_cpu"), "fault_labels_observed": fault_labels}

from __future__ import annotations

import csv
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ipoe_bind import recover_remote_testbed, run_remote_preflight


def _ssh_base(settings: Any) -> list[str]:
    """Build the SSH command used to communicate with the testbed."""

    if not settings.testbed_host:
        raise RuntimeError("TESTBED_HOST is not configured.")

    if not settings.testbed_user:
        raise RuntimeError("TESTBED_USER is not configured.")

    if not settings.testbed_ssh_key_path:
        raise RuntimeError("TESTBED_SSH_KEY_PATH is not configured.")

    return [
        "ssh",
        "-i",
        str(settings.testbed_ssh_key_path),
        "-n",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        f"{settings.testbed_user}@{settings.testbed_host}",
    ]


def _utc_now() -> str:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


def _validate_parameters(parameters: dict[str, Any]) -> tuple[int, int]:
    """Validate parameters required by the IPoE flap recipe."""

    sessions = int(parameters.get("sessions", 20))
    cycles = int(parameters.get("cycles", 1))

    if not 10 <= sessions <= 700:
        raise ValueError("sessions must be between 10 and 700")

    if not 1 <= cycles <= 5:
        raise ValueError("cycles must be between 1 and 5")

    return sessions, cycles

def _run_remote(
    settings: Any,
    command: str,
    timeout_seconds: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed command on the remote testbed through SSH."""

    try:
        return subprocess.run(
            _ssh_base(settings) + [command],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        command_preview = " ".join(command.strip().split())

        if len(command_preview) > 180:
            command_preview = command_preview[:180] + "..."

        raise RuntimeError(
            f"Remote command timed out after {timeout_seconds}s: "
            f"{command_preview}"
        ) from exc

def _get_session_counters(
    settings: Any,
    remote_dir: str,
) -> dict[str, Any]:
    """Read authoritative session counters from BNG Blaster."""

    command = (
        f"sudo docker exec clab-osvbng01-subscribers "
        f"bngblaster-cli '{remote_dir}/run.sock' session-counters"
    )

    result = _run_remote(settings, command)

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to read BNG Blaster session counters: "
            + result.stderr.strip()
        )

    try:
        data = json.loads(result.stdout)
        counters = data["session-counters"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            "Invalid session-counter response from BNG Blaster."
        ) from exc

    return {
        "established": int(counters.get("sessions-established", 0)),
        "terminated": int(counters.get("sessions-terminated", 0)),
        "flapped": int(counters.get("sessions-flapped", 0)),
    }

def _wait_for_established(
    settings: Any,
    remote_dir: str,
    expected_sessions: int,
    timeout_seconds: int,
) -> dict[str, int]:
    """Wait until BNG Blaster reports the requested sessions established."""

    deadline = time.monotonic() + timeout_seconds
    consecutive_failures = 0
    max_consecutive_failures = 3

    while time.monotonic() < deadline:
        try:
            counters = _get_session_counters(settings, remote_dir)
            consecutive_failures = 0

        except RuntimeError:
            consecutive_failures += 1

            if consecutive_failures >= max_consecutive_failures:
                raise

            time.sleep(2)
            continue

        if counters["established"] >= expected_sessions:
            return counters

        time.sleep(2)

    raise TimeoutError(
        f"Timed out waiting for {expected_sessions} sessions to establish."
    )

def _record_event(
    timeline: list[dict[str, Any]],
    counters_log: list[dict[str, Any]],
    cycle: int,
    event: str,
    counters: dict[str, Any],
) -> None:
    """Record a flap event and its BNG Blaster counter snapshot."""

    timestamp = _utc_now()

    timeline.append(
        {
            "timestamp": timestamp,
            "cycle": cycle,
            "event": event,
            "established_sessions": counters["established"],
        }
    )

    counters_log.append(
        {
            "timestamp": timestamp,
            "cycle": cycle,
            "event": event,
            "established": counters["established"],
            "terminated": counters["terminated"],
            "flapped": counters["flapped"],
        }
    )

def _start_blaster(
    settings: Any,
    remote_dir: str,
    config_path: str,
    sessions: int,
    start_rate: int = 20,
) -> None:
    """Start BNG Blaster for one phase of the flap experiment."""

    prepare_command = (
        f"sudo docker exec clab-osvbng01-subscribers sh -lc "
        f"\"rm -rf '{remote_dir}' && mkdir -p '{remote_dir}'\""
    )

    result = _run_remote(settings, prepare_command)

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to prepare BNG Blaster directory: "
            + result.stderr.strip()
        )

    # First use the original, known-working Stage 2 transfer path: copy the
    # existing Stage 1 subscriber config into this Stage 2 temporary directory.
    copy_command = (
        f"sudo docker cp '{config_path}' "
        f"clab-osvbng01-subscribers:'{remote_dir}/config.json'"
    )

    result = _run_remote(settings, copy_command)

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to copy BNG Blaster configuration: "
            + result.stderr.strip()
        )

    # Apply Stage 1-style pacing ONLY to the temporary Stage 2 copy.
    # The original Stage 1 config on the AWS host is never modified.
    pace_command = (
        "sudo docker exec clab-osvbng01-subscribers python3 -c "
        + repr(
            "import json, pathlib; "
            f"p=pathlib.Path({remote_dir!r} + '/config.json'); "
            "c=json.loads(p.read_text()); "
            "s=c.setdefault('sessions', {}); "
            f"s['start-rate']={int(start_rate)}; "
            f"s['stop-rate']={int(start_rate)}; "
            "p.write_text(json.dumps(c, indent=2) + '\\n')"
        )
    )

    result = _run_remote(settings, pace_command)

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to pace temporary BNG Blaster configuration: "
            + result.stderr.strip()
        )

    # Verify the exact file BNG Blaster will consume before launching.
    verify_command = (
        "sudo docker exec clab-osvbng01-subscribers sh -lc "
        f"\"test -s '{remote_dir}/config.json'\""
    )

    result = _run_remote(settings, verify_command)

    if result.returncode != 0:
        raise RuntimeError(
            f"BNG Blaster configuration is missing at {remote_dir}/config.json."
        )

    start_command = (
        "sudo docker exec -d clab-osvbng01-subscribers sh -lc "
        f"\"echo \\$\\$ > '{remote_dir}/pid'; "
        f"exec bngblaster "
        f"-C '{remote_dir}/config.json' "
        f"-c '{sessions}' "
        f"-l dhcp -L '{remote_dir}/blaster.log' "
        f"-J '{remote_dir}/blaster-report.json' "
        f"-j sessions -j streams "
        f"-S '{remote_dir}/run.sock' "
        f"-P '{remote_dir}/access.pcap' "
        f"-b > '{remote_dir}/blaster.stdout.log' 2>&1\""
    )

    result = _run_remote(settings, start_command)

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to start BNG Blaster: "
            + result.stderr.strip()
        )

    # docker exec -d may return before the control socket exists. Wait for it
    # without treating normal startup as a counter-query failure.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ready_command = (
            "sudo docker exec clab-osvbng01-subscribers sh -lc "
            f"\"test -S '{remote_dir}/run.sock'\""
        )
        ready = _run_remote(settings, ready_command)
        if ready.returncode == 0:
            return
        time.sleep(1)

    # If Blaster exited during startup, surface its log immediately.
    log_command = (
        "sudo docker exec clab-osvbng01-subscribers sh -lc "
        f"\"cat '{remote_dir}/blaster.stdout.log' 2>/dev/null || true\""
    )
    log_result = _run_remote(settings, log_command)
    detail = log_result.stdout.strip()
    if detail:
        raise RuntimeError(f"BNG Blaster failed during startup: {detail}")

    raise TimeoutError(
        f"BNG Blaster did not create {remote_dir}/run.sock within 30 seconds."
    )


def _stop_blaster(
    settings: Any,
    remote_dir: str,
) -> None:
    """Gracefully stop BNG Blaster, then force termination if needed."""

    command = (
        "sudo docker exec clab-osvbng01-subscribers sh -lc "
        f"\"if test -r '{remote_dir}/pid'; then "
        f"pid=\\$(cat '{remote_dir}/pid'); "
        f"kill -INT \\$pid 2>/dev/null || true; "
        f"for i in \\$(seq 1 60); do "
        f"kill -0 \\$pid 2>/dev/null || exit 0; "
        f"sleep 1; "
        f"done; "
        f"kill -TERM \\$pid 2>/dev/null || true; "
        f"fi\""
    )

    result = _run_remote(
        settings,
        command,
        timeout_seconds=70,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to stop BNG Blaster: "
            + result.stderr.strip()
        )

def _get_active_sessions(settings: Any) -> int:
    """Read the current active subscriber count from Prometheus."""

    command = (
        "curl -sG 'http://localhost:9090/api/v1/query' "
        "--data-urlencode 'query=osvbng_subscriber_sessions_active'"
    )

    result = _run_remote(settings, command)

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to query active subscriber sessions: "
            + result.stderr.strip()
        )

    try:
        data = json.loads(result.stdout)
        results = data["data"]["result"]

        if not results:
            return 0

        return int(float(results[0]["value"][1]))

    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "Invalid Prometheus active-session response."
        ) from exc

def _clear_stale_sessions(settings: Any) -> None:
    """Clear stale subscriber sessions using the osVBNG CLI."""

    command = r"""
set -e

cli_output="$(
    printf 'show subscriber sessions\nexit\n' |
    sudo docker exec -i clab-osvbng01-bng1 \
        /usr/local/bin/osvbngcli -server localhost:50050 2>/dev/null || true
)"

session_ids="$(
    printf '%s\n' "$cli_output" |
    grep -Eo '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' |
    sort -u || true
)"

if [ -z "$session_ids" ]; then
    exit 0
fi

while IFS= read -r session_id; do
    [ -n "$session_id" ] || continue

    printf 'exec subscriber session clear session-id %s\n' "$session_id" |
    sudo docker exec -i clab-osvbng01-bng1 \
        /usr/local/bin/osvbngcli -server localhost:50050 \
        >/dev/null 2>&1 || true
done <<EOF
$session_ids
EOF
"""

    result = _run_remote(
        settings,
        command,
        timeout_seconds=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to clear stale subscriber sessions: "
            + result.stderr.strip()
        )

def _wait_for_disconnected(
    settings: Any,
    timeout_seconds: int = 180,
) -> int:
    """Wait until osVBNG reports that all subscriber sessions are released."""

    deadline = time.monotonic() + timeout_seconds
    cleanup_attempted = False

    while time.monotonic() < deadline:
        active_sessions = _get_active_sessions(settings)

        if active_sessions == 0:
            return 0

        if not cleanup_attempted:
            _clear_stale_sessions(settings)
            cleanup_attempted = True
            time.sleep(3)
            continue

        time.sleep(2)

    raise TimeoutError(
        "Subscriber sessions did not reach zero after disconnect."
    )

def _ensure_testbed_ready(
    settings: Any,
    log_path: Path,
) -> None:
    """Run Stage 1 preflight and recover the topology when it is unhealthy."""

    max_recoveries = max(0, int(settings.max_preflight_recovery_attempts))
    wait_seconds = max(0, int(settings.preflight_recovery_wait_seconds))

    for recovery_attempt in range(max_recoveries + 1):
        preflight = run_remote_preflight(
            settings,
            log_path,
            90,
        )

        if preflight.returncode == 0:
            return

        if recovery_attempt == max_recoveries:
            raise RuntimeError(
                "Testbed preflight remained unhealthy after recovery attempts."
            )

        recovery = recover_remote_testbed(
            settings,
            log_path,
            300,
        )

        if recovery.returncode != 0:
            # Retry policy is intentionally bounded. A failed redeploy is
            # followed by the configured recovery wait and another preflight.
            pass

        if wait_seconds:
            time.sleep(wait_seconds)

    raise RuntimeError("Testbed preflight recovery failed.")


def _run_flap_cycles(
    settings: Any,
    config_path: str,
    sessions: int,
    cycles: int,
    timeout_seconds: int,
    remote_dirs: list[str],
    recovery_log_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run establish -> disconnect -> reconnect cycles."""

    timeline: list[dict[str, Any]] = []
    counters_log: list[dict[str, Any]] = []

    # Match the newer Stage 2 recipes: verify the shared testbed before
    # starting, and use the existing bounded graceful-redeploy recovery path.
    _ensure_testbed_ready(settings, recovery_log_path)

    # Stage 1 refuses to begin a run when subscriber state is not clean.
    active_before = _get_active_sessions(settings)
    if active_before != 0:
        raise RuntimeError(
            f"Testbed is not clean before IPoE flap: {active_before} active "
            "subscriber session(s) remain."
        )

    # Initial establishment
    remote_dir = "/tmp/stage2-ipoe-flap/initial"
    remote_dirs.append(remote_dir)

    _start_blaster(
        settings,
        remote_dir,
        config_path,
        sessions,
    )

    counters = _wait_for_established(
        settings,
        remote_dir,
        sessions,
        timeout_seconds,
    )

    _record_event(
        timeline,
        counters_log,
        0,
        "established",
        counters,
    )

    # Perform requested flap cycles
    for cycle in range(1, cycles + 1):

        # Disconnect current subscribers
        _stop_blaster(
            settings,
            remote_dir,
        )

        _wait_for_disconnected(settings)

        disconnected_counters = {
            "established": 0,
            "terminated": "",
            "flapped": "",
        }

        _record_event(
            timeline,
            counters_log,
            cycle,
            "disconnected",
            disconnected_counters,
        )

        # The disconnect can expose a routing/testbed failure. Re-run the
        # authoritative preflight while no Blaster is active; if unhealthy,
        # use the same bounded graceful-redeploy recovery as IPoE Scale.
        _ensure_testbed_ready(settings, recovery_log_path)

        # Reconnect using a fresh BNG Blaster run
        remote_dir = f"/tmp/stage2-ipoe-flap/cycle-{cycle}"
        remote_dirs.append(remote_dir)

        _start_blaster(
            settings,
            remote_dir,
            config_path,
            sessions,
        )

        counters = _wait_for_established(
            settings,
            remote_dir,
            sessions,
            timeout_seconds,
        )

        _record_event(
            timeline,
            counters_log,
            cycle,
            "reconnected",
            counters,
        )

    return timeline, counters_log

def _write_csv_artifacts(
    job_directory: Path,
    timeline: list[dict[str, Any]],
    counters_log: list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Write the session timeline and counter snapshots to CSV files."""

    job_directory.mkdir(parents=True, exist_ok=True)

    timeline_path = job_directory / "session_timeline.csv"

    with timeline_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "cycle",
                "event",
                "established_sessions",
            ],
        )

        writer.writeheader()
        writer.writerows(timeline)

    counters_path = job_directory / "counters.csv"

    with counters_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "cycle",
                "event",
                "established",
                "terminated",
                "flapped",
            ],
        )

        writer.writeheader()
        writer.writerows(counters_log)

    return timeline_path, counters_path

def _collect_remote_artifacts(
    settings: Any,
    job_directory: Path,
    remote_dirs: list[str],
) -> list[str]:
    """Copy available BNG Blaster artifacts into the Stage 2 job."""

    artifact_names: list[str] = []

    for index, remote_dir in enumerate(remote_dirs):
        phase_name = "initial" if index == 0 else f"cycle-{index}"

        for source_name in [
            "access.pcap",
            "blaster.log",
            "blaster-report.json",
            "blaster.stdout.log",
        ]:
            destination_name = f"{phase_name}-{source_name}"
            destination = job_directory / destination_name

            remote_source = (
                f"{settings.testbed_user}@{settings.testbed_host}:"
                f"/tmp/{destination_name}"
            )

            copy_out_command = (
                "sudo docker cp "
                f"clab-osvbng01-subscribers:'{remote_dir}/{source_name}' "
                f"'/tmp/{destination_name}'"
            )

            try:
                result = _run_remote(
                    settings,
                    copy_out_command,
                    timeout_seconds=30,
                )
            except RuntimeError:
                continue

            if result.returncode != 0:
                continue

            scp_command = [
                "scp",
                "-i",
                str(settings.testbed_ssh_key_path),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                remote_source,
                str(destination),
            ]

            try:
                scp_result = subprocess.run(
                    scp_command,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue

            if scp_result.returncode == 0:
                artifact_names.append(destination_name)

    return artifact_names

def _cleanup_flap_run(
    settings: Any,
    remote_dirs: list[str],
) -> None:
    """Best-effort cleanup of BNG Blaster processes from this recipe."""

    for remote_dir in remote_dirs:
        try:
            _stop_blaster(settings, remote_dir)
        except Exception:
            pass

def execute(
    settings: Any,
    job_directory: Path,
    parameters: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Execute the IPoE flap recipe."""

    sessions, cycles = _validate_parameters(parameters)

    job_directory.mkdir(parents=True, exist_ok=True)
    artifacts_directory = job_directory / "artifacts"
    artifacts_directory.mkdir(parents=True, exist_ok=True)

    timeline: list[dict[str, Any]] = []
    counters_log: list[dict[str, Any]] = []
    remote_dirs: list[str] = []

    config_path = (
        f"{settings.testbed_project_path}/"
        "subscribers/config.json"
    )

    try:
        timeline, counters_log = _run_flap_cycles(
            settings=settings,
            config_path=config_path,
            sessions=sessions,
            cycles=cycles,
            timeout_seconds=timeout_seconds,
            remote_dirs=remote_dirs,
            recovery_log_path=artifacts_directory / "preflight-recovery.log",
        )

        # The last reconnect is intentionally left running by _run_flap_cycles
        # so its counters can be recorded. Stop it now and verify that the
        # testbed returns to zero before copying final artifacts.
        if remote_dirs:
            _stop_blaster(settings, remote_dirs[-1])
            _wait_for_disconnected(settings)

        timeline_path, counters_path = _write_csv_artifacts(
            artifacts_directory,
            timeline,
            counters_log,
        )

        remote_artifacts = _collect_remote_artifacts(
            settings,
            artifacts_directory,
            remote_dirs,
        )

        result = {
            "recipe": "ipoe-flap",
            "status": "completed",
            "requested_sessions": sessions,
            "cycles": cycles,
            "timeline": timeline,
            "artifacts": [
                timeline_path.name,
                counters_path.name,
                *remote_artifacts,
            ],
        }

        report_path = artifacts_directory / "report.json"
        result["artifacts"].append(report_path.name)

        report_path.write_text(
            json.dumps(result, indent=2) + "\n",
            encoding="utf-8",
        )

        return result

    finally:
        _cleanup_flap_run(
            settings,
            remote_dirs,
        )
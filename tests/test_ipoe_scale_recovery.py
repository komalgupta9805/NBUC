from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
import csv
import json

from stage2.recipes.ipoe_scale import _ensure_point_preflight, execute


class ScalePreflightRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            max_preflight_recovery_attempts=2,
            preflight_recovery_wait_seconds=0,
        )

    def run_preflight(self, preflight_results: list[int]):
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "job.log"
            with (
                patch(
                    "stage2.recipes.ipoe_scale.run_remote_preflight",
                    side_effect=[SimpleNamespace(returncode=result) for result in preflight_results],
                ) as preflight,
                patch(
                    "stage2.recipes.ipoe_scale.recover_remote_testbed",
                    return_value=SimpleNamespace(returncode=0),
                ) as recover,
                patch("stage2.recipes.ipoe_scale._wait_for_recovery", return_value=True),
            ):
                ready, events = _ensure_point_preflight(
                    self.settings,
                    log_path,
                    100,
                    time.monotonic() + 30,
                )
                return ready, events, preflight.call_count, recover.call_count, log_path.read_text()

    def test_healthy_preflight_starts_point_without_recovery(self) -> None:
        ready, events, preflight_calls, recovery_calls, log = self.run_preflight([0])
        self.assertTrue(ready)
        self.assertEqual(preflight_calls, 1)
        self.assertEqual(recovery_calls, 0)
        self.assertEqual(events[-1]["passed"], True)
        self.assertIn("Preflight PASSED", log)

    def test_one_failed_preflight_recovers_then_starts_point(self) -> None:
        ready, events, preflight_calls, recovery_calls, log = self.run_preflight([1, 0])
        self.assertTrue(ready)
        self.assertEqual(preflight_calls, 2)
        self.assertEqual(recovery_calls, 1)
        self.assertIn("Starting graceful Containerlab redeploy", log)
        self.assertEqual(events[-1]["recovery_attempt"], 1)

    def test_persistent_preflight_failure_stops_after_retry_limit(self) -> None:
        ready, events, preflight_calls, recovery_calls, log = self.run_preflight([1, 1, 1])
        self.assertFalse(ready)
        self.assertEqual(preflight_calls, 3)
        self.assertEqual(recovery_calls, 2)
        self.assertIn("retry limit reached", log)
        self.assertEqual(sum(event["event"] == "recovery" for event in events), 2)


class ScalePointRetryTests(unittest.TestCase):
    def _run_scale_point(self, summaries: list[tuple[int, float]], cpu_limit: int = 80):
        settings = SimpleNamespace(
            testbed_project_path="/remote/project",
            testbed_user="ubuntu",
            testbed_host="testbed",
            testbed_ssh_key_path="/tmp/testbed-key",
            max_preflight_recovery_attempts=1,
            preflight_recovery_wait_seconds=0,
        )
        summary_index = 0

        def fake_run(command, **kwargs):
            nonlocal summary_index
            if command[0] == "scp":
                established, peak_cpu = summaries[summary_index]
                summary_index += 1
                destination = Path(command[-1]) / "run_summary.csv"
                with destination.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=("offered_rate", "requested_sessions", "established_sessions", "failed_sessions", "peak_active_sessions", "peak_bng_cpu"),
                    )
                    writer.writeheader()
                    writer.writerow({
                        "offered_rate": 5,
                        "requested_sessions": 70,
                        "established_sessions": established,
                        "failed_sessions": 70 - established,
                        "peak_active_sessions": established,
                        "peak_bng_cpu": peak_cpu,
                    })
            else:
                kwargs["stdout"].write(
                    f"Experiment A complete: /remote/project/experiment-a-results/run-{summary_index + 1}\n"
                )
            return SimpleNamespace(returncode=0)

        with (
            TemporaryDirectory() as directory,
            patch("stage2.recipes.ipoe_scale.validate_remote_project"),
            patch("stage2.recipes.ipoe_scale._ssh_base", return_value=["ssh"]),
            patch("stage2.recipes.ipoe_scale.run_remote_preflight", return_value=SimpleNamespace(returncode=0)) as preflight,
            patch("stage2.recipes.ipoe_scale.recover_remote_testbed", return_value=SimpleNamespace(returncode=0)) as recover,
            patch("stage2.recipes.ipoe_scale._wait_for_recovery", return_value=True),
            patch("stage2.recipes.ipoe_scale.subprocess.run", side_effect=fake_run),
        ):
            result = execute(
                settings,
                Path(directory),
                {"start_sessions": 70, "max_sessions": 70, "cpu_limit": cpu_limit},
                600,
            )
            report = json.loads((Path(directory) / "artifacts" / "report.json").read_text())
        return result, report, preflight.call_count, recover.call_count

    def test_healthy_point_succeeds_without_recovery(self) -> None:
        result, report, preflight_calls, recovery_calls = self._run_scale_point([(70, 3.0)])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(report["completed_scale_points"], [70])
        self.assertEqual(preflight_calls, 1)
        self.assertEqual(recovery_calls, 0)

    def test_incomplete_point_recovers_and_retries_before_succeeding(self) -> None:
        result, report, preflight_calls, recovery_calls = self._run_scale_point([(53, 3.0), (70, 3.0)])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(report["completed_scale_points"], [70])
        self.assertIsNone(report["failed_scale_point"])
        self.assertEqual(preflight_calls, 2)
        self.assertEqual(recovery_calls, 1)

    def test_persistent_incomplete_point_stops_without_fake_success(self) -> None:
        result, report, _preflight_calls, recovery_calls = self._run_scale_point([(53, 3.0), (53, 3.0)])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["requested_sessions"], 70)
        self.assertEqual(result["established_sessions"], 53)
        self.assertEqual(result["failed_sessions"], 17)
        self.assertEqual(report["completed_scale_points"], [])
        self.assertEqual(report["failed_point_established_sessions"], 53)
        self.assertEqual(recovery_calls, 1)

    def test_cpu_safety_stops_after_a_fully_established_point(self) -> None:
        result, report, _preflight_calls, recovery_calls = self._run_scale_point([(70, 80.0)], cpu_limit=80)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(report["completed_scale_points"], [70])
        self.assertEqual(recovery_calls, 0)


if __name__ == "__main__":
    unittest.main()

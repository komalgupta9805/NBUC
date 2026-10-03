from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from stage2.recipes.ipoe_scale import _ensure_point_preflight


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


if __name__ == "__main__":
    unittest.main()

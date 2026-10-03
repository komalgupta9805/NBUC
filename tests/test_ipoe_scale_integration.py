from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from stage2.intent_parser import deterministic_intent
from stage2.job_manager import JobManager
from stage2.registry import RECIPE_REGISTRY
from stage2.validator import validate


class IpoeScaleIntegrationTests(unittest.TestCase):
    def test_scale_request_preserves_explicit_range_and_cpu_limit(self) -> None:
        raw = deterministic_intent(
            "Generate an IPoE Scale dataset from 20 to 100 sessions with an 80% CPU safety limit."
        )
        self.assertEqual(raw["recipe"], "ipoe-scale")
        self.assertEqual(
            raw["parameters"],
            {"start_sessions": 20, "max_sessions": 100, "cpu_limit": 80},
        )
        result = validate(raw)
        self.assertEqual(result.kind, "confirm")
        self.assertEqual(result.intent, {"topology": "osvbng", "recipe": "ipoe-scale", "parameters": raw["parameters"]})

    def test_scale_request_recognizes_go_till_maximum(self) -> None:
        raw = deterministic_intent("generate ipoe scale start from 20 sessions go till 60 sessions")
        self.assertEqual(raw["recipe"], "ipoe-scale")
        self.assertEqual(raw["parameters"]["start_sessions"], 20)
        self.assertEqual(raw["parameters"]["max_sessions"], 60)

    def test_registry_driven_dispatch_uses_scale_timeout(self) -> None:
        manager = JobManager.__new__(JobManager)
        manager.jobs = {
            "test-job": {
                "job_id": "test-job",
                "directory": Path("/tmp/test-scale-job"),
                "intent": {
                    "recipe": "ipoe-scale",
                    "parameters": {"start_sessions": 10, "max_sessions": 10, "cpu_limit": 80},
                },
                "user_request": "scale test",
            }
        }
        execute = Mock(return_value={"status": "completed", "reason": None})
        manager._package = Mock()
        manager._finish = Mock()
        executor_module = SimpleNamespace(execute=execute)
        with patch("stage2.job_manager.import_module", return_value=executor_module):
            manager._run("test-job")
        self.assertEqual(RECIPE_REGISTRY["ipoe-scale"]["status"], "implemented")
        self.assertEqual(execute.call_args.args[3], 1200)
        self.assertEqual(execute.call_args.args[2]["max_sessions"], 10)

    def test_bind_and_flap_registry_states_are_unchanged(self) -> None:
        self.assertEqual(RECIPE_REGISTRY["ipoe-bind"]["status"], "implemented")
        self.assertEqual(RECIPE_REGISTRY["ipoe-flap"]["status"], "implemented")


if __name__ == "__main__":
    unittest.main()

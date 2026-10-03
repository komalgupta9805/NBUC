from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import unittest
import uuid

from stage2.app import (
    ChatRequest,
    RecipeSelectionRequest,
    chat,
    recipe_selection,
)
from stage2.intent_parser import parse_intent
from stage2.validator import validate


class RecipeSelectionContextTests(unittest.TestCase):
    def test_scale_parameter_follow_up_uses_selected_context(self) -> None:
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            parsed = parse_intent(
                "start from 20 go up till 100 with cpu safety limit of 80%",
                selected_recipe="ipoe-scale",
                selected_topology="osvbng",
            )
        result = validate(parsed)
        self.assertEqual(
            result.intent,
            {
                "topology": "osvbng",
                "recipe": "ipoe-scale",
                "parameters": {
                    "start_sessions": 20,
                    "max_sessions": 100,
                    "cpu_limit": 80,
                },
            },
        )

    def test_selection_is_persisted_and_passed_to_chat_parser(self) -> None:
        conversation_id = f"test-selection-{uuid.uuid4()}"
        recipe_selection(
            RecipeSelectionRequest(
                conversation_id=conversation_id,
                recipe="ipoe-scale",
            )
        )
        parsed = {
            "topology": "osvbng",
            "recipe": "ipoe-scale",
            "parameters": {
                "start_sessions": 20,
                "max_sessions": 100,
                "cpu_limit": 80,
            },
            "needs_clarification": False,
            "out_of_scope": False,
        }
        with patch("stage2.app.parse_intent", return_value=parsed) as parser:
            response = chat(
                ChatRequest(
                    conversation_id=conversation_id,
                    message="start from 20 go up till 100 with cpu safety limit of 80%",
                )
            )
        parser.assert_called_once_with(
            "start from 20 go up till 100 with cpu safety limit of 80%",
            selected_recipe="ipoe-scale",
            selected_topology="osvbng",
        )
        self.assertEqual(response["type"], "confirm")
        self.assertEqual(response["intent"], validate(parsed).intent)

    def test_parameter_only_followups_are_contextual_for_each_recipe(self) -> None:
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            bind = parse_intent("30", selected_recipe="ipoe-bind", selected_topology="osvbng")
            flap = parse_intent("30 sessions and 2 cycles", selected_recipe="ipoe-flap", selected_topology="osvbng")
            acct = parse_intent("30 sessions for 120 seconds", selected_recipe="radius-acct", selected_topology="osvbng")
        self.assertEqual(bind["recipe"], "ipoe-bind")
        self.assertEqual(bind["parameters"], {"sessions": 30})
        self.assertEqual(flap["recipe"], "ipoe-flap")
        self.assertEqual(flap["parameters"], {"sessions": 30, "cycles": 2})
        self.assertEqual(acct["recipe"], "radius-acct")
        self.assertEqual(acct["parameters"], {"sessions": 30, "capture_duration": 120})

    def test_explicit_standalone_recipe_overrides_stale_selection(self) -> None:
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            parsed = parse_intent(
                "generate 30 IPoE Bind sessions",
                selected_recipe="ipoe-scale",
                selected_topology="osvbng",
            )
        self.assertEqual(parsed["recipe"], "ipoe-bind")
        self.assertEqual(parsed["parameters"], {"sessions": 30})

    def test_selected_recipe_wins_over_implicit_parser_guess(self) -> None:
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            parsed = parse_intent(
                "30 sessions",
                selected_recipe="ipoe-scale",
                selected_topology="osvbng",
            )
        self.assertEqual(parsed["recipe"], "ipoe-scale")
        self.assertEqual(parsed["topology"], "osvbng")


if __name__ == "__main__":
    unittest.main()

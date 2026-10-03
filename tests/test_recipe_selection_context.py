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
from stage2.conversation import conversations
from stage2.intent_parser import parse_intent
from stage2.validator import validate


class RecipeSelectionContextTests(unittest.TestCase):
    def test_scale_slots_are_collected_across_multiple_turns(self) -> None:
        conversation_id = f"test-scale-slots-{uuid.uuid4()}"
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            first = chat(ChatRequest(conversation_id=conversation_id, message="IPoE Scale"))
            second = chat(ChatRequest(conversation_id=conversation_id, message="20"))
            third = chat(ChatRequest(conversation_id=conversation_id, message="go till 60"))
            final = chat(ChatRequest(conversation_id=conversation_id, message="80%"))
        self.assertEqual(first["type"], "needs_clarification")
        self.assertIn("starting", first["assistant_message"].lower())
        self.assertIn("maximum", second["assistant_message"].lower())
        self.assertIn("cpu", third["assistant_message"].lower())
        self.assertEqual(final["type"], "confirm")
        self.assertEqual(final["intent"]["parameters"], {
            "start_sessions": 20,
            "max_sessions": 60,
            "cpu_limit": 80,
        })

    def test_scale_asks_only_for_unresolved_slots(self) -> None:
        conversation_id = f"test-scale-partial-{uuid.uuid4()}"
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            response = chat(ChatRequest(
                conversation_id=conversation_id,
                message="Generate IPoE Scale starting from 10 sessions and go till 60 sessions.",
            ))
        self.assertEqual(response["type"], "needs_clarification")
        self.assertIn("cpu", response["assistant_message"].lower())
        self.assertNotIn("maximum number", response["assistant_message"].lower())
        self.assertIsNone(conversations.get(conversation_id).pending_intent)

    def test_scale_with_all_slots_proceeds_to_confirmation(self) -> None:
        conversation_id = f"test-scale-complete-{uuid.uuid4()}"
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            response = chat(ChatRequest(
                conversation_id=conversation_id,
                message="Generate IPoE Scale starting from 10 sessions, go till 60 sessions, CPU limit 80%.",
            ))
        self.assertEqual(response["type"], "confirm")
        self.assertEqual(response["intent"]["parameters"], {
            "start_sessions": 10,
            "max_sessions": 60,
            "cpu_limit": 80,
        })

    def test_menu_selected_scale_accepts_a_bare_start_count(self) -> None:
        conversation_id = f"test-menu-scale-{uuid.uuid4()}"
        recipe_selection(RecipeSelectionRequest(conversation_id=conversation_id, recipe="ipoe-scale"))
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            response = chat(ChatRequest(conversation_id=conversation_id, message="20"))
        self.assertEqual(response["type"], "needs_clarification")
        self.assertEqual(conversations.get(conversation_id).collected_parameters, {"start_sessions": 20})

    def test_bind_requires_sessions_before_confirmation(self) -> None:
        conversation_id = f"test-bind-slots-{uuid.uuid4()}"
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            first = chat(ChatRequest(conversation_id=conversation_id, message="Generate an IPoE Bind dataset."))
            final = chat(ChatRequest(conversation_id=conversation_id, message="50 sessions"))
        self.assertEqual(first["type"], "needs_clarification")
        self.assertIn("how many sessions", first["assistant_message"].lower())
        self.assertEqual(final["type"], "confirm")
        self.assertEqual(final["intent"]["parameters"]["sessions"], 50)

    def test_flap_preserves_sessions_while_collecting_cycles(self) -> None:
        conversation_id = f"test-flap-slots-{uuid.uuid4()}"
        with patch("stage2.intent_parser.settings", SimpleNamespace(groq_api_key=None)):
            first = chat(ChatRequest(conversation_id=conversation_id, message="Generate IPoE Flap with 40 sessions."))
            final = chat(ChatRequest(conversation_id=conversation_id, message="Run 2 cycles."))
        self.assertEqual(first["type"], "needs_clarification")
        self.assertIn("cycles", first["assistant_message"].lower())
        self.assertEqual(final["type"], "confirm")
        self.assertEqual(final["intent"]["parameters"], {"sessions": 40, "cycles": 2})

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

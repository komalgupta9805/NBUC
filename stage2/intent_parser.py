"""LLM JSON extraction with a safe deterministic fallback for development."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from .config import settings


SYSTEM_PROMPT = """Return only JSON matching this shape: {"topology":"osvbng"|null,
"recipe":"ipoe-bind"|"ipoe-scale"|"ipoe-flap"|"radius-acct"|null,
"confidence":"high"|"medium"|"low", "parameters":{},
"needs_clarification":boolean,"clarification_question":string|null,
"out_of_scope":boolean}. Never return commands or prose."""


def _integer(text: str, names: tuple[str, ...]) -> int | None:
    preceding_pattern = r"\b(\d+)\s+(?:" + "|".join(names) + r")\b"
    match = re.search(preceding_pattern, text, re.I)
    if match:
        return int(match.group(1))
    pattern = r"(?:" + "|".join(names) + r")\s*(?:of|for|=)?\s*(\d+)"
    match = re.search(pattern, text, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\s+(?:ipoe|subscribers?|sessions?)\b", text, re.I)
    return int(match.group(1)) if match else None


def _scale_parameters(text: str) -> dict[str, int]:
    """Extract the bounded scale-specific fields from common natural phrasing."""
    parameters: dict[str, int] = {}
    start = re.search(r"\b(?:from|start(?:ing)?(?:\s+at)?)\s*(\d+)\s*(?:sessions?|subscribers?)?", text, re.I)
    if not start:
        start = re.search(r"\b(\d+)\s*(?:sessions?|subscribers?)?\s+to\s+\d+\s*(?:sessions?|subscribers?)?", text, re.I)
    target = re.search(r"\b(?:to|till|until|up\s+(?:to|till|until)|go\s+(?:up\s+)?(?:to|till|until)|target|maximum|max)\s*(\d+)\s*(?:sessions?|subscribers?)?", text, re.I)
    cpu = re.search(r"\bcpu(?:\s+safety)?(?:\s+(?:limit|threshold))?\s*(?:of|to|=)?\s*(\d+)\s*(?:%|percent)?", text, re.I)
    cpu_suffix = re.search(r"\b(\d+)\s*(?:%|percent)\s+cpu(?:\s+(?:limit|threshold))?", text, re.I)
    if start:
        parameters["start_sessions"] = int(start.group(1))
    if target:
        parameters["max_sessions"] = int(target.group(1))
    if cpu or cpu_suffix:
        parameters["cpu_limit"] = int((cpu or cpu_suffix).group(1))
    return parameters


def _context_parameters(text: str, recipe: str) -> dict[str, int]:
    """Extract parameters from a follow-up after a recipe was selected."""
    if recipe == "ipoe-scale":
        return _scale_parameters(text)

    parameters: dict[str, int] = {}
    sessions = _integer(text, ("sessions?", "subscribers?"))
    if sessions is None:
        number = re.search(r"\b(\d+)\b", text)
        sessions = int(number.group(1)) if number else None
    if sessions is not None:
        parameters["sessions"] = sessions

    if recipe == "ipoe-flap":
        cycles = _integer(text, ("cycles?", "flaps?", "reconnects?"))
        if cycles is not None:
            parameters["cycles"] = cycles
    elif recipe == "radius-acct":
        duration = _integer(text, ("duration", "seconds?"))
        if duration is not None:
            parameters["capture_duration"] = duration
    return parameters


def _mentions_recipe(text: str) -> bool:
    """Whether a message explicitly names a recipe instead of just its inputs."""
    return bool(
        re.search(
            r"\bipoe[\s-]*(?:bind|scale|flap)\b|\bradius\b|\baccounting\b|\bipoe[\s-]*acct\b",
            text,
            re.I,
        )
    )


def deterministic_intent(message: str) -> dict[str, Any]:
    """Narrow, non-executing parser used only when Groq is not configured."""
    text = message.lower()
    if not any(token in text for token in ("bng", "ipoe", "dhcp", "subscriber", "radius")):
        return {"topology": None, "recipe": None, "confidence": "high", "parameters": {}, "needs_clarification": False, "clarification_question": None, "out_of_scope": True}
    if any(token in text for token in ("radius", "accounting", "acct")):
        recipe = "radius-acct"
    elif any(token in text for token in ("flap", "reconnect", "disconnect")):
        recipe = "ipoe-flap"
    elif any(token in text for token in ("scale", "how many", "setup rate", "sweep")):
        recipe = "ipoe-scale"
    elif any(token in text for token in ("bind", "dual-stack", "dual stack", "session")):
        recipe = "ipoe-bind"
    else:
        return {"topology": "osvbng", "recipe": None, "confidence": "low", "parameters": {}, "needs_clarification": True, "clarification_question": "Which experiment do you need: session binding, scaling, disconnect/reconnect, or RADIUS accounting?", "out_of_scope": False}
    sessions = _integer(text, ("sessions?", "subscribers?"))
    params = _scale_parameters(text) if recipe == "ipoe-scale" else {}
    if sessions is not None and (recipe != "ipoe-scale" or "start_sessions" not in params):
        params["start_sessions" if recipe == "ipoe-scale" else "sessions"] = sessions
    if recipe == "ipoe-flap":
        cycles = _integer(text, ("cycles?", "flaps?", "reconnects?"))
        if cycles is not None:
            params["cycles"] = cycles
    return {"topology": "osvbng", "recipe": recipe, "confidence": "medium", "parameters": params, "needs_clarification": False, "clarification_question": None, "out_of_scope": False}


def parse_intent(
    message: str,
    *,
    selected_recipe: str | None = None,
    selected_topology: str | None = None,
) -> dict[str, Any]:
    if not settings.groq_api_key:
        parsed = deterministic_intent(message)
    else:
        payload = {"model": settings.llm_model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message}], "temperature": 0, "response_format": {"type": "json_object"}}
        request = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response_payload = json.load(response)
            parsed = json.loads(response_payload["choices"][0]["message"]["content"])
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            # A provider outage must not make an otherwise unambiguous request unusable.
            # This parser remains non-executing; the validator still gates every run.
            parsed = deterministic_intent(message)

    # An explicitly named recipe in a standalone request always wins.
    # Otherwise, parameter-only follow-ups keep their selected context even if
    # an LLM attempts to infer a different recipe from an isolated number.
    if selected_recipe and not _mentions_recipe(message):
        parameters = parsed.get("parameters") if isinstance(parsed.get("parameters"), dict) else {}
        parsed = {
            **parsed,
            "topology": selected_topology or "osvbng",
            "recipe": selected_recipe,
            "parameters": {**parameters, **_context_parameters(message, selected_recipe)},
            "needs_clarification": False,
            "clarification_question": None,
            "out_of_scope": False,
        }
    return parsed

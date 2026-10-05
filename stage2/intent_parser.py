"""LLM JSON extraction with a safe deterministic fallback for development."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from .config import settings


SYSTEM_PROMPT = """You are the intent parser for a BNG dataset-generation assistant.
Return only JSON matching this shape:
{"topology":"osvbng"|null,
"recipe":"ipoe-bind"|"ipoe-scale"|"ipoe-flap"|"radius-acct"|null,
"confidence":"high"|"medium"|"low",
"parameters":{},
"needs_clarification":boolean,
"clarification_question":string|null,
"out_of_scope":boolean}.

Interpret natural wording, not only exact recipe names:
- ipoe-bind: establish/bring up/create dual-stack IPoE subscriber sessions.
- ipoe-flap: disconnect/reconnect, flap, recovery or resilience testing.
- ipoe-scale: scale/load/sweep subscriber counts toward a target with a CPU limit.
- radius-acct: RADIUS/accounting requests.

Parameter meanings:
- ipoe-bind: sessions, offered_rate, duration.
- ipoe-flap: sessions, cycles.
- ipoe-scale: start_sessions, max_sessions, cpu_limit.
- radius-acct: sessions, capture_duration.

Words such as users, clients or subscribers may refer to subscriber sessions.
For phrases such as "twice", "two times" or "3 reconnects", infer flap cycles.
If the request is about BNG/IPoE but the experiment type is ambiguous, set
needs_clarification=true instead of guessing.
If it is outside the current BNG/IPoE scope, set out_of_scope=true.
Never return commands or prose."""


def _integer(text: str, names: tuple[str, ...]) -> int | None:
    preceding_pattern = r"\b(\d+)\s+(?:" + "|".join(names) + r")\b"
    match = re.search(preceding_pattern, text, re.I)
    if match:
        return int(match.group(1))
    pattern = r"(?:" + "|".join(names) + r")\s*(?:of|for|=)?\s*(\d+)"
    match = re.search(pattern, text, re.I)
    if match:
        return int(match.group(1))
    if any(name.startswith(("sessions", "subscribers")) for name in names):
        match = re.search(r"\b(\d+)\s+(?:ipoe|subscribers?|sessions?|users?|clients?)\b", text, re.I)
        return int(match.group(1)) if match else None
    return None


def _word_number(text: str) -> int | None:
    """Return a small spoken number commonly used in conversational requests."""
    words = {
        "one": 1, "once": 1,
        "two": 2, "twice": 2,
        "three": 3, "thrice": 3,
        "four": 4, "five": 5,
    }
    for word, value in words.items():
        if re.search(rf"\b{word}\b", text, re.I):
            return value
    return None


def _scale_parameters(text: str) -> dict[str, int]:
    """Extract the bounded scale-specific fields from common natural phrasing."""
    parameters: dict[str, int] = {}
    start = re.search(
        r"\b(?:from|start(?:ing)?(?:\s+at)?)\s*(?:sessions?|subscribers?)(?:\s+count)?\s*(?:of|=)?\s*(\d+)\b|\b(?:from|start(?:ing)?(?:\s+at)?)\s*(\d+)\s*(?:sessions?|subscribers?)?",
        text,
        re.I,
    )
    if not start:
        start = re.search(r"\b(\d+)\s*(?:sessions?|subscribers?)?\s+to\s+\d+\s*(?:sessions?|subscribers?)?", text, re.I)
    target = re.search(
        r"\b(?:maximum|max)\s*(?:sessions?|subscribers?)(?:\s+count)?\s*(?:of|=)?\s*(\d+)\b|\b(?:to|till|until|up\s+(?:to|till|until)|go\s+(?:up\s+)?(?:to|till|until)|target|maximum|max)\s*(\d+)\s*(?:sessions?|subscribers?)?",
        text,
        re.I,
    )
    cpu = re.search(r"\bcpu(?:\s+safety)?(?:\s+(?:limit|threshold))?\s*(?:of|to|=)?\s*(\d+)\s*(?:%|percent)?", text, re.I)
    cpu_suffix = re.search(r"\b(\d+)\s*(?:%|percent)\s+cpu(?:\s+(?:limit|threshold))?", text, re.I)
    cpu_percent = re.search(r"\b(\d+)\s*%", text, re.I)
    if start:
        parameters["start_sessions"] = int(next(value for value in start.groups() if value is not None))
    if target:
        parameters["max_sessions"] = int(next(value for value in target.groups() if value is not None))
    if cpu or cpu_suffix or cpu_percent:
        parameters["cpu_limit"] = int((cpu or cpu_suffix or cpu_percent).group(1))
    return parameters


def _context_parameters(
    text: str,
    recipe: str,
    existing_parameters: dict[str, int] | None = None,
) -> dict[str, int]:
    """Extract parameters from a follow-up after a recipe was selected."""
    existing_parameters = existing_parameters or {}
    if recipe == "ipoe-scale":
        parameters = _scale_parameters(text)

        # For unlabeled follow-ups, fill only the unresolved Scale slots
        # in their natural order: max_sessions, then cpu_limit.
        if not parameters:
            numbers = [int(value) for value in re.findall(r"\b\d+\b", text)]

            if "start_sessions" not in existing_parameters and numbers:
                parameters["start_sessions"] = numbers.pop(0)

            if "max_sessions" not in existing_parameters and numbers:
                parameters["max_sessions"] = numbers.pop(0)

            if "cpu_limit" not in existing_parameters and numbers:
                parameters["cpu_limit"] = numbers.pop(0)

        return parameters

    parameters: dict[str, int] = {}
    sessions = _integer(text, ("sessions?", "subscribers?", "users?", "clients?"))
    cycles = _integer(text, ("cycles?", "flaps?", "reconnects?")) if recipe == "ipoe-flap" else None
    if recipe == "ipoe-flap" and cycles is None and re.search(r"\b(?:cycle|flap|reconnect|disconnect|time|times)\b", text, re.I):
        cycles = _word_number(text)
    if sessions is None and cycles is None:
        number = re.search(r"\b(\d+)\b", text)
        if number:
            value = int(number.group(1))

            if recipe == "ipoe-flap":
                if "sessions" not in existing_parameters:
                    sessions = value
                elif "cycles" not in existing_parameters:
                    cycles = value
            else:
                sessions = value
    if sessions is not None:
        parameters["sessions"] = sessions

    if recipe == "ipoe-flap":
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
    if re.fullmatch(r"\s*\d+\s*", text):
        return {
            "topology": "osvbng",
            "recipe": None,
            "confidence": "low",
            "parameters": {},
            "needs_clarification": True,
            "clarification_question": "Which experiment do you need: session binding, scaling or disconnect/reconnect?",
            "out_of_scope": False,
        }
    if not any(
    token in text
    for token in (
        "bng",
        "ipoe",
        "dhcp",
        "subscriber",
        "session",
        "user",
        "client",
        "recovery",
        "resilience",
        "load",
        "radius",
        "flap",
        "cycle",
        "reconnect",
        "disconnect",
        "scale",
        "bind",
    )
):
        return {"topology": None, "recipe": None, "confidence": "high", "parameters": {}, "needs_clarification": False, "clarification_question": None, "out_of_scope": True}
    if any(token in text for token in ("radius", "accounting", "acct")):
        recipe = "radius-acct"
    elif any(token in text for token in ("flap", "cycle", "cycles", "reconnect", "disconnect", "recovery", "resilience")):
        recipe = "ipoe-flap"
    elif any(token in text for token in ("scale", "scaling", "load", "setup rate", "sweep", "ramp")):
        recipe = "ipoe-scale"
    elif any(token in text for token in ("bind", "dual-stack", "dual stack", "bring up", "establish", "session")):
        recipe = "ipoe-bind"
    else:
        return {"topology": "osvbng", "recipe": None, "confidence": "low", "parameters": {}, "needs_clarification": True, "clarification_question": "Which experiment do you need: session binding, scaling, disconnect/reconnect, or RADIUS accounting?", "out_of_scope": False}
    sessions = _integer(text, ("sessions?", "subscribers?", "users?", "clients?"))
    params = _scale_parameters(text) if recipe == "ipoe-scale" else {}
    if sessions is not None and (recipe != "ipoe-scale" or "start_sessions" not in params):
        params["start_sessions" if recipe == "ipoe-scale" else "sessions"] = sessions
    if recipe == "ipoe-flap":
        cycles = _integer(text, ("cycles?", "flaps?", "reconnects?"))
        if cycles is None and re.search(r"\b(?:cycle|flap|reconnect|disconnect|time|times)\b", text, re.I):
            cycles = _word_number(text)
        if cycles is not None:
            params["cycles"] = cycles
    return {"topology": "osvbng", "recipe": recipe, "confidence": "medium", "parameters": params, "needs_clarification": False, "clarification_question": None, "out_of_scope": False}


def parse_intent(
    message: str,
    *,
    selected_recipe: str | None = None,
    selected_topology: str | None = None,
    selected_parameters: dict[str, int] | None = None,
) -> dict[str, Any]:
    selected_parameters = selected_parameters or {}
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
             "parameters": {
    **parameters,
    **_context_parameters(
        message,
        selected_recipe,
        {**selected_parameters, **parameters},
    ),
},
            "needs_clarification": False,
            "clarification_question": None,
            "out_of_scope": False,
        }
    return parsed

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
    pattern = r"(?:" + "|".join(names) + r")\s*(?:of|for|=)?\s*(\d+)"
    match = re.search(pattern, text, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\s+(?:ipoe|subscribers?|sessions?)\b", text, re.I)
    return int(match.group(1)) if match else None


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
    params: dict[str, int] = {}
    if sessions is not None:
        params["start_sessions" if recipe == "ipoe-scale" else "sessions"] = sessions
    return {"topology": "osvbng", "recipe": recipe, "confidence": "medium", "parameters": params, "needs_clarification": False, "clarification_question": None, "out_of_scope": False}


def parse_intent(message: str) -> dict[str, Any]:
    if not settings.groq_api_key:
        return deterministic_intent(message)
    payload = {"model": settings.llm_model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message}], "temperature": 0, "response_format": {"type": "json_object"}}
    request = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_payload = json.load(response)
        return json.loads(response_payload["choices"][0]["message"]["content"])
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        # A provider outage must not make an otherwise unambiguous request unusable.
        # This parser remains non-executing; the validator still gates every run.
        return deterministic_intent(message)

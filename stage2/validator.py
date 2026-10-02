from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import settings
from .registry import RECIPE_ALIASES, RECIPE_PARAMETERS, RECIPE_REGISTRY, TOPOLOGY_REGISTRY


@dataclass
class ValidationResult:
    kind: str
    message: str
    intent: dict[str, Any] | None = None
    options: list[str] | None = None


def validate(raw: object) -> ValidationResult:
    if not isinstance(raw, dict):
        return ValidationResult("needs_clarification", "I could not understand that request. Please describe the BNG dataset you need.")
    if raw.get("out_of_scope") or raw.get("topology") not in TOPOLOGY_REGISTRY:
        return ValidationResult("out_of_scope", "osvbng is the only supported topology; this request did not match it.")
    recipe = raw.get("recipe")
    if isinstance(recipe, str):
        recipe = RECIPE_ALIASES.get(recipe, recipe)
    if raw.get("needs_clarification") or recipe is None:
        return ValidationResult("needs_clarification", str(raw.get("clarification_question") or "Which experiment do you need: session binding, scaling, disconnect/reconnect, or RADIUS accounting?"), options=["Session binding", "Scaling", "Disconnect/reconnect", "RADIUS accounting"])
    if recipe not in RECIPE_REGISTRY:
        return ValidationResult("not_implemented", "That BNG use case is currently not implemented (future).")
    if RECIPE_REGISTRY[recipe]["status"] != "implemented":
        return ValidationResult("not_implemented", f"{recipe} is recognized but not yet implemented for this demo.")
    provided = raw.get("parameters", {})
    if not isinstance(provided, dict):
        provided = {}
    allowed = RECIPE_PARAMETERS[recipe]
    parameters: dict[str, int] = {}
    for name, rule in allowed.items():
        value = provided.get(name, rule["default"])
        if isinstance(value, bool) or not isinstance(value, int):
            return ValidationResult("needs_clarification", f"{name.replace('_', ' ').capitalize()} must be a whole number between {rule['minimum']} and {rule['maximum']}.")
        if value < rule["minimum"] or value > rule["maximum"]:
            return ValidationResult("needs_clarification", f"Requested {value} for {name.replace('_', ' ')}, which is outside the supported range of {rule['minimum']}–{rule['maximum']} for {recipe}.")
        parameters[name] = value
    if recipe == "ipoe-scale" and parameters["max_sessions"] < parameters["start_sessions"]:
        return ValidationResult("needs_clarification", "max sessions must be at least start sessions for ipoe-scale.")
    if any(value > settings.max_sessions_hard_cap for name, value in parameters.items() if "sessions" in name):
        return ValidationResult("needs_clarification", f"The hard safety cap is {settings.max_sessions_hard_cap} sessions.")
    intent = {"topology": "osvbng", "recipe": recipe, "parameters": parameters}
    return ValidationResult("confirm", f"I understood this as {recipe} on osvbng. Shall I start?", intent=intent)

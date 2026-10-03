from __future__ import annotations

import json
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, settings
from .conversation import conversations
from .intent_parser import parse_intent
from .job_manager import jobs
from .registry import RECIPE_ALIASES, RECIPE_PARAMETERS, RECIPE_REGISTRY
from .validator import validate

app = FastAPI(title="BNG Dataset Generator")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class RecipeSelectionRequest(BaseModel):
    conversation_id: str
    recipe: str | None = None


@app.get("/")
def index():
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/api/health")
def health():
    testbed_configured = bool(
        settings.testbed_host
        and settings.testbed_user
        and settings.testbed_project_path
        and settings.testbed_ssh_key_path
    )

    components = {
        "bng": "not_available",
        "bng_blaster": "not_available",
        "prometheus": "not_available",
        "grafana": "not_available",
        "frr": "not_available",
    }

    if testbed_configured:
        key = Path(settings.testbed_ssh_key_path)

        ssh_base = [
            "ssh",
            "-i",
            str(key),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            f"{settings.testbed_user}@{settings.testbed_host}",
        ]

        remote_command = (
            "printf 'bng='; "
            "sudo -n docker inspect -f '{{.State.Status}}' clab-osvbng01-bng1; "
            "printf 'bng_blaster='; "
            "sudo -n docker exec clab-osvbng01-subscribers sh -c 'command -v bngblaster'; "
            "printf 'prometheus='; "
            "sudo -n docker inspect -f '{{.State.Status}}' mon-prometheus; "
            "printf 'grafana='; "
            "sudo -n docker inspect -f '{{.State.Status}}' mon-grafana; "
            "printf 'frr='; "
            "sudo -n docker inspect -f '{{.State.Status}}' clab-osvbng01-corerouter1"
        )

        try:
            result = subprocess.run(
                ssh_base + [remote_command],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            for line in result.stdout.splitlines():
                if "=" not in line:
                    continue

                name, value = line.split("=", 1)
                value = value.strip()

                if name == "bng_blaster":
                    components[name] = "available" if value else "not_available"
                elif name in components:
                    components[name] = value or "not_available"

        except (OSError, subprocess.TimeoutExpired):
            pass

    return {
        "status": "ok",
        "llm_configured": bool(settings.groq_api_key),
        "testbed_configured": testbed_configured,
        "components": components,
    }


@app.get("/api/capabilities")
def capabilities():
    """Read-only frontend capabilities derived from the canonical registry."""
    return {
        "recipes": [
            {
                "id": recipe_id,
                **definition,
                "parameters": RECIPE_PARAMETERS[recipe_id],
            }
            for recipe_id, definition in RECIPE_REGISTRY.items()
        ]
    }


@app.get("/api/experiments")
def experiments():
    """Read existing job metadata for history views; no second experiment store."""
    records = []
    for item in jobs.jobs.values():
        result = item.get("result", {})
        metadata_path = item["directory"] / "metadata.json"
        if metadata_path.is_file():
            try:
                result = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        intent = item.get("intent", {})
        parameters = result.get("parameters", intent.get("parameters", {}))
        records.append({
            "job_id": item["job_id"],
            "status": item["status"],
            "stage": item["stage"],
            "reason": item.get("reason"),
            "recipe": result.get("recipe", intent.get("recipe")),
            "parameters": parameters,
            "requested_sessions": result.get("requested_sessions", parameters.get("sessions") or parameters.get("max_sessions")),
            "established_sessions": result.get("established_sessions_blaster", result.get("established_sessions")),
            "failed_sessions": result.get("failed_sessions"),
            "setup_p50_ms": result.get("setup_p50_ms"),
            "setup_p95_ms": result.get("setup_p95_ms"),
            "peak_bng_cpu": result.get("peak_bng_cpu"),
            "timestamp_start": result.get("timestamp_start"),
            "timestamp_end": result.get("timestamp_end"),
        })
    records.sort(key=lambda record: record.get("timestamp_end") or record.get("timestamp_start") or record["job_id"], reverse=True)
    return {"experiments": records}


@app.post("/api/conversations/recipe-selection")
def recipe_selection(payload: RecipeSelectionRequest):
    """Persist the UI recipe selector as context for a parameter follow-up."""
    conversation = conversations.get(payload.conversation_id)
    if payload.recipe is None:
        conversation.selected_recipe = None
        conversation.selected_topology = None
        conversation.pending_intent = None
        return {"selected_recipe": None, "selected_topology": None}

    recipe = RECIPE_ALIASES.get(payload.recipe, payload.recipe)
    if recipe not in RECIPE_REGISTRY:
        raise HTTPException(400, "Unknown recipe selection")
    conversation.selected_recipe = recipe
    conversation.selected_topology = "osvbng"
    conversation.pending_intent = None
    return {"selected_recipe": recipe, "selected_topology": "osvbng"}


@app.post("/api/chat")
def chat(payload: ChatRequest):
    conversation = conversations.get(payload.conversation_id)
    message = payload.message.strip()
    if message.lower() in {"yes", "confirm"} and conversation.pending_intent:
        job = jobs.start(conversation.pending_intent, conversation.history[-1]["message"] if conversation.history else "")
        conversation.pending_intent = None
        conversation.selected_recipe = None
        conversation.selected_topology = None
        if "error" in job:
            return {"type": "rejected", "assistant_message": job["error"], "reason": job["error"]}
        return {"type": "accepted", "assistant_message": "Preparing testbed…", "job_id": job["job_id"]}
    if message.lower() in {"no", "cancel"} and conversation.pending_intent:
        conversation.pending_intent = None
        conversation.selected_recipe = None
        conversation.selected_topology = None
        return {"type": "rejected", "assistant_message": "Okay — I did not start an experiment.", "reason": "Cancelled before execution."}
    conversation.history.append({"message": message})
    result = validate(
        parse_intent(
            message,
            selected_recipe=conversation.selected_recipe,
            selected_topology=conversation.selected_topology,
        )
    )
    response = {"type": result.kind, "assistant_message": result.message}
    if result.options:
        response["options"] = result.options
    if result.intent:
        conversation.pending_intent = result.intent
        conversation.selected_recipe = result.intent["recipe"]
        conversation.selected_topology = result.intent["topology"]
        response["intent"] = result.intent
    return response


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    item = jobs.get(job_id)
    if not item:
        raise HTTPException(404, "Job not found")
    return {key: item[key] for key in ("job_id", "status", "stage", "progress_pct", "reason")}


@app.get("/api/jobs/{job_id}/result")
def result(job_id: str):
    item = jobs.get(job_id)
    if not item:
        raise HTTPException(404, "Job not found")
    metadata = item["directory"] / "metadata.json"
    if metadata.exists():
        return FileResponse(metadata, media_type="application/json")
    return {"status": item["status"], "reason": item["reason"]}


def download(job_id: str, name: str):
    item = jobs.get(job_id)
    path = item["directory"] / name if item else None
    if not path or not path.is_file():
        raise HTTPException(404, "Requested download is not available")
    return FileResponse(path, filename=name)


@app.get("/api/jobs/{job_id}/dataset")
def dataset(job_id: str):
    return download(job_id, "dataset.tar.gz")


@app.get("/api/jobs/{job_id}/artifacts")
def artifacts(job_id: str):
    return download(job_id, "artifacts.zip")

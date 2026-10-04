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
from stage2.result_analyzer import (
    analyze_result,
    load_result_evidence,
    explain_result,
)

app = FastAPI(title="BNG Dataset Generator")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    result_job_id: str | None = None


class RecipeSelectionRequest(BaseModel):
    conversation_id: str
    recipe: str | None = None


def _missing_required_parameters(recipe: str, parameters: dict[str, object]) -> list[str]:
    """Return required conversational slots that have not been supplied."""
    return [
        name
        for name, rule in RECIPE_PARAMETERS[recipe].items()
        if rule.get("required") and name not in parameters
    ]


def _missing_parameter_question(recipe: str, missing: list[str]) -> str:
    """Ask only for the unresolved slots in natural recipe-specific language."""
    missing_set = set(missing)
    if recipe == "ipoe-bind":
        return "How many sessions should I generate?"
    if recipe == "ipoe-scale":
        if missing_set == {"start_sessions", "max_sessions", "cpu_limit"}:
            return "What starting number of sessions, maximum number of sessions, and CPU safety limit (%) should I use?"
        if missing_set == {"max_sessions", "cpu_limit"}:
            return "What maximum number of sessions should I scale up to, and what CPU safety limit (%) should I use?"
        if missing_set == {"start_sessions", "max_sessions"}:
            return "What starting number of sessions and maximum number of sessions should I use?"
        if missing_set == {"start_sessions", "cpu_limit"}:
            return "What starting number of sessions and CPU safety limit (%) should I use?"
        if missing_set == {"start_sessions"}:
            return "What starting number of sessions should I use?"
        if missing_set == {"max_sessions"}:
            return "What maximum number of sessions should I scale to?"
        return "What CPU safety limit (%) should I use?"
    if recipe == "ipoe-flap":
        if missing_set == {"sessions", "cycles"}:
            return "How many sessions should I use, and how many disconnect/reconnect cycles should I run?"
        if missing_set == {"sessions"}:
            return "How many sessions should I use?"
        return "How many disconnect/reconnect cycles should I run?"
    return "Please provide the remaining required parameters."


@app.get("/")
def index():
    return FileResponse(ROOT / "templates" / "index.html")

def _testbed_ssh_base() -> list[str]:
    """Build the SSH command used to access the remote testbed."""
    key = Path(settings.testbed_ssh_key_path)

    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        f"{settings.testbed_user}@{settings.testbed_host}",
    ]

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
        ssh_base = _testbed_ssh_base()

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


@app.get("/api/metrics/subscribers")
def subscriber_metrics():
    """Return recent subscriber-session time series from Prometheus."""
    testbed_configured = bool(
        settings.testbed_host
        and settings.testbed_user
        and settings.testbed_ssh_key_path
    )

    if not testbed_configured:
        raise HTTPException(503, "Testbed is not configured")

    ssh_base = _testbed_ssh_base()

    remote_command = (
        "END=$(date +%s); "
        "START=$((END - 600)); "
        "curl -fsS -G 'http://localhost:9090/api/v1/query_range' "
        "--data-urlencode 'query=osvbng_subscriber_sessions_active' "
        "--data-urlencode \"start=$START\" "
        "--data-urlencode \"end=$END\" "
        "--data-urlencode 'step=5s'"
    )

    try:
        result = subprocess.run(
            ssh_base + [remote_command],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "Unable to reach the testbed") from exc

    if result.returncode != 0:
        raise HTTPException(
            503,
            f"Unable to query Prometheus: {result.stderr.strip()}",
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "Prometheus returned invalid JSON") from exc

    if payload.get("status") != "success":
        raise HTTPException(
            502,
            payload.get("error", "Prometheus query failed"),
        )

    results = payload.get("data", {}).get("result", [])

    if not results:
        return {
            "metric": "osvbng_subscriber_sessions_active",
            "values": [],
        }

    values = results[0].get("values", [])

    return {
        "metric": "osvbng_subscriber_sessions_active",
        "values": [
            {
                "timestamp": timestamp,
                "subscribers": float(value),
            }
            for timestamp, value in values
        ],
    }
@app.get("/api/capabilities")
def capabilities():
    """Read-only frontend capabilities for implemented recipes."""
    supported_recipes = {"ipoe-bind", "ipoe-scale", "ipoe-flap"}

    return {
        "recipes": [
            {
                "id": recipe_id,
                **definition,
                "parameters": RECIPE_PARAMETERS[recipe_id],
            }
            for recipe_id, definition in RECIPE_REGISTRY.items()
            if recipe_id in supported_recipes
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
        conversation.collected_parameters.clear()
        return {"selected_recipe": None, "selected_topology": None}

    recipe = RECIPE_ALIASES.get(payload.recipe, payload.recipe)
    if recipe not in RECIPE_REGISTRY:
        raise HTTPException(400, "Unknown recipe selection")
    conversation.selected_recipe = recipe
    conversation.selected_topology = "osvbng"
    conversation.pending_intent = None
    conversation.collected_parameters.clear()
    return {"selected_recipe": recipe, "selected_topology": "osvbng"}

def is_result_question(message: str) -> bool:
    """Route only clear questions about an already-run experiment to analysis."""
    text = message.lower().strip()
    if text.startswith(("generate", "run", "start", "create", "make")):
        return False

    analysis_prefixes = (
        "why did",
        "why was",
        "why were",
        "what caused",
        "what is causing",
        "what was",
        "what were",
        "explain",
        "show me",
        "compare",
        "how many",
        "did all",
        "were all",
        "was there",
        "is there",
    )
    if not text.startswith(analysis_prefixes):
        return False

    result_topics = (
        "stop",
        "result",
        "p50",
        "p95",
        "latency",
        "retry",
        "failure",
        "failed",
        "success rate",
        "established",
        "setup rate",
        "peak active",
        "cpu",
        "memory",
        "nak",
        "discover",
        "scale point",
        "scale progression",
        "reconnect",
        "session loss",
        "instability",
        "bottleneck",
        "performance degradation",
    )
    return any(topic in text for topic in result_topics)
@app.post("/api/chat")
def chat(payload: ChatRequest):
    conversation = conversations.get(payload.conversation_id)
    message = payload.message.strip()

    # Handle confirmation
    if message.lower() in {"yes", "confirm"} and conversation.pending_intent:
        job = jobs.start(
            conversation.pending_intent,
            conversation.history[-1]["message"]
            if conversation.history
            else "",
        )

        conversation.last_job_id = job.get("job_id")
        conversation.pending_intent = None
        conversation.selected_recipe = None
        conversation.selected_topology = None

        conversation.collected_parameters.clear()
        if "error" in job:
            return {
                "type": "rejected",
                "assistant_message": job["error"],
                "reason": job["error"],
            }

        return {
            "type": "accepted",
            "assistant_message": "Preparing testbed…",
            "job_id": job["job_id"],
        }

    # Handle cancellation
    if message.lower() in {"no", "cancel"} and conversation.pending_intent:
        conversation.pending_intent = None
        conversation.selected_recipe = None
        conversation.selected_topology = None
        conversation.collected_parameters.clear()
        return {"type": "rejected", "assistant_message": "Okay — I did not start an experiment.", "reason": "Cancelled before execution."}
        # Handle questions about experiment results
    if is_result_question(message):
        job = None

        # Explicit result job from the UI
        if payload.result_job_id:
            job = jobs.get(payload.result_job_id)

        # Otherwise use the job associated with this conversation
        if job is None and conversation.last_job_id:
            job = jobs.get(conversation.last_job_id)

        # If there is no conversation job, find the latest supported
        # completed/failed experiment with the required artifacts
        if job is None:
            candidate_jobs = [
                item
                for item in jobs.jobs.values()
                if item.get("status") in {"completed", "failed"}
            ]

            candidate_jobs.sort(
                key=lambda item: item.get("job_id", ""),
                reverse=True,
            )

            for candidate in candidate_jobs:
                recipe = candidate.get("intent", {}).get("recipe")
                artifacts = candidate["directory"] / "artifacts"

                supported = (
                    recipe == "ipoe-bind"
                    and (artifacts / "session_dataset.csv").is_file()
                    and (artifacts / "blaster-report.json").is_file()
                ) or (
                    recipe == "ipoe-flap"
                    and (artifacts / "session_timeline.csv").is_file()
                    and (artifacts / "counters.csv").is_file()
                ) or (
                    recipe == "ipoe-scale"
                    and (artifacts / "scale_timeseries.csv").is_file()
                    and (artifacts / "report.json").is_file()
                )

                if supported:
                    job = candidate
                    break

        if job:
            evidence = load_result_evidence(job)
            analysis = analyze_result(evidence)

            answer = explain_result(
                evidence,
                analysis,
                settings.groq_api_key,
                message,
            )

            return {
                "type": "analysis",
                "assistant_message": answer,
            }

    conversation.history.append({"message": message})
    parse_context = {
        "selected_recipe": conversation.selected_recipe,
        "selected_topology": conversation.selected_topology,
    }
    if conversation.collected_parameters:
        parse_context["selected_parameters"] = conversation.collected_parameters
    raw = parse_intent(message, **parse_context)
    recipe = raw.get("recipe")
    if isinstance(recipe, str):
        recipe = RECIPE_ALIASES.get(recipe, recipe)

    if recipe in RECIPE_REGISTRY:
        parsed_parameters = raw.get("parameters")
        if not isinstance(parsed_parameters, dict):
            parsed_parameters = {}
        if recipe != conversation.selected_recipe:
            conversation.collected_parameters.clear()
        parameters = {**conversation.collected_parameters, **parsed_parameters}
        raw = {
            **raw,
            "recipe": recipe,
            "topology": raw.get("topology") or conversation.selected_topology or "osvbng",
            "parameters": parameters,
        }

    result = validate(raw)
    if result.kind == "confirm" and recipe in RECIPE_REGISTRY:
        parameters = raw["parameters"]
        conversation.selected_recipe = recipe
        conversation.selected_topology = raw["topology"]
        conversation.collected_parameters = parameters
        missing = _missing_required_parameters(recipe, parameters)
        if missing:
            conversation.pending_intent = None
            return {
                "type": "needs_clarification",
                "assistant_message": _missing_parameter_question(recipe, missing),
            }

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

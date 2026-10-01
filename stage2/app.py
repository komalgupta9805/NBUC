from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, settings
from .conversation import conversations
from .intent_parser import parse_intent
from .job_manager import jobs
from .validator import validate

app = FastAPI(title="BNG Dataset Generator")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


@app.get("/")
def index():
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_configured": bool(settings.groq_api_key),
        "testbed_configured": bool(
            settings.testbed_host
            and settings.testbed_user
            and settings.testbed_project_path
            and settings.testbed_ssh_key_path
        ),
    }


@app.post("/api/chat")
def chat(payload: ChatRequest):
    conversation = conversations.get(payload.conversation_id)
    message = payload.message.strip()
    if message.lower() in {"yes", "confirm"} and conversation.pending_intent:
        job = jobs.start(conversation.pending_intent, conversation.history[-1]["message"] if conversation.history else "")
        conversation.pending_intent = None
        if "error" in job:
            return {"type": "rejected", "assistant_message": job["error"], "reason": job["error"]}
        return {"type": "accepted", "assistant_message": "Preparing testbed…", "job_id": job["job_id"]}
    if message.lower() in {"no", "cancel"} and conversation.pending_intent:
        conversation.pending_intent = None
        return {"type": "rejected", "assistant_message": "Okay — I did not start an experiment.", "reason": "Cancelled before execution."}
    conversation.history.append({"message": message})
    result = validate(parse_intent(message))
    response = {"type": result.kind, "assistant_message": result.message}
    if result.options:
        response["options"] = result.options
    if result.intent:
        conversation.pending_intent = result.intent
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

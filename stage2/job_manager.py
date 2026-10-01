from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self._load_existing_jobs()

    def _load_existing_jobs(self) -> None:
        """Recover completed/failed jobs so downloads survive an app restart."""
        if not settings.jobs_output_dir.is_dir():
            return
        for metadata_path in settings.jobs_output_dir.glob("JOB-*/metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                directory = metadata_path.parent
                request_path = directory / "request.json"
                request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.is_file() else {}
                job_id = str(metadata.get("request_id") or request.get("job_id") or directory.name)
                self.jobs[job_id] = {
                    "job_id": job_id,
                    "status": metadata.get("status", "failed"),
                    "stage": "completed" if metadata.get("status") == "completed" else "failed",
                    "progress_pct": 100,
                    "reason": metadata.get("reason"),
                    "directory": directory,
                    "intent": metadata.get("normalized_intent", request.get("normalized_intent", {})),
                    "user_request": metadata.get("user_request_text", request.get("user_request_text", "")),
                    "result": metadata,
                }
            except (OSError, ValueError, TypeError):
                continue

    def start(self, intent: dict[str, Any], user_request: str) -> dict[str, Any]:
        with self.lock:
            active = next((j for j in self.jobs.values() if j["status"] in {"queued", "running"}), None)
            if active:
                return {"error": f"An experiment is already running ({active['job_id']}), please wait for it to finish."}
            job_id = f"JOB-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
            directory = settings.jobs_output_dir / job_id
            job = {"job_id": job_id, "status": "queued", "stage": "queued", "progress_pct": 0, "reason": None, "directory": directory, "intent": intent, "user_request": user_request}
            self.jobs[job_id] = job
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "request.json").write_text(json.dumps({"job_id": job_id, "user_request_text": user_request, "normalized_intent": intent, "status": "queued"}, indent=2) + "\n")
            threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
            return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def _run(self, job_id: str) -> None:
        job = self.jobs[job_id]
        job.update(status="running", stage="preparing_testbed", progress_pct=10)
        try:
            from .recipes.ipoe_bind import execute
            job.update(stage="running_experiment", progress_pct=30)
            result = execute(
                settings,
                job["directory"],
                job["intent"]["parameters"],
                settings.default_job_timeout_seconds,
            )
            job.update(stage="packaging_dataset", progress_pct=80)
            self._package(job, result)
            self._finish(job, result["status"], result.get("reason"), result)
        except subprocess.TimeoutExpired:
            self._finish(
                job,
                "failed",
                f"Execution exceeded the {settings.default_job_timeout_seconds // 60}-minute timeout and was terminated; partial logs preserved.",
            )
        except Exception as exc:
            self._finish(job, "failed", f"Experiment execution failed: {exc}")

    def _package(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        directory: Path = job["directory"]
        artifacts = directory / "artifacts"
        artifacts.mkdir(exist_ok=True)
        metadata = {"request_id": job["job_id"], "user_request_text": job["user_request"], "normalized_intent": job["intent"], "topology": "osvbng", "recipe": "ipoe-bind", "parameters": job["intent"]["parameters"], "timestamp_start": result.get("timestamp_start"), "timestamp_end": datetime.now(UTC).isoformat(), "testbed_host_env_var": "TESTBED_HOST", "experiment_version": "experiment-a", **result, "artifact_paths": {"dataset": "dataset.tar.gz", "artifacts_dir": "artifacts/"}}
        (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        with tarfile.open(directory / "dataset.tar.gz", "w:gz") as archive:
            archive.add(directory / "metadata.json", arcname="metadata.json")
            archive.add(artifacts, arcname="artifacts")
        shutil.make_archive(str(directory / "artifacts"), "zip", artifacts)

    def _finish(self, job: dict[str, Any], status: str, reason: str | None, result: dict[str, Any] | None = None) -> None:
        job.update(status=status, stage="completed" if status == "completed" else "failed", progress_pct=100, reason=reason)
        if result:
            job["result"] = result
        directory: Path = job["directory"]
        metadata = directory / "metadata.json"
        if not metadata.exists():
            artifacts = directory / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            metadata.write_text(json.dumps({
                "request_id": job["job_id"], "user_request_text": job["user_request"],
                "normalized_intent": job["intent"], "topology": "osvbng",
                "recipe": job["intent"]["recipe"], "parameters": job["intent"]["parameters"],
                "testbed_host_env_var": "TESTBED_HOST", "status": status, "reason": reason,
                "artifact_paths": {"dataset": "dataset.tar.gz", "artifacts_dir": "artifacts/"},
            }, indent=2) + "\n")
            with tarfile.open(directory / "dataset.tar.gz", "w:gz") as archive:
                archive.add(metadata, arcname="metadata.json")
                archive.add(artifacts, arcname="artifacts")
            shutil.make_archive(str(directory / "artifacts"), "zip", artifacts)
        request = job["directory"] / "request.json"
        payload = json.loads(request.read_text())
        payload.update(status=status, reason=reason)
        request.write_text(json.dumps(payload, indent=2) + "\n")


jobs = JobManager()

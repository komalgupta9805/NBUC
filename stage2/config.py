from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_local_env() -> None:
    """Read the optional local .env without adding a runtime dependency."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


_load_local_env()


@dataclass(frozen=True)
class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "groq")
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    testbed_host: str | None = os.getenv("TESTBED_HOST")
    testbed_user: str | None = os.getenv("TESTBED_USER")
    testbed_project_path: str | None = os.getenv("TESTBED_PROJECT_PATH")
    testbed_ssh_key_path: str | None = os.getenv("TESTBED_SSH_KEY_PATH")
    max_sessions_hard_cap: int = int(os.getenv("MAX_SESSIONS_HARD_CAP", "700"))
    default_job_timeout_seconds: int = int(os.getenv("DEFAULT_JOB_TIMEOUT_SECONDS", "600"))
    scale_job_timeout_seconds: int = int(os.getenv("SCALE_JOB_TIMEOUT_SECONDS", "1200"))
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    jobs_output_dir: Path = Path(os.getenv("JOBS_OUTPUT_DIR", str(ROOT / "stage2-runs")))


settings = Settings()

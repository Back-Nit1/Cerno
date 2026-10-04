"""Runtime settings, read from environment (and an optional .env in the repo root)."""

import os
from pathlib import Path

from pydantic import BaseModel, model_validator

CERNO_DIR = ".cerno"
# Hard ceilings. Settings can come from a `.env` inside the repository under review, so a value there must
# never be able to turn a bounded investigation into an unbounded one.
LIMITS = {
    "max_tool_steps": (1, 30), "max_graph_depth": (0, 4), "max_snippet_lines": (10, 400),
    "max_search_results": (1, 50), "check_timeout": (1, 1800), "request_timeout": (1.0, 600.0),
    "max_model_calls": (1, 2000), "max_total_tokens": (1000, 50_000_000), "max_calls_per_minute": (1, 600),
}


class Settings(BaseModel):
    model: str = "gpt-5.6-luna"
    max_tool_steps: int = 8
    max_graph_depth: int = 2
    max_snippet_lines: int = 120
    max_search_results: int = 8
    check_timeout: int = 300
    request_timeout: float = 120.0  # per model request, seconds
    max_model_calls: int = 200  # per run; the run stops with an error once spent
    max_total_tokens: int = 3_000_000  # input + output, per run
    max_calls_per_minute: int = 60  # client-side rate limit on the API key
    price_input_per_mtok: float | None = None  # user-supplied prices only; never guessed
    price_output_per_mtok: float | None = None
    db_path: str = f"{CERNO_DIR}/cerno.db"
    log_level: str = "INFO"
    api_key: str | None = None

    @model_validator(mode="after")
    def _clamp(self) -> "Settings":
        for name, (low, high) in LIMITS.items():
            setattr(self, name, min(max(getattr(self, name), low), high))
        return self

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        env = dict(os.environ)
        dotenv = (root or Path.cwd()) / ".env"
        if dotenv.is_file() and not os.environ.get("CERNO_NO_DOTENV"):  # set in CI: the PR owns that file
            for line in dotenv.read_text().splitlines():
                key, sep, value = line.partition("=")
                if sep and not line.lstrip().startswith("#") and value.strip():
                    env.setdefault(key.strip(), value.strip().strip("'\""))
        fields = {
            "model": "CERNO_MODEL",
            "max_tool_steps": "CERNO_MAX_TOOL_STEPS",
            "max_graph_depth": "CERNO_MAX_GRAPH_DEPTH",
            "max_snippet_lines": "CERNO_MAX_SNIPPET_LINES",
            "max_search_results": "CERNO_MAX_SEARCH_RESULTS",
            "check_timeout": "CERNO_CHECK_TIMEOUT",
            "request_timeout": "CERNO_REQUEST_TIMEOUT",
            "max_model_calls": "CERNO_MAX_MODEL_CALLS",
            "max_total_tokens": "CERNO_MAX_TOTAL_TOKENS",
            "max_calls_per_minute": "CERNO_MAX_CALLS_PER_MINUTE",
            "price_input_per_mtok": "CERNO_PRICE_INPUT_PER_MTOK",
            "price_output_per_mtok": "CERNO_PRICE_OUTPUT_PER_MTOK",
            "db_path": "CERNO_DB_PATH",
            "log_level": "CERNO_LOG_LEVEL",
            "api_key": "OPENAI_API_KEY",
        }
        return cls(**{f: env[e] for f, e in fields.items() if env.get(e)})


def cerno_dir(root: Path) -> Path:
    return root / CERNO_DIR


def db_file(root: Path, settings: Settings) -> Path:
    path = Path(settings.db_path)
    return path if path.is_absolute() else root / path


def session_dir(root: Path, session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    path = cerno_dir(root) / "sessions" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path

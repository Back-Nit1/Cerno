"""Runtime settings, read from environment (and an optional .env in the repo root)."""

import os
from pathlib import Path

from pydantic import BaseModel

CERNO_DIR = ".cerno"


class Settings(BaseModel):
    model: str = "gpt-5.6-luna"
    max_tool_steps: int = 8
    max_graph_depth: int = 2
    max_snippet_lines: int = 120
    max_search_results: int = 8
    check_timeout: int = 300
    db_path: str = f"{CERNO_DIR}/cerno.db"
    log_level: str = "INFO"
    api_key: str | None = None

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        env = dict(os.environ)
        dotenv = (root or Path.cwd()) / ".env"
        if dotenv.is_file():
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

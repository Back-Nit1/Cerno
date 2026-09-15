"""Allowlisted project checks. Commands come only from detected project configuration, never from the model."""

import json
import subprocess
import time
import tomllib
from pathlib import Path

from cerno.models import CheckKind, CheckResult

TAIL_CHARS = 2000


def detect_project_types(root: Path) -> list[str]:
    types = []
    if (root / "package.json").is_file():
        types.append("typescript" if (root / "tsconfig.json").is_file() else "javascript")
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        types.append("python")
    return types


def detect_checks(root: Path) -> dict[str, list[str]]:
    """kind -> argv. Only kinds a project actually configures are returned."""
    checks: dict[str, list[str]] = {}
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {}) or {}
        except json.JSONDecodeError:
            scripts = {}
        runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        aliases = {"test": ["test"], "build": ["build"], "lint": ["lint"], "typecheck": ["typecheck", "type-check", "tsc"]}
        for kind, names in aliases.items():
            if name := next((n for n in names if n in scripts), None):
                checks[kind] = [runner, "run", name]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and "test" not in checks:
        try:
            data = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError:
            data = {}
        prefix = ["uv", "run"] if (root / "uv.lock").exists() else ["python", "-m"]
        tool = data.get("tool", {})
        if "pytest" in tool or (root / "tests").is_dir():
            checks["test"] = [*prefix, "pytest", "-q"]
        if "ruff" in tool:
            checks.setdefault("lint", [*prefix, "ruff", "check", "."])
        if "mypy" in tool:
            checks.setdefault("typecheck", [*prefix, "mypy", "."])
    return checks


def run_check(root: Path, kind: CheckKind, timeout: int = 300) -> CheckResult | None:
    command = detect_checks(root).get(kind)
    if command is None:
        return None
    start = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True, errors="replace", timeout=timeout,
                              shell=False, env=None)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = None, str(exc.stdout or ""), f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        code, out, err = None, "", f"command not found: {exc}"
    return CheckResult(
        kind=kind, command=command, exit_code=code, duration_s=round(time.monotonic() - start, 2),
        stdout_tail=out[-TAIL_CHARS:], stderr_tail=err[-TAIL_CHARS:],
    )

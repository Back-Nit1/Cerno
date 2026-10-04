"""Shared fixture and result helpers for the review benchmark."""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evals" / "cases"
BUILTIN_RULES = {
    "CERNO-CONTRACT": "Breaks an existing caller or public API contract.",
    "CERNO-AUTHZ": "Removes or weakens an authentication, permission or validation check.",
    "CERNO-DATA-EXPOSURE": "Secrets, personal data or unfiltered records reach logs, responses or other tenants.",
    "CERNO-DESTRUCTIVE": "Introduces a destructive data or schema change.",
    "CERNO-BEHAVIOR": "Changes existing behaviour outside the apparent purpose of the diff.",
}
SEVERITIES = {"critical", "high", "medium", "low"}


@dataclass(frozen=True)
class Case:
    """One labelled benchmark case."""

    case_id: str
    root: Path
    label: dict[str, Any]


def run(argv: list[str], cwd: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=check)


def load_cases(split: str | None = None, cases_dir: Path = CASES_DIR) -> list[Case]:
    cases = []
    if not cases_dir.is_dir():
        raise ValueError(f"cases directory does not exist: {cases_dir}")
    for label_path in sorted(cases_dir.glob("*/label.json")):
        label = json.loads(label_path.read_text())
        case = Case(label_path.parent.name, label_path.parent, label)
        if split is None or label.get("split") == split:
            cases.append(case)
    return cases


def overlay(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def project_test_command(root: Path) -> list[str]:
    if (root / "package.json").is_file():
        return ["npm", "test"]
    if (root / "pyproject.toml").is_file():
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    raise ValueError(f"cannot infer test command for {root}")


def install_regression(case: Case, root: Path) -> None:
    python_test = case.root / "regress_test.py"
    javascript_test = case.root / "regress_test.js"
    if python_test.is_file():
        destination = root / "tests" / "test_regression.py"
    elif javascript_test.is_file():
        destination = root / "test" / "regression.test.js"
    else:
        raise ValueError(f"{case.case_id}: violation has no regress_test.*")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_test if python_test.is_file() else javascript_test, destination)


def materialize(case: Case, target: Path, *, changed: bool = True, regression: bool = False) -> None:
    shutil.copytree(case.root / "base", target)
    if changed:
        overlay(case.root / "change", target)
    if regression:
        install_regression(case, target)


def init_review_repo(case: Case, target: Path) -> tuple[str, str]:
    """Materialize and commit base and changed revisions, returning their SHAs."""
    materialize(case, target, changed=False)
    git = ["git", "-c", "user.name=Cerno Evals", "-c", "user.email=evals@cerno.local"]
    run([*git, "init", "-q"], target, check=True)
    run([*git, "add", "-A"], target, check=True)
    run([*git, "commit", "-q", "-m", "base"], target, check=True)
    base = run(["git", "rev-parse", "HEAD"], target, check=True).stdout.strip()
    overlay(case.root / "change", target)
    run([*git, "add", "-A"], target, check=True)
    run([*git, "commit", "-q", "-m", "change"], target, check=True)
    head = run(["git", "rev-parse", "HEAD"], target, check=True).stdout.strip()
    return base, head


def unified_diff(case: Case) -> str:
    chunks = []
    paths = sorted(
        {path.relative_to(case.root / "base") for path in (case.root / "base").rglob("*") if path.is_file()}
        | {path.relative_to(case.root / "change") for path in (case.root / "change").rglob("*") if path.is_file()}
    )
    for relative in paths:
        base_path = case.root / "base" / relative
        change_path = case.root / "change" / relative
        before = base_path.read_text().splitlines(keepends=True) if base_path.is_file() else []
        after = change_path.read_text().splitlines(keepends=True) if change_path.is_file() else before
        diff = difflib.unified_diff(before, after, f"a/{relative}", f"b/{relative}")
        chunks.extend(diff)
    return "".join(chunks)


def changed_lines(case: Case) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for change_path in sorted((case.root / "change").rglob("*")):
        if not change_path.is_file():
            continue
        relative = change_path.relative_to(case.root / "change")
        base_path = case.root / "base" / relative
        before = base_path.read_text().splitlines() if base_path.is_file() else []
        after = change_path.read_text().splitlines()
        matcher = difflib.SequenceMatcher(a=before, b=after)
        lines: set[int] = set()
        for tag, _a1, _a2, b1, b2 in matcher.get_opcodes():
            if tag in {"replace", "insert"}:
                lines.update(range(b1 + 1, b2 + 1))
        result[str(relative)] = lines
    return result


def cases_commit(root: Path = ROOT) -> str | None:
    result = run(["git", "log", "-1", "--format=%H", "--", "evals/cases"], root)
    return result.stdout.strip() or None


def cases_digest(cases_dir: Path = CASES_DIR) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in cases_dir.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(cases_dir)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()

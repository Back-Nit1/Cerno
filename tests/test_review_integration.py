"""End-to-end check of `cerno review --format md`.

Skipped until the review engine (cerno.review) is merged; activates automatically once it is.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cerno.review", reason="the review engine is not on this branch yet")


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root, check=True, capture_output=True, text=True, shell=False,
    )
    return proc.stdout.strip()


def _tiny_repo(root: Path) -> str:
    """A main branch plus a `change` branch that weakens a check; returns the head sha."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "--initial-branch=main")
    (root / "app.py").write_text(
        "def charge(user, amount):\n"
        "    if not user.get('verified'):\n"
        "        raise PermissionError('unverified')\n"
        "    return amount\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-q", "-b", "change")
    (root / "app.py").write_text(
        "def charge(user, amount):\n"
        "    return amount\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "drop the verification check")
    return _git(root, "rev-parse", "HEAD")


def test_review_renders_markdown_without_an_api_key(tmp_path):
    repo = tmp_path / "tiny"
    head = _tiny_repo(repo)
    env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    proc = subprocess.run(
        [sys.executable, "-m", "cerno", "review", "--repo", str(repo), "--base", "main",
         "--head", head, "--no-checks", "--format", "md"],
        capture_output=True, text=True, env=env, shell=False,
    )
    assert proc.returncode in {0, 1}, f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    lines = proc.stdout.strip().splitlines()
    assert lines, f"no Markdown on stdout; stderr: {proc.stderr}"
    assert lines[0].startswith("# ")
    assert "No production risks found" in lines[0] or "finding" in lines[0]
    assert lines[-1].startswith("base `")
    assert "model none" in lines[-1]  # no key, so no model was used

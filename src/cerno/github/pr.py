"""Resolve a pull request to its base and head commits with the gh CLI.

Read-only by construction: the only commands run are `gh pr view` and `git fetch`/`git cat-file`. Nothing
here comments, pushes, approves or merges.
"""

import json
import subprocess
from pathlib import Path

INSTALL_HINT = {
    "gh": "install the GitHub CLI (https://cli.github.com) or pass --base/--head explicitly",
    "git": "install git",
}
AUTH_MARKERS = ("gh auth login", "authentication", "not logged in", "http 401", "bad credentials")
MISSING_MARKERS = ("could not resolve to a pullrequest", "no pull requests found", "http 404", "not found")


def _capture(argv: list[str], root: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, cwd=root, capture_output=True, text=True, errors="replace", shell=False,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RuntimeError(f"cannot run {argv[0]}: {INSTALL_HINT.get(argv[0], 'command not found')}") from exc


def _gh_pr_view(root: Path, number: int) -> dict:
    proc = _capture(["gh", "pr", "view", str(number), "--json", "baseRefOid,headRefOid"], root)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        low = stderr.lower()
        if any(marker in low for marker in AUTH_MARKERS):
            raise RuntimeError(f"gh is not authenticated for this repository: run `gh auth login` ({stderr})")
        if any(marker in low for marker in MISSING_MARKERS):
            raise RuntimeError(f"pull request #{number} was not found in this repository ({stderr})")
        raise RuntimeError(f"gh pr view {number} failed: {stderr or f'exit code {proc.returncode}'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh pr view {number} did not return JSON: {proc.stdout.strip()[:200]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"gh pr view {number} returned {type(data).__name__}, expected an object")
    return data


def _has_commit(root: Path, sha: str) -> bool:
    return _capture(["git", "cat-file", "-e", f"{sha}^{{commit}}"], root).returncode == 0


def resolve_pr(root: Path, number: int) -> tuple[str, str]:
    """Return (base_sha, head_sha) for pull request `number`, fetching the head commit if it is missing."""
    data = _gh_pr_view(root, number)
    base, head = data.get("baseRefOid"), data.get("headRefOid")
    if not base or not head:
        raise RuntimeError(f"pull request #{number} has no base or head commit: {json.dumps(data)[:200]}")
    if not _has_commit(root, head):
        # ponytail: fetch from `origin`; add remote detection if a repository names its GitHub remote otherwise.
        proc = _capture(["git", "fetch", "--quiet", "origin", f"pull/{number}/head"], root)
        if proc.returncode != 0 or not _has_commit(root, head):
            raise RuntimeError(
                f"head commit {head} of pull request #{number} is not available locally and fetching "
                f"pull/{number}/head from origin did not provide it: {proc.stderr.strip() or 'no error output'}"
            )
    return str(base), str(head)

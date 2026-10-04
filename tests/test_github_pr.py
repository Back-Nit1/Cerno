import json
import subprocess
from pathlib import Path

import pytest

from cerno.github import pr

BASE = "9f1c2e8a7b3d4c5e6f708192a3b4c5d6e7f80912"
HEAD = "5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f"
VIEW = ["gh", "pr", "view", "7", "--json", "baseRefOid,headRefOid"]


class FakeRunner:
    """Replaces subprocess.run; `replies` maps an argv prefix tuple to (returncode, stdout, stderr)."""

    def __init__(self, replies: dict, missing: set[str] | None = None):
        self.replies = replies
        self.missing = missing or set()
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] in self.missing:
            raise FileNotFoundError(argv[0])
        for prefix, (code, out, err) in self.replies.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(argv, code, out, err)
        raise AssertionError(f"unexpected command: {argv}")


def _install(monkeypatch, runner: FakeRunner) -> FakeRunner:
    monkeypatch.setattr(subprocess, "run", runner)
    return runner


def _view_ok(base: str = BASE, head: str = HEAD) -> tuple[int, str, str]:
    return 0, json.dumps({"baseRefOid": base, "headRefOid": head}), ""


def test_returns_base_and_head_without_fetching_when_the_head_is_local(monkeypatch):
    runner = _install(monkeypatch, FakeRunner({
        tuple(VIEW): _view_ok(),
        ("git", "cat-file"): (0, "", ""),
    }))
    assert pr.resolve_pr(Path("/repo"), 7) == (BASE, HEAD)
    assert runner.calls[0] == VIEW
    assert not any(call[:2] == ["git", "fetch"] for call in runner.calls)


def test_fetches_pull_head_when_the_head_commit_is_missing(monkeypatch):
    seen_cat_file = []

    class Runner(FakeRunner):
        def __call__(self, argv, **kwargs):
            if argv[:2] == ["git", "cat-file"]:
                seen_cat_file.append(argv)
                return subprocess.CompletedProcess(argv, 0 if len(seen_cat_file) > 1 else 1, "", "")
            return super().__call__(argv, **kwargs)

    runner = _install(monkeypatch, Runner({
        tuple(VIEW): _view_ok(),
        ("git", "fetch"): (0, "", ""),
    }))
    assert pr.resolve_pr(Path("/repo"), 7) == (BASE, HEAD)
    assert ["git", "fetch", "--quiet", "origin", "pull/7/head"] in runner.calls


def test_runs_in_the_given_repository_and_never_writes_to_github(monkeypatch):
    captured = {}

    class Runner(FakeRunner):
        def __call__(self, argv, **kwargs):
            captured.setdefault("cwd", kwargs.get("cwd"))
            captured.setdefault("shell", kwargs.get("shell"))
            return super().__call__(argv, **kwargs)

    runner = _install(monkeypatch, Runner({tuple(VIEW): _view_ok(), ("git", "cat-file"): (0, "", "")}))
    pr.resolve_pr(Path("/repo"), 7)
    assert captured == {"cwd": Path("/repo"), "shell": False}
    forbidden = {"comment", "review", "merge", "close", "edit", "push", "ready"}
    for call in runner.calls:
        assert forbidden.isdisjoint(call), call
        assert call[0] in {"gh", "git"}


def test_missing_gh_is_an_actionable_error(monkeypatch):
    _install(monkeypatch, FakeRunner({}, missing={"gh"}))
    with pytest.raises(RuntimeError, match="install the GitHub CLI"):
        pr.resolve_pr(Path("/repo"), 7)


def test_unauthenticated_gh_points_at_gh_auth_login(monkeypatch):
    _install(monkeypatch, FakeRunner({
        tuple(VIEW): (4, "", "gh: To get started with GitHub CLI, please run: gh auth login"),
    }))
    with pytest.raises(RuntimeError, match="gh auth login"):
        pr.resolve_pr(Path("/repo"), 7)


@pytest.mark.parametrize("stderr", [
    "GraphQL: Could not resolve to a PullRequest with the number of 7.",
    "no pull requests found for branch",
    "HTTP 404: Not Found",
])
def test_unknown_pull_request_says_it_was_not_found(monkeypatch, stderr):
    _install(monkeypatch, FakeRunner({tuple(VIEW): (1, "", stderr)}))
    with pytest.raises(RuntimeError, match="pull request #7 was not found"):
        pr.resolve_pr(Path("/repo"), 7)


def test_other_gh_failures_surface_their_stderr(monkeypatch):
    _install(monkeypatch, FakeRunner({tuple(VIEW): (1, "", "HTTP 500: upstream unavailable")}))
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        pr.resolve_pr(Path("/repo"), 7)


@pytest.mark.parametrize("stdout", ["not json at all", "[]", '{"baseRefOid": "", "headRefOid": ""}'])
def test_unusable_gh_output_is_rejected(monkeypatch, stdout):
    _install(monkeypatch, FakeRunner({tuple(VIEW): (0, stdout, "")}))
    with pytest.raises(RuntimeError):
        pr.resolve_pr(Path("/repo"), 7)


def test_failed_fetch_reports_that_the_head_is_unavailable(monkeypatch):
    _install(monkeypatch, FakeRunner({
        tuple(VIEW): _view_ok(),
        ("git", "cat-file"): (1, "", ""),
        ("git", "fetch"): (128, "", "fatal: couldn't find remote ref pull/7/head"),
    }))
    with pytest.raises(RuntimeError, match="couldn't find remote ref"):
        pr.resolve_pr(Path("/repo"), 7)

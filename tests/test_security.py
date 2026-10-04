"""Mitigations for untrusted input: settings from a checkout, refs, rules, model spend, the local API."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cerno.analysis import git
from cerno.api.app import create_app
from cerno.cli import app as cli
from cerno.config import Settings
from cerno.llm.openai import LLM, ProviderError
from cerno.review.rules import MAX_FILE_RULES, RulesError, load_rules
from tests.conftest import FakeClient, run_git, write


def test_settings_from_a_checkout_cannot_lift_the_caps(repo, monkeypatch):
    write(repo, {".env": "CERNO_MAX_TOOL_STEPS=100000\nCERNO_MAX_MODEL_CALLS=999999999\n"})
    monkeypatch.delenv("CERNO_NO_DOTENV", raising=False)
    settings = Settings.load(repo)
    assert settings.max_tool_steps == 30 and settings.max_model_calls == 2000
    monkeypatch.setenv("CERNO_NO_DOTENV", "1")
    assert Settings.load(repo).max_tool_steps == 8


def test_model_budget_stops_the_run():
    llm = LLM(Settings(max_model_calls=2), client=FakeClient([]))
    llm.create("t", input=[])
    llm.create("t", input=[])
    with pytest.raises(ProviderError, match="budget exhausted"):
        llm.create("t", input=[])
    spent = LLM(Settings(max_total_tokens=1000), client=FakeClient([]))
    spent.input_tokens = 1000
    with pytest.raises(ProviderError, match="budget exhausted"):
        spent.create("t", input=[])


def test_calls_per_minute_waits(monkeypatch):
    waits = []
    monkeypatch.setattr("cerno.llm.openai.time.sleep", waits.append)
    llm = LLM(Settings(max_calls_per_minute=1), client=FakeClient([]))
    llm.create("t", input=[])
    assert not waits
    llm.create("t", input=[])
    assert len(waits) == 1 and 0 < waits[0] <= 60


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-n", "a b", "", "main\n--all"])
def test_option_shaped_refs_are_refused(ref):
    with pytest.raises(git.GitError):
        git.safe_ref(ref)


def test_review_rules_come_from_the_base_not_the_change(repo):
    write(repo, {".cerno.toml": '[[rule]]\nid = "PAY-1"\ntext = "Refunds never exceed the capture."\n'})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "rules")
    base_tree = git.head_tree(repo)
    write(repo, {".cerno.toml": '[review]\nbuiltin = false\n'})  # the change tries to switch everything off
    assert load_rules(repo).rules == []
    ids = {r.id for r in load_rules(repo, base_tree=base_tree).rules}
    assert "PAY-1" in ids and "CERNO-AUTHZ" in ids


def test_rules_file_is_bounded(repo):
    rule = '[[rule]]\nid = "R{n}"\ntext = "x"\n'
    write(repo, {".cerno.toml": "".join(rule.format(n=n) for n in range(MAX_FILE_RULES + 1))})
    with pytest.raises(RulesError, match="at most"):
        load_rules(repo)


def test_api_refuses_foreign_host_and_cross_origin_writes(repo):
    app = create_app(repo)
    assert TestClient(app, base_url="http://evil.example").get("/api/health").status_code == 400
    local = TestClient(app, base_url="http://127.0.0.1")
    assert local.get("/api/health").status_code == 200
    assert local.post("/api/history/index", headers={"origin": "http://evil.example"}).status_code == 403
    assert local.post("/api/history/markers", json={"kind": "good", "commit": "--output=x"}).status_code == 400


def test_dashboard_refuses_to_bind_a_network_interface(repo):
    result = CliRunner().invoke(cli, ["dashboard", "--repo", str(repo), "--host", "0.0.0.0"])
    assert result.exit_code == 2

"""`cerno review` end to end: output, exit codes, and the flags the contract names."""

import json

import pytest
from conftest import FakeClient, block, offending_line, run_git, write
from typer.testing import CliRunner

from cerno.cli import app
from cerno.config import Settings
from cerno.llm.openai import LLM

runner = CliRunner()


def scripted(monkeypatch, responses):
    """Make the engine build its LLM around a scripted client instead of a real one."""
    def factory(settings, db=None, session_id=None, client=None):
        return LLM(settings, db, session_id, client=FakeClient(list(responses)))

    monkeypatch.setattr("cerno.review.engine.LLM", factory)


def submit(status="VIOLATED", level="structural", locations=(), label=None):
    import re

    def build(**kwargs):
        text = json.dumps(kwargs["input"], default=str)
        pattern = rf"{label} \[(E-[0-9a-f]{{10}})\]" if label else r"E-[0-9a-f]{10}"
        ids = list(dict.fromkeys(re.findall(pattern, text)))
        return [block("function_call", call_id="c1", name="submit_verdict", arguments=json.dumps({
            "status": status, "evidence_level": level, "summary": "requireAdmin no longer guards deleteProject.",
            "evidence_ids": ids, "missing_evidence": [], "locations": list(locations),
        }))]

    return build


def review(repo, *args, monkeypatch=None, responses=()):
    if monkeypatch is not None:
        scripted(monkeypatch, responses)
    out = repo / "report.json"
    result = runner.invoke(app, ["review", "--repo", str(repo), "--out", str(out), *args])
    payload = json.loads(out.read_text()) if out.exists() else None
    return result, payload


def test_clean_diff_exits_zero_with_no_findings(review_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    run_git(review_repo, "checkout", "-q", "main")
    result, payload = review(review_repo, "--head", "main", "--no-checks")
    assert result.exit_code == 0
    assert payload["findings"] == [] and payload["schema_version"] == 1
    assert payload["model"] is None and payload["rules_source"] == ["builtin"]
    assert {r["verdict"] for r in payload["rules"]} == {"UNCERTAIN"}


def test_a_confirmed_finding_exits_one(review_repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    line = offending_line(review_repo)
    responses = [
        [block("function_call", call_id="c1", name="run_project_check", arguments=json.dumps({"kind": "test"}))],
        submit(level="test", locations=[{"path": "src/auth.js", "line": line, "end_line": line}]),
    ]
    result, payload = review(review_repo, "--rules", str(_only_authz(review_repo)), monkeypatch=monkeypatch,
                             responses=responses)
    assert result.exit_code == 1
    shown = [f for f in payload["findings"] if f["shown"]]
    assert len(shown) == 1
    assert shown[0]["path"] == "src/auth.js" and shown[0]["status"] == "confirmed"
    assert payload["checks"][0]["kind"] == "test"
    assert payload["head"]["sha"] is None and payload["base"]["ref"] == "main"


def _only_authz(repo):
    """A rules file that leaves one built-in rule on, so one scripted response is enough."""
    path = repo / "review.toml"
    path.write_text('[review]\ndisable = ["CERNO-CONTRACT", "CERNO-DATA-EXPOSURE", "CERNO-DESTRUCTIVE", '
                    '"CERNO-BEHAVIOR"]\n')
    return path


def test_no_api_key_reports_insufficient_and_exits_zero(review_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result, payload = review(review_repo, "--no-checks")
    assert result.exit_code == 0
    assert [i["rule_id"] for i in payload["insufficient"]] == [r["id"] for r in payload["rules"]]
    assert payload["usage"]["model_calls"] == 0 and payload["usage"]["cost_usd"] is None
    assert payload["checks"] == []


def test_bad_ref_exits_two(review_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result, _ = review(review_repo, "--base", "no-such-ref")
    assert result.exit_code == 2


def test_unreadable_rules_file_exits_two(review_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (review_repo / ".cerno.toml").write_text('[[rule]]\nid = "A"\nseverity = "blocker"\n')
    result, _ = review(review_repo, "--no-checks")
    assert result.exit_code == 2


@pytest.mark.parametrize("bad", ["html", "yaml"])
def test_unknown_format_exits_two(review_repo, bad):
    result, _ = review(review_repo, "--format", bad)
    assert result.exit_code == 2


def test_staged_reviews_the_index_only(review_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    write(review_repo, {"src/auth.js": (review_repo / "src/auth.js").read_text() + "// staged\n"})
    run_git(review_repo, "add", "src/auth.js")
    write(review_repo, {"README.md": "unstaged edit\n"})
    result, payload = review(review_repo, "--staged", "--no-checks")
    assert result.exit_code == 0
    assert payload["head"]["ref"] == "(staged)" and payload["head"]["sha"] is None
    assert payload["base"]["ref"] == "HEAD"


def test_json_goes_to_stdout_when_out_is_absent(review_repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(app, ["review", "--repo", str(review_repo), "--no-checks"])
    assert result.exit_code == 0
    assert json.loads(result.stdout[result.stdout.index("{"):])["schema_version"] == 1


def test_settings_pick_up_the_price_environment_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("CERNO_PRICE_INPUT_PER_MTOK", "1.25")
    monkeypatch.setenv("CERNO_PRICE_OUTPUT_PER_MTOK", "10")
    monkeypatch.setenv("CERNO_REQUEST_TIMEOUT", "45")
    settings = Settings.load(tmp_path)
    assert (settings.price_input_per_mtok, settings.price_output_per_mtok) == (1.25, 10.0)
    assert settings.request_timeout == 45.0


def test_md_and_pr_exit_two_when_their_module_cannot_be_imported(review_repo, monkeypatch):
    """Both paths import lazily, so a missing module must be exit 2, never a traceback."""
    import builtins

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith(("cerno.report", "cerno.github")):
            raise ImportError(f"No module named {name!r}")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    for args in (["--format", "md", "--no-checks"], ["--pr", "7"]):
        result = runner.invoke(app, ["review", "--repo", str(review_repo), *args])
        assert result.exit_code == 2

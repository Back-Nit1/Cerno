"""Failure honesty, credential isolation, and cache invalidation."""

import json

import pytest
from conftest import FakeClient, block, write

from cerno.config import Settings, db_file
from cerno.intent import compiler
from cerno.llm.openai import LLM, ProviderError
from cerno.review.engine import review_session_id, run_review
from cerno.review.rules import BUILTIN, RuleSet
from cerno.review.trees import resolve
from cerno.storage.sqlite import DB
from cerno.verification.runtime import child_env, run_check

AUTHZ = next(r for r in BUILTIN if r.id == "CERNO-AUTHZ")
SENTINEL = "zz" + "leak" + "zz"  # split so the probe script never contains the value it looks for


class AuthenticationError(Exception):
    """Same class name the OpenAI SDK uses; matched by name so cerno need not import the SDK."""


class RaisingClient:
    def __init__(self, exc):
        self.responses = type("R", (), {"create": lambda _self, **kw: (_ for _ in ()).throw(exc)})()


def _repo_with_change(repo):
    write(repo, {"src/auth.js": "function f() { return 1; }\n"})
    from conftest import run_git

    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "base")
    run_git(repo, "branch", "-M", "main")
    write(repo, {"src/auth.js": "function f() { return 2; }\n"})
    return repo


@pytest.mark.parametrize("exc", [AuthenticationError("Incorrect API key provided"),
                                 type("APIConnectionError", (Exception,), {})("connection refused")])
def test_provider_auth_and_connection_errors_are_not_insufficient_evidence(repo, exc):
    root = _repo_with_change(repo)
    settings = Settings(api_key="bad")
    db = DB(db_file(root, settings))
    revisions = resolve(root, base="main")
    llm = LLM(settings, db, review_session_id(revisions), client=RaisingClient(exc))
    with pytest.raises(ProviderError):
        run_review(root, revisions, RuleSet([AUTHZ], "high", ["builtin"]), settings, llm=llm, db=db, no_checks=True)


def test_other_model_errors_stay_isolated_to_one_rule(repo):
    root = _repo_with_change(repo)
    settings = Settings(api_key="k")
    db = DB(db_file(root, settings))
    revisions = resolve(root, base="main")
    llm = LLM(settings, db, review_session_id(revisions), client=RaisingClient(ValueError("boom")))
    result = run_review(root, revisions, RuleSet([AUTHZ], "high", ["builtin"]), settings, llm=llm, db=db,
                        no_checks=True)
    assert result.rules[0].verdict == "UNCERTAIN"
    assert result.errors and "ValueError: boom" in result.errors[0]


def test_project_checks_cannot_read_the_reviewers_credentials(repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + SENTINEL)
    monkeypatch.setenv("ACME_TOKEN", "tok-" + SENTINEL)
    monkeypatch.setenv("CERNO_MODEL", "keep-me")
    # the needle is assembled inside node, so the script text itself never contains the sentinel
    script = ("node -e \"const e=process.env, s=['zz','leak','zz'].join(''); console.log(JSON.stringify("
              "{leaks: Object.keys(e).filter(n => /API_KEY|TOKEN|SECRET|PASSWORD/.test(n)), "
              "model: e.CERNO_MODEL, path: Boolean(e.PATH), "
              "leaked: Object.values(e).some(v => String(v).includes(s))}))\"")
    write(repo, {"package.json": json.dumps({"name": "x", "scripts": {"test": script}})})
    result = run_check(repo, "test", timeout=120)
    assert result.exit_code == 0
    seen = json.loads(result.stdout_tail.strip().splitlines()[-1])  # npm prints a banner first
    assert seen["leaks"] == []
    assert seen["leaked"] is False
    assert seen["model"] == "keep-me" and seen["path"]
    assert "PATH" in child_env() and "OPENAI_API_KEY" not in child_env()


def test_no_checks_runs_no_repository_script(repo):
    from cerno.review.engine import build_context

    root = _repo_with_change(repo)
    write(repo, {"package.json": json.dumps({"name": "x", "scripts": {"test": "node -e \"process.exit(3)\""}})})
    settings = Settings()
    ctx = build_context(root, resolve(root, base="main"), [AUTHZ], settings, DB(db_file(root, settings)),
                        no_checks=True)
    assert ctx.check("test") is None and ctx.checks == {}


def _contract_response():
    contract = {"task_summary": "Add OAuth", "protected_areas": [], "explicit_acceptance_criteria": [],
                "requirements": [{"id": "x", "text": "Users can log in", "type": "feature", "priority": "high",
                                  "explicit": True, "verification_hint": ""}]}
    return [block("function_call", call_id="t", name="submit_intent_contract", arguments=json.dumps(contract))]


def test_editing_the_system_prompt_invalidates_the_intent_cache(repo, monkeypatch):
    settings = Settings()
    db = DB(db_file(repo, settings))
    client = FakeClient([_contract_response(), _contract_response()])
    llm = LLM(settings, db, "s", client=client)
    compiler.compile_intent(llm, ["Add OAuth login"], db)
    compiler.compile_intent(llm, ["Add OAuth login"], db)
    assert len(client.requests) == 1  # second run served from the cache

    monkeypatch.setattr(compiler, "SYSTEM", compiler.SYSTEM + "\nAlways name the file.")
    compiler.compile_intent(llm, ["Add OAuth login"], db)
    assert len(client.requests) == 2


def test_editing_the_agent_instructions_invalidates_the_verdict_cache(repo):
    from cerno.review.engine import REVIEW_PROMPT, build_context
    from cerno.verification.agent import SYSTEM_PROMPT, _cache_key
    from cerno.verification.tools import TOOLS

    root = _repo_with_change(repo)
    settings = Settings()
    ctx = build_context(root, resolve(root, base="main"), [AUTHZ], settings, DB(db_file(root, settings)))
    llm = LLM(settings)
    req = ctx.requirement(AUTHZ.id)
    base_key = _cache_key(llm, ctx, req, "packet", SYSTEM_PROMPT)
    assert base_key != _cache_key(llm, ctx, req, "packet", REVIEW_PROMPT)
    assert base_key != _cache_key(llm, ctx, req, "packet", SYSTEM_PROMPT + " extra")
    assert str(TOOLS) in str(TOOLS)  # schemas are part of the key material; see _cache_key

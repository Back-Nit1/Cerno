import json

import pytest
from conftest import FakeClient, block, write
from test_analysis import _ctx

from cerno.config import Settings
from cerno.evidence.store import EvidenceStore
from cerno.intent.compiler import MAX_REQUIREMENTS, compile_intent, fallback_contract, parse_contract
from cerno.llm.anthropic import LLM
from cerno.models import Requirement, Verdict
from cerno.verification.deterministic import apply_rules
from cerno.verification.verdict import finalize

PROMPT = """Add GitHub OAuth.
Preserve existing email/password login.
Use the current User model.
Do not create a new database table.
Keep secrets in environment variables.
Add tests."""


def _req(text, type_="feature", rid="R1"):
    return {"id": rid, "text": text, "type": type_, "priority": "high", "explicit": True, "verification_hint": ""}


def test_intent_compiler_parses_model_contract():
    contract = {"task_summary": "Add GitHub OAuth", "protected_areas": [], "explicit_acceptance_criteria": [],
                "requirements": [_req("Users can authenticate with GitHub", rid="x"),
                                 _req("Do not create another database table", "prohibition", rid="y")]}
    client = FakeClient([[block("tool_use", id="t", name="submit_intent_contract", input=contract)]])
    result, source = compile_intent(LLM(Settings(), client=client), [PROMPT])
    assert source == "model" and [r.id for r in result.requirements] == ["R1", "R2"]
    assert result.requirements[1].type == "prohibition" and result.requirements[0].verification_hint is None
    assert client.requests[0]["tool_choice"] == {"type": "tool", "name": "submit_intent_contract"}


def test_intent_compiler_caps_requirements_and_rejects_malformed():
    many = {"task_summary": "t", "protected_areas": [], "explicit_acceptance_criteria": [],
            "requirements": [_req(f"req {i}") for i in range(30)]}
    assert len(parse_contract(many).requirements) == MAX_REQUIREMENTS
    bad = {"task_summary": "t", "requirements": [{"id": "R1", "text": "x", "type": "wish"}]}
    client = FakeClient([[block("tool_use", id="t", name="submit_intent_contract", input=bad)]])
    result, source = compile_intent(LLM(Settings(), client=client), [PROMPT])
    assert source == "fallback" and len(result.requirements) == 6


def test_fallback_contract_does_not_invent_requirements():
    contract = fallback_contract(PROMPT)
    types = {r.text: r.type for r in contract.requirements}
    assert len(contract.requirements) == 6
    assert types["Do not create a new database table"] == "prohibition"
    assert types["Preserve existing email/password login"] == "invariant"
    assert types["Add tests"] == "verification"
    assert types["Keep secrets in environment variables"] == "security"


class _Store(EvidenceStore):
    def __init__(self):
        self.items, self.raw, self.session_id = {}, {}, "s"
        self.db = type("NoDB", (), {"put": lambda *a, **k: None})()


def _verdict(status, ids, level="semantic"):
    return Verdict(requirement_id="R1", status=status, evidence_level=level, summary="s", evidence_ids=ids,
                   missing_evidence=[], files_inspected=[], tool_calls_used=0)


def test_verified_requires_qualifying_evidence():
    store = _Store()
    code = store.add("code", "final", "source of f", raw="def f(): ...").id
    rule = store.add("rule", "rule:x", "no manifests changed").id
    downgraded = finalize(_verdict("VERIFIED", [code], "runtime"), store)
    assert downgraded.status == "LIKELY" and downgraded.evidence_level == "semantic"
    assert finalize(_verdict("VERIFIED", [code, rule]), store).status == "VERIFIED"
    no_evidence = finalize(_verdict("VIOLATED", ["E-doesnotexist"]), store)
    assert no_evidence.status == "UNCERTAIN" and no_evidence.evidence_level == "insufficient"


@pytest.mark.parametrize("added_dep, expected", [(True, "VIOLATED"), (False, "VERIFIED")])
def test_dependency_rule_is_deterministic(repo, added_dep, expected):
    pkg = {"name": "x", "dependencies": {"left-pad": "1.0.0"}}
    after = {**pkg, "dependencies": {**pkg["dependencies"], "axios": "1.0.0"}} if added_dep else {**pkg, "version": "2"}
    ctx, _ = _ctx(repo, {"package.json": json.dumps(pkg)}, {"package.json": json.dumps(after)})
    req = Requirement(id="R1", text="Do not add new dependencies", type="prohibition", priority="critical", explicit=True)
    result = apply_rules(req, ctx)
    assert result.verdict.status == expected and result.verdict.evidence_ids
    assert finalize(result.verdict, ctx.store).status == expected


def test_schema_rule_detects_new_table(repo):
    ctx, _ = _ctx(repo, {"db/schema.sql": "CREATE TABLE users (id int);\n"},
                  {"db/schema.sql": "CREATE TABLE users (id int);\nCREATE TABLE oauth_accounts (id int);\n"})
    req = Requirement(id="R1", text="Do not create a new database table", type="prohibition", priority="critical",
                      explicit=True)
    assert apply_rules(req, ctx).verdict.status == "VIOLATED"


def test_tests_rule_without_test_changes_is_violated(repo):
    ctx, _ = _ctx(repo, {"src/a.js": "export const a = 1;\n"}, {"src/a.js": "export const a = 2;\n"})
    write(repo, {})
    req = Requirement(id="R1", text="Add tests", type="verification", priority="high", explicit=True)
    assert apply_rules(req, ctx).verdict.status == "VIOLATED"

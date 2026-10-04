"""Review engine: tree resolution, the gate, and end-to-end runs against a scripted model."""

import json
import re

import pytest
from conftest import FakeClient, block, run_git, write

from cerno.analysis.git import GitError
from cerno.config import Settings, db_file
from cerno.llm.openai import LLM
from cerno.models import CodeLocation, Verdict
from cerno.review.engine import build_context, gate, review_session_id, run_review
from cerno.review.models import ReviewRule
from cerno.review.rules import BUILTIN, RuleSet
from cerno.review.trees import resolve
from cerno.storage.sqlite import DB

AUTHZ = next(r for r in BUILTIN if r.id == "CERNO-AUTHZ")

BASE_AUTH = """function requireAdmin(user) {
  if (!user || !user.isAdmin) {
    throw new Error("forbidden");
  }
  return true;
}

function deleteProject(user, id) {
  requireAdmin(user);
  return db.remove(id);
}

module.exports = { requireAdmin, deleteProject };
"""

HEAD_AUTH = BASE_AUTH.replace("  requireAdmin(user);\n", "")

TEST_FILE = """const { deleteProject } = require("../src/auth");

test("deleteProject rejects non-admins", () => {
  deleteProject({}, 1);
});
"""

PACKAGE = json.dumps({"name": "demo", "scripts": {"test": "node -e \"process.exit(0)\""}}, indent=2) + "\n"


@pytest.fixture
def review_repo(repo):
    """main holds the guarded version; `feature` (checked out) removed the permission check."""
    write(repo, {"src/auth.js": BASE_AUTH, "tests/auth.test.js": TEST_FILE, "package.json": PACKAGE})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "base")
    run_git(repo, "branch", "-M", "main")
    run_git(repo, "checkout", "-qb", "feature")
    write(repo, {"src/auth.js": HEAD_AUTH})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "drop the admin check")
    return repo


def offending_line(repo) -> int:
    lines = (repo / "src/auth.js").read_text().splitlines()
    return lines.index("  return db.remove(id);") + 1


def _call(name: str, args: dict):
    return [block("function_call", name=name, call_id="c1", arguments=json.dumps(args))]


def _ids(kwargs, label: str | None = None) -> list[str]:
    text = json.dumps(kwargs["input"], default=str)
    if label:
        return re.findall(rf"{label} \[(E-[0-9a-f]{{10}})\]", text)
    return list(dict.fromkeys(re.findall(r"E-[0-9a-f]{10}", text)))


def submit(status="VIOLATED", level="structural", summary="Removed requireAdmin from deleteProject.",
           label=None, locations=None, missing=()):
    """A scripted submit_verdict that cites whichever evidence ids the packet actually produced."""

    def build(**kwargs):
        return _call("submit_verdict", {
            "status": status, "evidence_level": level, "summary": summary,
            "evidence_ids": _ids(kwargs, label), "missing_evidence": list(missing),
            "locations": locations(kwargs) if callable(locations) else (locations or []),
        })

    return build


def run(repo, responses=(), rules=None, no_checks=True, with_key=True, base="main", head=None, staged=False):
    settings = Settings(api_key="test-key" if with_key else None)
    db = DB(db_file(repo, settings))
    revisions = resolve(repo, base=base, head=head, staged=staged)
    client = FakeClient(list(responses)) if with_key else None
    llm = LLM(settings, db, review_session_id(revisions), client=client)
    ruleset = rules or RuleSet([AUTHZ], "high", ["builtin"])
    return run_review(repo, revisions, ruleset, settings, llm=llm, db=db, no_checks=no_checks)


# --- tree resolution ---------------------------------------------------------------------------

def test_resolve_uses_the_merge_base_tree(review_repo):
    revisions = resolve(review_repo, base="main")
    assert revisions.merge_base == run_git_out(review_repo, "rev-parse", "main")
    assert revisions.head.sha is None and revisions.head.ref == "feature"


def run_git_out(root, *args) -> str:
    from cerno.analysis import git

    return git.git(root, *args).strip()


def test_bad_ref_raises_with_gits_message(review_repo):
    with pytest.raises(GitError) as exc:
        resolve(review_repo, base="no-such-branch")
    assert "no-such-branch" in str(exc.value)


def test_staged_sees_only_staged_changes(review_repo):
    write(review_repo, {"src/auth.js": HEAD_AUTH + "// staged\n", "README.md": "unstaged\n"})
    run_git(review_repo, "add", "src/auth.js")
    revisions = resolve(review_repo, staged=True)
    settings = Settings()
    ctx = build_context(review_repo, revisions, [AUTHZ], settings, DB(db_file(review_repo, settings)))
    assert [c.path for c in ctx.changes] == ["src/auth.js"]


# --- the gate ----------------------------------------------------------------------------------

def _ctx(repo):
    settings = Settings()
    revisions = resolve(repo, base="main")
    return build_context(repo, revisions, [AUTHZ], settings, DB(db_file(repo, settings)))


def _verdict(ctx, status="VIOLATED", types=("rule",)):
    ids = [ctx.store.add(t, f"src:{t}", f"{t} evidence").id for t in types]
    return Verdict(requirement_id=AUTHZ.id, status=status, evidence_level="structural", summary="s",
                   evidence_ids=ids, missing_evidence=[], files_inspected=[], tool_calls_used=0)


def test_gate_shows_a_violated_corroborated_in_diff_finding(review_repo):
    ctx = _ctx(review_repo)
    shown, reason = gate(ctx, AUTHZ, _verdict(ctx), "src/auth.js", offending_line(review_repo),
                         offending_line(review_repo), "high")
    assert shown and reason is None


@pytest.mark.parametrize("status", ["LIKELY", "UNCERTAIN", "VERIFIED"])
def test_gate_suppresses_everything_but_violated(review_repo, status):
    ctx = _ctx(review_repo)
    shown, reason = gate(ctx, AUTHZ, _verdict(ctx, status), "src/auth.js", offending_line(review_repo),
                         offending_line(review_repo), "high")
    assert not shown and "not VIOLATED" in reason


def test_gate_suppresses_below_min_severity(review_repo):
    ctx = _ctx(review_repo)
    low = ReviewRule(id="X", text="t", severity="medium")
    shown, reason = gate(ctx, low, _verdict(ctx), "src/auth.js", offending_line(review_repo),
                         offending_line(review_repo), "high")
    assert not shown and "below min_severity" in reason


def test_gate_suppresses_a_location_outside_the_diff(review_repo):
    ctx = _ctx(review_repo)
    shown, reason = gate(ctx, AUTHZ, _verdict(ctx), "src/auth.js", 1, 1, "high")
    assert not shown and "outside the lines this diff changed" in reason
    shown, reason = gate(ctx, AUTHZ, _verdict(ctx), "tests/auth.test.js", 1, 1, "high")
    assert not shown and "outside the lines this diff changed" in reason


def test_gate_suppresses_diff_only_evidence(review_repo):
    ctx = _ctx(review_repo)
    shown, reason = gate(ctx, AUTHZ, _verdict(ctx, types=("diff", "code")), "src/auth.js",
                         offending_line(review_repo), offending_line(review_repo), "high")
    assert not shown and "diff alone" in reason


def test_locations_outside_the_diff_are_dropped(review_repo):
    from cerno.verification.agent import valid_locations

    ctx = _ctx(review_repo)
    line = offending_line(review_repo)
    verdict = _verdict(ctx).model_copy(update={"locations": [
        CodeLocation(path="src/auth.js", line=line, end_line=line),
        CodeLocation(path="src/auth.js", line=1, end_line=1),
        CodeLocation(path="does/not/exist.js", line=line, end_line=line),
    ]})
    assert [(x.path, x.line) for x in valid_locations(ctx, verdict)] == [("src/auth.js", line)]


# --- end to end --------------------------------------------------------------------------------

def test_clean_diff_reports_nothing(review_repo):
    run_git(review_repo, "checkout", "-q", "main")
    result = run(review_repo, [submit(status="VERIFIED", level="structural")], head="main")
    assert result.findings == [] and not result.shown
    assert [r.verdict for r in result.rules] == ["VERIFIED"]


def test_removed_permission_check_with_test_evidence_is_shown_and_confirmed(review_repo):
    line = offending_line(review_repo)
    responses = [
        _call("run_project_check", {"kind": "test"}),
        submit(level="test", locations=[{"path": "src/auth.js", "line": line, "end_line": line}]),
    ]
    result = run(review_repo, responses, no_checks=False)
    shown = result.shown
    assert len(shown) == 1
    finding = shown[0]
    assert (finding.rule_id, finding.path, finding.line) == ("CERNO-AUTHZ", "src/auth.js", line)
    assert finding.status == "confirmed" and finding.suppressed_reason is None
    assert len(finding.id) == 12 and int(finding.id, 16) >= 0
    assert [c.kind for c in result.checks] == ["test"]


def test_the_same_verdict_citing_only_the_diff_is_suppressed(review_repo):
    line = offending_line(review_repo)
    responses = [
        submit(label="DIFF", locations=[{"path": "src/auth.js", "line": line, "end_line": line}]),
        submit(status="UNCERTAIN", level="insufficient", summary="Could not find a caller.",
               label="DIFF", missing=["a caller or test that exercises deleteProject"]),
    ]
    result = run(review_repo, responses)
    assert not result.shown
    assert "diff alone" in result.findings[0].suppressed_reason


def test_a_location_outside_the_diff_never_becomes_a_shown_finding(review_repo):
    responses = [submit(locations=[{"path": "src/auth.js", "line": 1, "end_line": 1}])]
    result = run(review_repo, responses)
    assert not result.shown
    assert "outside the lines this diff changed" in result.findings[0].suppressed_reason


def test_without_a_key_rules_land_in_insufficient(review_repo):
    result = run(review_repo, with_key=False)
    assert result.findings == [] and result.model is None
    assert [i.rule_id for i in result.insufficient] == ["CERNO-AUTHZ"]
    assert result.usage.model_calls == 0 and result.usage.cost_usd is None


def test_rules_whose_paths_match_nothing_are_skipped_without_a_model_call(review_repo):
    scoped = ReviewRule(id="PAY-001", text="Refunds must never exceed the captured amount.", severity="critical",
                        source="file", paths=["src/payments/**"])
    result = run(review_repo, [], rules=RuleSet([scoped], "high", ["builtin", ".cerno.toml"]))
    assert [(r.id, r.verdict) for r in result.rules] == [("PAY-001", "SKIPPED")]
    assert result.usage.model_calls == 0


def test_cost_is_null_without_prices_and_computed_with_them(review_repo):
    settings = Settings(api_key="k", price_input_per_mtok=1.0, price_output_per_mtok=2.0)
    db = DB(db_file(review_repo, settings))
    revisions = resolve(review_repo, base="main")
    llm = LLM(settings, db, review_session_id(revisions), client=FakeClient([submit(status="UNCERTAIN")]))
    result = run_review(review_repo, revisions, RuleSet([AUTHZ], "high", ["builtin"]), settings, llm=llm, db=db,
                        no_checks=True)
    assert result.usage.cost_usd == pytest.approx(100 / 1e6 * 1.0 + 20 / 1e6 * 2.0)
    assert result.usage.wall_time_s >= 0

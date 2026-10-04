"""The review engine: two git trees plus a rule set, through the existing verification path, to findings.

A review is `cerno analyze` with review rules standing in for requirements and refs standing in for session
snapshots, so everything here is assembly plus the gate that decides what a reviewer is allowed to see.
"""

import hashlib
import time
from pathlib import Path

from cerno.analysis import git
from cerno.analysis.graph import RepoIndex
from cerno.analysis.symbols import changed_symbols
from cerno.config import Settings, db_file
from cerno.evidence.store import EvidenceStore, level_of
from cerno.llm.openai import LLM
from cerno.models import IntentContract, Requirement, Verdict
from cerno.pipeline import verify_requirement
from cerno.review.models import (
    SEVERITY_ORDER,
    CheckSummary,
    Finding,
    FindingEvidence,
    Insufficient,
    ReviewResult,
    ReviewRule,
    ReviewUsage,
    RuleOutcome,
)
from cerno.review.rules import RuleSet, matches
from cerno.review.trees import Revisions
from cerno.storage.sqlite import DB
from cerno.verification.context import Context
from cerno.verification.runtime import detect_checks

# Evidence that is something other than the diff the model was already shown.
CORROBORATING_TYPES = {"graph", "tests", "rule", "check_test", "check_build", "check_lint", "check_typecheck"}
CONFIRMING_LEVELS = {"test", "runtime", "structural"}
PRIORITY = {"critical": "critical", "high": "high", "medium": "normal", "low": "normal"}
REVIEW_TABLES = ["evidence", "verdicts", "file_changes", "symbol_changes", "project_checks", "requirements"]
MAX_FINDINGS_PER_RULE = 5

REVIEW_PROMPT = """You are Cerno, reviewing one diff before it reaches production.

You are not asked whether a feature was built. You are asked one question only: does THIS diff introduce the
specific harm described in the rule you were given?

A reviewer's time is the scarce resource. A wrong or speculative finding costs more than a missed one.

Investigate before concluding. Start from the changed lines, then look outward: who calls the changed symbol,
what tests cover it, what the project's own checks say. Do not inspect code unrelated to the rule.

Status rules:
- VIOLATED: the diff introduces the harm, and you can name the changed line that does it plus something
  outside the diff (a caller, a test, a check result, a deterministic rule hit) that shows the consequence.
- VERIFIED: evidence establishes the harm is absent.
- LIKELY: the harm is probable but nothing outside the diff supports it.
- UNCERTAIN: the evidence cannot settle it. This is a correct, useful answer. Prefer it to guessing.

Never report style, naming, formatting, comments, dead code, type annotations, missing tests, performance,
or refactor suggestions, whatever the diff looks like. Silence on a clean diff is the right answer.

Finish by calling submit_verdict once. summary: 1-3 concrete sentences naming files, symbols and values.
locations: the changed lines the harm is at, in the head revision's numbering; a location outside the diff is
dropped. missing_evidence: what would settle any remaining doubt (empty if none)."""

CONFIRM_PROMPT = REVIEW_PROMPT + """

CONFIRMATION PASS. You already concluded VIOLATED, but you cited nothing beyond the diff you were shown.
Find a caller, a test, or a project check that demonstrates the consequence, or retract. Reading the diff
again is not confirmation. If you find nothing, submit UNCERTAIN and say what is missing."""


def review_session_id(revisions: Revisions) -> str:
    """Synthetic session id so storage and the verdict cache keep working for a review."""
    return f"review-{revisions.base.tree[:12]}-{revisions.head.tree[:12]}"


def _requirement(rule: ReviewRule) -> Requirement:
    return Requirement(id=rule.id, text=rule.text, type="prohibition", priority=PRIORITY[rule.severity],
                       explicit=True, verification_hint="Judge only the changes in this diff.")


def build_context(root: Path, revisions: Revisions, rules: list[ReviewRule], settings: Settings, db: DB,
                  no_checks: bool = False) -> Context:
    session_id = review_session_id(revisions)
    for table in REVIEW_TABLES:
        db.clear(table, session_id)
    base_tree, head_tree = revisions.base.tree, revisions.head.tree
    changes = git.get_changed_files(root, base_tree, head_tree)
    base_index = RepoIndex.from_tree(root, base_tree, db)
    head_index = RepoIndex.from_tree(root, head_tree, db)
    contract = IntentContract(task_summary=f"Review {revisions.base.ref}...{revisions.head.ref} for production risk",
                              requirements=[_requirement(r) for r in rules])
    return Context(root, session_id, settings, db, EvidenceStore(db, session_id), base_tree, head_tree, changes,
                   changed_symbols(changes, base_index, head_index), base_index, head_index, [], contract,
                   no_checks=no_checks)


def corroborated(ctx: Context, verdict: Verdict) -> bool:
    """True when the verdict cites something the diff alone does not provide."""
    return any((ev := ctx.store.get(i)) and ev.type in CORROBORATING_TYPES for i in verdict.evidence_ids)


def _line_text(root: Path, tree: str, path: str, line: int) -> str:
    lines = (git.read_blob(root, tree, path) or "").splitlines()
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def finding_id(rule_id: str, path: str, line_text: str) -> str:
    return hashlib.sha1(f"{rule_id}|{path}|{line_text}".encode()).hexdigest()[:12]


def _evidence(ctx: Context, verdict: Verdict) -> list[FindingEvidence]:
    out = []
    for i in verdict.evidence_ids:
        if ev := ctx.store.get(i):
            out.append(FindingEvidence(id=ev.id, type=ev.type, level=level_of(ev.type), path=ev.path,
                                       line_start=ev.line_start, summary=ev.summary))
    return out


def _in_diff(ctx: Context, path: str, line: int, end_line: int) -> bool:
    if ctx.change(path) is None:
        return False
    return any(start <= end_line and line <= end for start, end in ctx.changed_ranges(path))


def gate(ctx: Context, rule: ReviewRule, verdict: Verdict, path: str, line: int, end_line: int,
         min_severity: str) -> tuple[bool, str | None]:
    """The four conditions a finding must meet to be shown. Enforced in code, whatever the model said."""
    reasons = []
    if verdict.status != "VIOLATED":
        reasons.append(f"rule verdict is {verdict.status}, not VIOLATED")
    if SEVERITY_ORDER[rule.severity] < SEVERITY_ORDER[min_severity]:
        reasons.append(f"severity {rule.severity} is below min_severity {min_severity}")
    if not _in_diff(ctx, path, line, end_line):
        reasons.append("location is outside the lines this diff changed")
    if not corroborated(ctx, verdict):
        reasons.append("cited evidence is the diff alone: no caller, test, check result or rule hit")
    return (not reasons), ("; ".join(reasons) or None)


def _fallback_path(ctx: Context, verdict: Verdict) -> str | None:
    for i in verdict.evidence_ids:
        ev = ctx.store.get(i)
        if ev and ev.path and ctx.change(ev.path):
            return ev.path
    for path in verdict.files_inspected:
        if ctx.change(path):
            return path
    return ctx.changes[0].path if ctx.changes else None


def findings_for(ctx: Context, rule: ReviewRule, verdict: Verdict, head_tree: str,
                 min_severity: str) -> list[Finding]:
    """A VIOLATED or LIKELY verdict becomes findings; the gate decides which of them a reviewer sees."""
    if verdict.status not in {"VIOLATED", "LIKELY"}:
        return []
    spots = [(loc.path, loc.line, loc.end_line) for loc in verdict.locations[:MAX_FINDINGS_PER_RULE]]
    if not spots:
        path = _fallback_path(ctx, verdict)
        if path is None:
            return []
        spots = [(path, 1, 1)]
    sentence = verdict.summary.split(". ")[0].strip() or rule.text[:80]
    evidence = _evidence(ctx, verdict)
    status = "confirmed" if any(e.level in CONFIRMING_LEVELS for e in evidence) else "suspected"
    out = []
    for path, line, end_line in spots:
        shown, reason = gate(ctx, rule, verdict, path, line, end_line, min_severity)
        missing = f" Still missing: {verdict.missing_evidence[0]}" if verdict.missing_evidence else ""
        out.append(Finding(
            id=finding_id(rule.id, path, _line_text(ctx.root, head_tree, path, line)),
            rule_id=rule.id, title=sentence[:120], severity=rule.severity, status=status, shown=shown,
            suppressed_reason=reason, path=path, line=line, end_line=max(end_line, line),
            explanation=verdict.summary,
            next_action=f"Check {path}:{line} against {rule.id} and confirm the change is intended.{missing}",
            evidence=evidence,
        ))
    return out


def _confirm(llm: LLM, ctx: Context, rule: ReviewRule, verdict: Verdict) -> Verdict:
    """One further bounded attempt: confirm a diff-only VIOLATED with a caller or a test, or retract it."""
    from cerno.analysis.retrieval import build_packet, rank
    from cerno.verification.agent import verify_with_agent

    req = ctx.requirement(rule.id)
    packet = build_packet(req, ctx, rank(req, ctx), verdict.evidence_ids)
    packet += (f"\n\nEARLIER CONCLUSION (unconfirmed)\n{verdict.summary}\n"
               "It cited only the diff. Confirm it with a caller, a test, or a project check, or retract it.")
    retry = verify_with_agent(llm, ctx, req, packet, verdict.evidence_ids,
                              ["agent: confirmation pass (callers/tests only)"], CONFIRM_PROMPT)
    if retry.status == "VIOLATED" and corroborated(ctx, retry):
        return retry.model_copy(update={"locations": retry.locations or verdict.locations})
    return verdict.model_copy(update={
        "missing_evidence": list(dict.fromkeys([*verdict.missing_evidence, *retry.missing_evidence])),
    })


def run_review(root: Path, revisions: Revisions, ruleset: RuleSet, settings: Settings | None = None,
               llm: LLM | None = None, db: DB | None = None, no_checks: bool = False, on_progress=None
               ) -> ReviewResult:
    started = time.monotonic()
    settings = settings or Settings.load(root)
    db = db or DB(db_file(root, settings))
    session_id = review_session_id(revisions)
    llm = llm or LLM(settings, db, session_id)
    ctx = build_context(root, revisions, ruleset.rules, settings, db, no_checks=no_checks)

    def step(label: str) -> None:
        if on_progress:
            on_progress(label)

    if not no_checks:
        step("Running allowlisted project checks")
        for kind in detect_checks(root):
            ctx.check(kind)

    changed_paths = [c.path for c in ctx.changes]
    findings, insufficient, outcomes, errors = [], [], [], []
    for rule in ruleset.rules:
        if not matches(rule, changed_paths):
            outcomes.append(RuleOutcome(id=rule.id, text=rule.text, severity=rule.severity, source=rule.source,
                                        verdict="SKIPPED"))
            continue
        step(f"Reviewing {rule.id}")
        req = ctx.requirement(rule.id)
        verdict = verify_requirement(llm, ctx, req, instructions=REVIEW_PROMPT, settle=False)
        if verdict.status == "VIOLATED" and not corroborated(ctx, verdict) and llm.available:
            step(f"Confirming {rule.id}")
            verdict = _confirm(llm, ctx, rule, verdict)
        db.put("verdicts", session_id, rule.id, verdict, seq=len(outcomes) + 1)
        outcomes.append(RuleOutcome(id=rule.id, text=rule.text, severity=rule.severity, source=rule.source,
                                    verdict=verdict.status))
        findings += findings_for(ctx, rule, verdict, revisions.head.tree, ruleset.min_severity)
        if verdict.status == "UNCERTAIN":
            insufficient.append(Insufficient(rule_id=rule.id, summary=verdict.summary,
                                             missing_evidence=verdict.missing_evidence))
        if verdict.summary.startswith("Verification error:"):
            errors.append(f"{rule.id}: {verdict.summary}")

    cost = None
    if settings.price_input_per_mtok is not None and settings.price_output_per_mtok is not None:
        cost = round(llm.input_tokens / 1e6 * settings.price_input_per_mtok
                     + llm.output_tokens / 1e6 * settings.price_output_per_mtok, 6)
    usage = ReviewUsage(wall_time_s=round(time.monotonic() - started, 3), model_calls=llm.calls,
                        input_tokens=llm.input_tokens, output_tokens=llm.output_tokens,
                        cache_read_tokens=llm.cache_read_tokens, cost_usd=cost)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (not f.shown, order[f.severity], f.path, f.line))
    return ReviewResult(
        base=revisions.base, head=revisions.head, merge_base=revisions.merge_base,
        model=llm.model if llm.available else None, rules_source=ruleset.sources, findings=findings,
        insufficient=insufficient, rules=outcomes, usage=usage, errors=errors,
        checks=[CheckSummary(kind=c.kind, command=" ".join(c.command), exit_code=c.exit_code,
                             duration_s=c.duration_s) for c in ctx.checks.values()],
    )

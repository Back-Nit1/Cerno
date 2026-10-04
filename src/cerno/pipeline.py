"""End-to-end analysis: intent -> changes -> checks -> rules -> retrieval -> agent -> drift -> claims -> usage."""

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from cerno.analysis import git
from cerno.analysis.graph import RepoIndex
from cerno.analysis.retrieval import RISKY, build_packet, keywords, overlap, rank, split_identifier
from cerno.analysis.segments import attribute_changes, build_segments
from cerno.analysis.symbols import changed_symbols
from cerno.capture.snapshots import load_snapshot, now
from cerno.capture.timeline import load_prompt_timeline
from cerno.capture.transcript import extract_claims, final_assistant_text
from cerno.config import Settings, db_file
from cerno.evidence.store import EvidenceStore
from cerno.intent.compiler import compile_intent_timeline
from cerno.llm.openai import LLM, ProviderError
from cerno.models import AgentClaim, ChangeAssessment, HookEvent, Session, UsageStats, Verdict
from cerno.storage.sqlite import DB
from cerno.verification.agent import verify_with_agent
from cerno.verification.context import Context
from cerno.verification.deterministic import _deps, apply_rules
from cerno.verification.runtime import detect_checks
from cerno.verification.verdict import finalize, uncertain

RESULT_TABLES = ["requirements", "file_changes", "symbol_changes", "evidence", "project_checks", "verdicts",
                 "agent_claims", "change_assessments", "model_usage", "prompt_timeline", "diff_segments",
                 "attributions"]
GUARD_TYPES = {"invariant", "prohibition"}


def latest_session_id(db: DB) -> str | None:
    sessions = sorted((s for s in db.list("sessions") if "prompts" in s), key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions[0]["id"] if sessions else None


def analyze(root: Path, session_id: str, settings: Settings | None = None, llm: LLM | None = None,
            on_progress: Callable[[str], None] | None = None) -> Session:
    settings = settings or Settings.load(root)
    db = DB(db_file(root, settings))
    stored = db.get("sessions", session_id, session_id)
    if stored is None:
        raise KeyError(f"unknown session {session_id}")
    session = Session.model_validate(stored)

    def step(label: str) -> None:
        for item in session.progress:
            item["state"] = "done"
        session.progress.append({"label": label, "state": "active"})
        session.updated_at = now()
        db.put("sessions", session_id, session_id, session)
        if on_progress:
            on_progress(label)

    session.status, session.progress, session.error = "analyzing", [], None
    try:
        raw_prompts = load_prompt_timeline(session_id, session, db)  # read before RESULT_TABLES is cleared
        for table in RESULT_TABLES:
            db.clear(table, session_id)
        llm = llm or LLM(settings, db, session_id)

        step("Compiling intent contract")
        contract, prompts, source = compile_intent_timeline(llm, raw_prompts, db)
        db.put_many("prompt_timeline", session_id, [(p.id, p) for p in prompts])
        db.put_many("requirements", session_id, [(r.id, r) for r in contract.requirements])
        db.put("sessions", session_id, "__contract__", contract)  # full contract incl. protected areas

        step("Locating changed files and symbols")
        ctx = build_context(root, session_id, settings, db, contract)
        db.put_many("file_changes", session_id, [(c.path, c) for c in ctx.changes])
        db.put_many("symbol_changes", session_id, [(f"{s.file_path}::{s.symbol_name}", s) for s in ctx.symbol_changes])

        step("Attributing changes to prompt segments")
        segments = build_segments(root, session_id, prompts, db)
        db.put_many("diff_segments", session_id, [(s.id, s) for s in segments])
        attributions = attribute_changes(segments, ctx.events)
        db.put_many("attributions", session_id, [(a.path, a) for a in attributions])
        ctx.segments, ctx.attributions = segments, {a.path: a for a in attributions}

        step("Running allowlisted project checks")
        for kind in detect_checks(root):
            ctx.check(kind)

        verdicts: list[Verdict] = []
        for req in contract.requirements:
            if not req.active:  # superseded requirements stay visible but never receive a verdict
                continue
            step(f"Verifying {req.id}: {req.text[:70]}")
            verdicts.append(verify_requirement(llm, ctx, req))
            db.put("verdicts", session_id, req.id, verdicts[-1], seq=len(verdicts))

        step("Assessing unexpected changes")
        db.put_many("change_assessments", session_id, [(a.path, a) for a in assess_changes(ctx, verdicts)])

        step("Checking Claude's final claims")
        db.put_many("agent_claims", session_id, [(c.id, c) for c in check_claims(ctx, session.transcript_path, verdicts)])

        usage = compute_usage(ctx, llm, verdicts)
        db.put("model_usage", session_id, "__summary__", usage, seq=10**6)
        session.task_summary, session.intent_source = contract.task_summary, source
        session.counts = dict(Counter(v.status for v in verdicts))
        session.status = "analyzed"
    except Exception as exc:
        session.status, session.error = "failed", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for item in session.progress:
            item["state"] = "done" if session.status == "analyzed" else item["state"]
        session.updated_at = now()
        db.put("sessions", session_id, session_id, session)
    return session


def build_context(root: Path, session_id: str, settings: Settings, db: DB, contract) -> Context:
    """Baseline vs final attribution. Missing snapshots fall back to HEAD / current tree."""
    baseline, final = load_snapshot(root, session_id, "baseline"), load_snapshot(root, session_id, "final")
    base_tree = baseline.tree if baseline and baseline.tree else git.head_tree(root)
    final_tree = final.tree if final and final.tree else git.snapshot_tree(root)
    changes = git.get_changed_files(root, base_tree, final_tree)
    base_index = RepoIndex.from_tree(root, base_tree, db)
    final_index = RepoIndex.from_tree(root, final_tree, db)
    symbols = changed_symbols(changes, base_index, final_index)
    events = [HookEvent.model_validate(e) for e in db.list("events", session_id)]
    return Context(root, session_id, settings, db, EvidenceStore(db, session_id), base_tree, final_tree, changes,
                   symbols, base_index, final_index, events, contract)


def verify_requirement(llm: LLM, ctx: Context, req, instructions: str | None = None,
                       settle: bool = True) -> Verdict:
    """settle=False keeps the deterministic rules as *evidence* but lets the agent decide: the session-shaped
    heuristics in deterministic.py are not safe arbiters of a review rule."""
    try:
        rules = apply_rules(req, ctx)
        if settle and rules.verdict is not None:
            return finalize(rules.verdict.model_copy(update={"plan": " -> ".join(rules.plan)}), ctx.store)
        candidates = rank(req, ctx)
        packet = build_packet(req, ctx, candidates, rules.evidence_ids)
        plan = rules.plan + ["retrieval: rank changed symbols and bounded impact neighborhood",
                             "agent: bounded tool-use investigation"]
        kwargs = {"instructions": instructions} if instructions else {}
        return verify_with_agent(llm, ctx, req, packet, rules.evidence_ids, plan, **kwargs)
    except ProviderError:
        raise  # a bad key or an unreachable provider is an operator error, not "insufficient evidence"
    except Exception as exc:  # one requirement failing must not sink the session
        return uncertain(req.id, f"Verification error: {type(exc).__name__}: {exc}", ["a successful investigation run"])


def assess_changes(ctx: Context, verdicts: list[Verdict]) -> list[ChangeAssessment]:
    reqs = [r for r in ctx.contract.requirements if r.active]  # superseded requirements carry no live verdict
    guards = [r for r in reqs if r.type in GUARD_TYPES]
    goals = [r for r in reqs if r.type not in GUARD_TYPES]
    violated = [v for v in verdicts if v.status == "VIOLATED"]

    def tokens(path: str) -> set[str]:
        names = {s.symbol_name for s in ctx.symbol_changes if s.file_path == path}
        return split_identifier(path).union(*(split_identifier(n) for n in names))

    def neighbors(path: str) -> set[str]:
        return ctx.final_index.imports.get(path, set()) | ctx.final_index.importers.get(path, set())

    expected = {c.path for c in ctx.changes
                if git.is_test(c.path) or any(overlap(keywords(r.text), tokens(c.path)) for r in goals)}
    out = []
    for c in ctx.changes:
        syms = [s for s in ctx.symbol_changes if s.file_path == c.path]
        contradicted = [
            v.requirement_id for v in violated
            if c.path in v.files_inspected or any((e := ctx.store.get(i)) and e.path == c.path for i in v.evidence_ids)
        ]
        protected = any(c.path.startswith(a.rstrip("/")) for a in ctx.contract.protected_areas if "/" in a or "." in a)
        near = neighbors(c.path)
        if contradicted or protected:
            cls = "CONTRADICTORY"
        elif c.path in expected:
            cls = "EXPECTED"
        elif git.is_manifest(c.path) or near & expected:
            cls = "EXPLAINABLE"
        else:
            cls = "UNEXPLAINED"
        near_tokens = tokens(c.path).union(*(split_identifier(n) for n in near))
        affected = contradicted or [r.id for r in guards if overlap(keywords(r.text), near_tokens)]

        pre_existing = [s for s in syms if s.change_type != "added"]
        callers = sorted({caller for s in syms for caller in s.callers})
        risky = bool(RISKY.search(c.path)) or git.is_schema(c.path) or git.is_manifest(c.path)
        impact = ("HIGH" if (pre_existing and (callers or risky)) or git.is_schema(c.path)
                  else "MEDIUM" if pre_existing or ctx.final_index.importers.get(c.path) else "LOW")
        sym_text = ", ".join(f"{s.change_type} {s.symbol_name}" for s in syms[:5])
        why = []
        if callers:
            why.append(f"{len(callers)} caller(s) of changed symbols: " + ", ".join(x.split("::")[-1] for x in callers[:5]))
        if importers := sorted(ctx.final_index.importers.get(c.path, set())):
            why.append("imported by " + ", ".join(importers[:5]))
        if risky:
            why.append("sensitive area (auth/session/config/schema/dependencies)")
        touched = [e for e in ctx.events if e.path == c.path and e.event_type == "post_tool_use"]
        explanation = (f"Claude edited this file via {', '.join(sorted({e.tool_name or '?' for e in touched}))} "
                       f"({len(touched)} event(s)); no stated rationale is captured in hook events"
                       if touched else "No Claude Edit/Write event touched this file directly (changed via Bash or "
                       "outside captured tool calls)")
        out.append(ChangeAssessment(
            path=c.path, classification=cls, impact=impact,
            what_changed=f"{c.status} (+{c.additions}/-{c.deletions})" + (f"; {sym_text}" if sym_text else ""),
            why_impact="; ".join(why) or "no known callers or importers", affected_requirements=affected,
            agent_explanation=explanation,
        ))
    order = {"CONTRADICTORY": 0, "UNEXPLAINED": 1, "EXPLAINABLE": 2, "EXPECTED": 3}
    return sorted(out, key=lambda a: (order[a.classification], {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[a.impact], a.path))


def check_claims(ctx: Context, transcript_path: str | None, verdicts: list[Verdict]) -> list[AgentClaim]:
    text = final_assistant_text(transcript_path)
    if not text:
        return []
    guards = {r.id for r in ctx.contract.requirements if r.type in GUARD_TYPES and r.active}
    claims = []
    for n, (category, sentence) in enumerate(extract_claims(text), start=1):
        result, ids, summary = "UNVERIFIABLE", [], "No evidence in this analysis addresses the claim."
        if category in {"tests_pass", "build_pass"}:
            check = ctx.checks.get("test" if category == "tests_pass" else "build")
            if check is not None and check.exit_code is not None:
                result = "CONFIRMED" if check.exit_code == 0 else "CONTRADICTED"
                ids, summary = [check.evidence_id], f"{' '.join(check.command)} -> exit code {check.exit_code}"
        elif category == "no_schema_change":
            hits = [c.path for c in ctx.changes if git.is_schema(c.path)]
            ev = ctx.store.add("rule", "rule:schema_changes", f"Schema/migration files changed: {', '.join(hits) or 'none'}")
            result, ids, summary = ("CONTRADICTED" if hits else "CONFIRMED"), [ev.id], ev.summary
        elif category == "no_dependency_change":
            added = set()
            for c in ctx.changes:
                if git.is_manifest(c.path):
                    added |= _deps(ctx.root, ctx.final_tree, c.path) - _deps(ctx.root, ctx.base_tree, c.old_path or c.path)
            ev = ctx.store.add("rule", "rule:dependency_manifests", f"New dependencies: {', '.join(sorted(added)) or 'none'}")
            result, ids, summary = ("CONTRADICTED" if added else "CONFIRMED"), [ev.id], ev.summary
        elif category == "behavior_preserved":
            relevant = [v for v in verdicts if v.requirement_id in guards]
            bad = [v for v in relevant if v.status == "VIOLATED"]
            if bad:
                result, summary = "CONTRADICTED", "; ".join(f"{v.requirement_id} VIOLATED: {v.summary}" for v in bad)
                ids = [i for v in bad for i in v.evidence_ids]
            elif relevant and all(v.status == "VERIFIED" for v in relevant):
                result, summary = "CONFIRMED", "All invariant/prohibition requirements VERIFIED."
                ids = [i for v in relevant for i in v.evidence_ids]
        claims.append(AgentClaim(id=f"C{n}", text=sentence, category=category, result=result, evidence_ids=ids,
                                 summary=summary[:500]))
    return claims


def compute_usage(ctx: Context, llm: LLM, verdicts: list[Verdict]) -> UsageStats:
    repo_files = len(git.list_tree_files(ctx.root, ctx.final_tree))
    source_lines = sum(s.count("\n") + 1 for s in ctx.final_index.sources.values()) or None
    avoided = round(100 * (1 - ctx.lines_sent / source_lines), 1) if source_lines and ctx.lines_sent else None
    return UsageStats(
        model_input_tokens=llm.input_tokens, model_output_tokens=llm.output_tokens,
        cache_read_tokens=llm.cache_read_tokens, model_calls=llm.calls, tool_calls=ctx.tool_calls,
        repo_files=repo_files, repo_lines=source_lines, files_inspected=len(ctx.inspected_files),
        symbols_inspected=len(ctx.inspected_symbols), requirements_checked=len(verdicts),
        cache_hits=sum(v.cached for v in verdicts), lines_sent_to_model=ctx.lines_sent,
        context_avoided_pct=max(avoided, 0.0) if avoided is not None else None,
    )

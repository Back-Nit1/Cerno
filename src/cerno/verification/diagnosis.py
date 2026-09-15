"""Production-breakage diagnosis: narrow a commit range, rank suspects deterministically, then
optionally ask OpenAI to add a narrative over a bounded, redacted evidence packet.

Deterministic git facts are always authoritative: the model is never allowed to introduce a new
suspect or change a score, only to add a short note and (for a correlation the model itself flagged
as especially likely) relabel it "model_inference". Every suspect the model returns is validated
against the candidate set it was given; anything else is dropped. Cerno never runs `git bisect`,
checks out a commit, or modifies the working tree here -- `bisect_plan` is text for the developer.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from cerno.analysis import git, history
from cerno.analysis.retrieval import keywords, overlap, split_identifier
from cerno.config import Settings
from cerno.llm.openai import LLM, call_arguments, function_calls
from cerno.models import CommitRecord, DiagnosisResult, SuspectFinding
from cerno.privacy import redact
from cerno.storage.sqlite import DB

MAX_CANDIDATES = 40
MAX_PACKET_COMMITS = 12

SYSTEM = """You are helping diagnose a production regression from a bounded set of candidate commits
that a deterministic Git analysis already selected and scored. You cannot see the rest of the
repository or its history. Never invent a commit, file, session, or requirement that is not present
in the packet -- every "ref" you return must be copied verbatim from the packet. Your job is to
write a short narrative connecting the evidence, and to flag which correlations look, in your
judgment, most likely to be causal (never a new score; the ranking you were given is authoritative).
Call submit_diagnosis exactly once."""

DIAGNOSIS_TOOL = {
    "type": "function",
    "name": "submit_diagnosis",
    "description": "Submit a narrative diagnosis over the given candidate commits.",
    "strict": True,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["narrative", "likely_causal_refs"],
        "properties": {
            "narrative": {"type": "string"},
            "likely_causal_refs": {"type": "array", "items": {"type": "string"},
                                   "description": "subset of the packet's commit hashes"},
        },
    },
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def candidate_commits(root: Path, db: DB, repo_id: str, good: str | None, bad: str | None,
                      since: str | None, until: str | None) -> list[CommitRecord]:
    if good and bad:
        hashes = [h for h in git.git(root, "rev-list", f"{good}..{bad}", check=False).split() if h]
        commits = [c for h in hashes if (c := history.get_commit(db, repo_id, h))]
        return sorted(commits, key=lambda c: c.seq, reverse=True)
    rows = sorted((CommitRecord.model_validate(r) for r in db.list("commits", repo_id)),
                 key=lambda c: c.seq, reverse=True)
    if since or until:
        rows = [c for c in rows if (not since or c.author_date >= since) and (not until or c.author_date <= until)]
    return rows[:MAX_CANDIDATES]


def _session_index(db: DB, repo_id: str) -> dict[str, str]:
    """commit hash -> session_id, from every recorded session-commit link."""
    out = {}
    for row in db.list("session_commit_links", repo_id):
        for h in row.get("commits_created", []):
            out[h] = row["session_id"]
    return out


def _violated_requirement(db: DB, session_id: str, path: str) -> str | None:
    for v in db.list("verdicts", session_id):
        if v["status"] == "VIOLATED" and path in (v.get("files_inspected") or []):
            return v["requirement_id"]
    return None


def score_commit(commit: CommitRecord, query_kw: set[str], suspected_path: str | None,
                 suspected_symbol: str | None, suspected_dependency: str | None) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    paths = {f.path for f in commit.files} | {f.old_path for f in commit.files if f.old_path}
    if suspected_path and suspected_path in paths:
        score, reasons = score + 4.0, reasons + [f"touches suspected file {suspected_path}"]
    if suspected_symbol and any(suspected_symbol in split_identifier(p) for p in paths):
        score, reasons = score + 3.0, reasons + [f"path relates to suspected symbol {suspected_symbol}"]
    if suspected_dependency and commit.manifest_changed:
        score, reasons = score + 2.5, reasons + [f"dependency manifest changed (suspected: {suspected_dependency})"]
    if commit.schema_changed:
        score, reasons = score + 2.0, reasons + ["schema/migration change"]
    if commit.manifest_changed and not suspected_dependency:
        score, reasons = score + 1.0, reasons + ["dependency manifest change"]
    if commit.test_changed and not any(git.is_test(p) is False for p in paths):
        score, reasons = score + 0.3, reasons + ["test-only change"]
    if query_kw:
        subject_hits = overlap(query_kw, keywords(commit.subject))
        path_hits = overlap(query_kw, {t for p in paths for t in split_identifier(p)})
        if subject_hits:
            score, reasons = score + 2.0 * len(subject_hits), reasons + [f"message matches: {', '.join(sorted(subject_hits))}"]
        if path_hits:
            score, reasons = score + 1.5 * len(path_hits), reasons + [f"path matches: {', '.join(sorted(path_hits))}"]
    return score, reasons


def suggest_bisect_plan(candidates: list[CommitRecord]) -> list[str]:
    """Text-only manual bisect suggestion. Cerno never runs `git bisect` or checks anything out."""
    if len(candidates) < 3:
        return []
    ordered = sorted(candidates, key=lambda c: c.seq)  # oldest -> newest
    mid = ordered[len(ordered) // 2]
    return [
        "git bisect start",
        f"git bisect bad {ordered[-1].hash}",
        f"git bisect good {ordered[0].hash}",
        f"Test around the midpoint {mid.hash[:10]} ({mid.subject[:60]!r}) first, then `git bisect good`/`git bisect bad` "
        "based on the result and repeat until bisect narrows to one commit.",
        "Run these yourself -- Cerno does not execute git bisect or check out commits.",
    ]


def _packet(candidates: list[SuspectFinding], commits: dict[str, CommitRecord]) -> str:
    lines = ["CANDIDATE COMMITS (already scored; do not rescore, only narrate and flag likely causes)"]
    for s in candidates[:MAX_PACKET_COMMITS]:
        c = commits[s.ref]
        lines.append(
            f"- {c.hash[:10]} score={s.score:.1f} subject={redact(c.subject)!r} "
            f"files={len(c.files)} schema={c.schema_changed} manifest={c.manifest_changed} "
            f"session={s.session_id or '-'} requirement={s.requirement_id or '-'} reason={'; '.join(redact(s.reason).split('; '))}"
        )
    return "\n".join(lines)


def diagnose(root: Path, db: DB, settings: Settings, *, good: str | None = None, bad: str | None = None,
            since: str | None = None, until: str | None = None, query: str | None = None,
            suspected_path: str | None = None, suspected_symbol: str | None = None,
            suspected_dependency: str | None = None, incident_description: str | None = None,
            llm: LLM | None = None) -> DiagnosisResult:
    repo_id = history.repo_id_for(root)
    if good is None and (markers := history.list_markers(db, repo_id, "good")):
        good = markers[-1].commit
    if bad is None and (markers := history.list_markers(db, repo_id, "bad")):
        bad = markers[-1].commit
    candidates = candidate_commits(root, db, repo_id, good, bad, since, until)
    query_kw = keywords(" ".join(filter(None, [query, incident_description])))
    session_of = _session_index(db, repo_id)

    scored: list[SuspectFinding] = []
    by_hash: dict[str, CommitRecord] = {}
    for c in candidates:
        score, reasons = score_commit(c, query_kw, suspected_path, suspected_symbol, suspected_dependency)
        if score <= 0:
            continue
        sid = session_of.get(c.hash)
        req_id = _violated_requirement(db, sid, suspected_path) if sid and suspected_path else None
        by_hash[c.hash] = c
        scored.append(SuspectFinding(
            kind="commit", ref=c.hash, score=round(score, 2), reason="; ".join(reasons) or "matched query",
            evidence="fact" if suspected_path in {f.path for f in c.files} else "correlation",
            session_id=sid, requirement_id=req_id,
        ))
    scored.sort(key=lambda s: -s.score)

    narrative = (
        f"{len(scored)} of {len(candidates)} candidate commit(s) between "
        f"{good or '(repo start)'} and {bad or 'HEAD'} matched the given evidence. "
        + (f"Top suspect: {scored[0].ref[:10]} ({scored[0].reason})." if scored else "No commit matched the given evidence.")
    )
    model_used = False
    if scored and llm is None and settings.api_key:
        llm = LLM(settings, db, session_id="diagnosis")
    if scored and llm is not None and llm.available:
        try:
            response = llm.create(
                "diagnosis", instructions=SYSTEM, max_output_tokens=1024, tools=[DIAGNOSIS_TOOL],
                tool_choice={"type": "function", "name": "submit_diagnosis"},
                input=[{"role": "user", "content": _packet(scored, by_hash)}],
            )
            out = call_arguments(next(iter(function_calls(response))))
            valid_refs = {s.ref for s in scored}
            causal = {r for r in out.get("likely_causal_refs", []) if r in valid_refs}
            for s in scored:
                if s.ref in causal and s.evidence == "correlation":
                    s.evidence = "model_inference"
            narrative = redact(str(out.get("narrative", narrative)))[:2000]
            model_used = True
        except (StopIteration, ValueError, TypeError, KeyError):
            pass  # malformed model output: keep the deterministic narrative

    result = DiagnosisResult(
        id=f"D-{uuid.uuid4().hex[:10]}", repo_id=repo_id, good=good, bad=bad, query=query, created_at=now(),
        candidate_commits=[c.hash for c in candidates], suspects=scored, narrative=narrative,
        model_used=model_used, bisect_plan=suggest_bisect_plan(candidates),
    )
    db.put("diagnoses", repo_id, result.id, result, seq=db.count("diagnoses", repo_id))
    return result


def get_diagnosis(db: DB, repo_id: str, diagnosis_id: str) -> DiagnosisResult | None:
    row = db.get("diagnoses", repo_id, diagnosis_id)
    return DiagnosisResult.model_validate(row) if row else None

"""Requirement-specific evidence retrieval: rank candidates, then build a compact evidence packet."""

import re
from dataclasses import dataclass, field

from cerno.analysis import git
from cerno.models import Requirement
from cerno.privacy import is_excluded
from cerno.verification.context import Context

STOPWORDS = set(
    "the a an and or of to in on for with without from by be is are was were must should not do does don't dont "
    "never any all existing new add use using keep make sure that this it its as at after before into via can will "
    "still only also when then than more less support supports".split()
)
RISKY = re.compile(r"(auth|session|login|password|token|security|payment|billing|config|schema|migration|permission)", re.I)
# Transparent, configurable weights. Scores rank candidates; they are not probabilities.
WEIGHTS = {"changed": 3.0, "keyword": 1.5, "graph": 2.0, "test": 1.5, "event": 0.5, "risk": 1.0}
MAX_CANDIDATES = 12
MAX_PACKET_DIFF_LINES = 40


def split_identifier(name: str) -> set[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return {p.lower() for p in re.split(r"[^A-Za-z0-9]+", parts) if len(p) >= 3}


def keywords(text: str) -> set[str]:
    return {w for w in split_identifier(text) if w not in STOPWORDS}


def overlap(a: set[str], b: set[str]) -> set[str]:
    """Match on shared 5-char prefix, a cheap stemmer: expire/expires/expiry, session/sessions."""
    return {x for x in a if any(x == y or (len(x) >= 5 and len(y) >= 5 and x[:5] == y[:5]) for y in b)}


@dataclass
class Candidate:
    symbol_id: str | None
    path: str
    score: float = 0.0
    distance: int = 0
    reasons: list[str] = field(default_factory=list)


def rank(req: Requirement, ctx: Context) -> list[Candidate]:
    kw = keywords(f"{req.text} {req.verification_hint or ''}")
    event_paths = {e.path for e in ctx.events if e.path}
    found: dict[str, Candidate] = {}

    def bump(key: str, cand: Candidate, amount: float, reason: str) -> None:
        cur = found.setdefault(key, cand)
        cur.score += amount
        cur.reasons.append(reason)

    changed_symbol_files = set()
    for sc in ctx.symbol_changes:
        if is_excluded(ctx.root, sc.file_path):
            continue
        sid = f"{sc.file_path}::{sc.symbol_name}"
        changed_symbol_files.add(sc.file_path)
        tokens = split_identifier(sc.symbol_name) | split_identifier(sc.file_path)
        bump(sid, Candidate(sid, sc.file_path), WEIGHTS["changed"], f"{sc.change_type} in diff")
        if hits := overlap(kw, tokens):
            bump(sid, found[sid], WEIGHTS["keyword"] * len(hits), f"keywords: {', '.join(sorted(hits))}")
        if RISKY.search(sc.file_path) or RISKY.search(sc.symbol_name):
            bump(sid, found[sid], WEIGHTS["risk"], "risky area")
        if sc.file_path in event_paths:
            bump(sid, found[sid], WEIGHTS["event"], "edited by Claude tool call")
        if git.is_test(sc.file_path):
            bump(sid, found[sid], WEIGHTS["test"], "test code")
        if sc.change_type != "deleted":
            for neighbor, dist in ctx.final_index.expand(sid, ctx.settings.max_graph_depth).items():
                if dist == 0 or is_excluded(ctx.root, neighbor.split("::", 1)[0]):
                    continue
                npath = neighbor.split("::", 1)[0]
                amount = WEIGHTS["graph"] / (1 + dist) + (WEIGHTS["test"] if git.is_test(npath) else 0)
                if hits := overlap(kw, split_identifier(neighbor)):
                    amount += WEIGHTS["keyword"] * len(hits)
                cand = found.get(neighbor) or Candidate(neighbor, npath, distance=dist)
                bump(neighbor, cand, amount, f"calls {sc.symbol_name} (distance {dist})")

    for change in ctx.changes:  # changed files without parsed symbols (configs, manifests, SQL, docs)
        if change.path in changed_symbol_files or is_excluded(ctx.root, change.path):
            continue
        bump(change.path, Candidate(None, change.path), WEIGHTS["changed"] * 0.7, f"{change.status} file")
        if hits := overlap(kw, split_identifier(change.path)):
            bump(change.path, found[change.path], WEIGHTS["keyword"] * len(hits), f"keywords: {', '.join(sorted(hits))}")
    return sorted(found.values(), key=lambda c: (-c.score, c.path, c.symbol_id or ""))[:MAX_CANDIDATES]


def _hunks_for(ctx: Context, path: str, start: int, end: int) -> str:
    lines = []
    for hunk in git.get_diff_hunks(ctx.diff(path)):
        if hunk["new_start"] <= end and hunk["new_start"] + hunk["new_len"] >= start or not hunk["added"]:
            lines += [f"- {line}" for line in hunk["removed"]] + [f"+ {line}" for line in hunk["added"]]
    return "\n".join(lines[:MAX_PACKET_DIFF_LINES])


def build_packet(req: Requirement, ctx: Context, candidates: list[Candidate], seed_ids: list[str]) -> str:
    out = [f"REQUIREMENT {req.id} ({req.type}, {req.priority})", req.text]
    if req.verification_hint:
        out.append(f"Hint: {req.verification_hint}")
    if seed_ids:
        out.append("\nDETERMINISTIC EVIDENCE")
        out += [f"[{i}] {ctx.store.get(i).summary}" for i in seed_ids if ctx.store.get(i)]

    out.append("\nRELEVANT CHANGES (ranked)")
    shown_diffs = 0
    for cand in candidates:
        label = cand.symbol_id or cand.path
        sc = next((s for s in ctx.symbol_changes if cand.symbol_id == f"{s.file_path}::{s.symbol_name}"), None)
        if sc is None and cand.symbol_id is not None:
            out.append(f"- {label} [score {cand.score:.1f}: {'; '.join(dict.fromkeys(cand.reasons))}]")
            continue
        start, end = (sc.start_line, sc.end_line) if sc else (1, 10**9)
        out.append(f"- {label} ({sc.change_type if sc else 'file'}) [score {cand.score:.1f}: "
                   f"{'; '.join(dict.fromkeys(cand.reasons))}]")
        if shown_diffs < 4 and (diff := _hunks_for(ctx, cand.path, start, end)):
            ev = ctx.store.add("diff", f"git diff {cand.path}", f"diff hunk(s) for {label}", path=cand.path,
                               line_start=start if sc else None, line_end=end if sc else None, raw=diff)
            out.append(f"  DIFF [{ev.id}]\n" + "\n".join(f"  {line}" for line in diff.splitlines()))
            ctx.touch(cand.path, cand.symbol_id)
            shown_diffs += 1

    paths = []
    for sc in ctx.symbol_changes:
        sid = f"{sc.file_path}::{sc.symbol_name}"
        if any(c.symbol_id == sid for c in candidates[:6]) and sc.change_type == "modified":
            for caller in sorted(ctx.final_index.callers.get(sid, ()))[:3]:
                paths.append(f"{caller.split('::')[-1]} -> {sc.symbol_name}   ({caller.split('::')[0]})")
    if paths:
        ev = ctx.store.add("graph", "impact graph (name-based, approximate)", "caller paths into changed symbols",
                           raw="\n".join(paths))
        out.append(f"\nDEPENDENCY PATHS [{ev.id}] (approximate)")
        out += paths[:8]

    if ctx.checks:
        out.append("\nRUNTIME EVIDENCE")
        out += [f"[{c.evidence_id}] {' '.join(c.command)}: exit {c.exit_code}" for c in ctx.checks.values()]

    relevant = {c.path for c in candidates}
    events = [e for e in ctx.events if e.path in relevant and e.event_type == "post_tool_use"][:5]
    if events:
        ev = ctx.store.add("events", "claude code hooks", f"{len(events)} Claude tool events on relevant files",
                           raw="\n".join(f"{e.timestamp} {e.tool_name} {e.path}" for e in events))
        out.append(f"\nAGENT EVENTS [{ev.id}]")
        out += [f"Claude used {e.tool_name} on {e.path}" for e in events]

    out.append(f"\nALL CHANGED FILES ({len(ctx.changes)}): " + ", ".join(c.path for c in ctx.changes[:40]))
    out.append(f"\nOPEN QUESTION\nDo the changes satisfy, violate, or leave unproven requirement {req.id}?")
    return "\n".join(out)

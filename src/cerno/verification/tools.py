"""Read-only investigation tools for the verification agent (plus allowlisted project checks).

No tool accepts a shell command, and no tool writes to the repository.
"""

import json
import re

from cerno.analysis import git
from cerno.privacy import is_excluded
from cerno.verification.context import Context

CHECK_KINDS = ["test", "build", "lint", "typecheck"]


def _tool(name: str, description: str, **props: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {"type": "object", "additionalProperties": False, "required": list(props), "properties": props},
    }


STR, INT = {"type": "string"}, {"type": "integer"}
TOOLS = [
    _tool("get_requirement", "Get one requirement from the Intent Contract.", requirement_id=STR),
    _tool("get_changed_files", "List files changed between the session baseline and final snapshot."),
    _tool("get_file_diff", "Unified diff (baseline -> final) for one changed file.", path=STR, max_lines=INT),
    _tool("get_changed_symbols", "Changed functions/classes/variables in a file ('' for all files).", path=STR),
    _tool("read_symbol", "Source of one symbol. symbol_id is 'path::QualifiedName'. version: final or baseline.",
          symbol_id=STR, version={"type": "string", "enum": ["final", "baseline"]}),
    _tool("search_code", "Case-insensitive literal search over source files in the final snapshot.", query=STR, limit=INT),
    _tool("find_callers", "Symbols that (approximately, by name) call symbol_id, up to depth.", symbol_id=STR, depth=INT),
    _tool("find_references", "Lines referencing a symbol's name.", symbol_id=STR, limit=INT),
    _tool("find_related_tests", "Test files and test names mentioning a symbol id or query.", query=STR, limit=INT),
    _tool("get_agent_events", "Claude Code tool events (Edit/Write/Bash) matching query ('' for all).", query=STR,
          limit=INT),
    _tool("get_test_results", "Results of project checks already run in this analysis."),
    _tool("run_project_check", "Run an allowlisted project check derived from project configuration.",
          kind={"type": "string", "enum": CHECK_KINDS}),
    _tool("get_evidence", "Full stored content of one evidence item.", evidence_id=STR),
    _tool(
        "submit_verdict",
        "Submit the final verdict for the requirement. Cite evidence ids returned by tools or the packet.",
        status={"type": "string", "enum": ["VERIFIED", "LIKELY", "UNCERTAIN", "VIOLATED"]},
        evidence_level={"type": "string", "enum": ["runtime", "test", "structural", "semantic", "insufficient"]},
        summary=STR,
        evidence_ids={"type": "array", "items": STR},
        missing_evidence={"type": "array", "items": STR},
    ),
]


def _dump(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def _clip(lines: list[str], limit: int) -> list[str]:
    return lines[:limit] + ([f"... ({len(lines) - limit} more)"] if len(lines) > limit else [])


def _limit(ctx: Context, value) -> int:
    return max(1, min(int(value or ctx.settings.max_search_results), ctx.settings.max_search_results))


def _sources(ctx: Context):
    changed = {c.path for c in ctx.changes}
    items = sorted(ctx.final_index.sources.items(), key=lambda kv: (kv[0] not in changed, kv[0]))
    return [(p, s) for p, s in items if not is_excluded(ctx.root, p)]


def _result(ctx: Context, type: str, source: str, summary: str, payload, path: str | None = None, **kw):
    text = payload if isinstance(payload, str) else _dump(payload)
    ev = ctx.store.add(type, source, summary, path=path, raw=text, **kw)
    return f"[evidence {ev.id}] {summary}\n{text}", [ev.id]


def get_requirement(ctx: Context, requirement_id: str):
    req = ctx.requirement(requirement_id)
    return (_dump(req.model_dump()) if req else f"unknown requirement {requirement_id}"), []


def get_changed_files(ctx: Context):
    rows = [{"path": c.path, "status": c.status, "add": c.additions, "del": c.deletions} for c in ctx.changes]
    return _result(ctx, "changes", "git diff-tree baseline final", f"{len(rows)} changed files", rows)


def get_file_diff(ctx: Context, path: str, max_lines: int):
    if is_excluded(ctx.root, path):
        return f"{path} is excluded by privacy rules", []
    diff = ctx.diff(path)
    if not diff:
        return f"no diff for {path}", []
    ctx.touch(path)
    limit = max(10, min(int(max_lines or 80), ctx.settings.max_snippet_lines))
    text = "\n".join(_clip(diff.splitlines(), limit))
    return _result(ctx, "diff", f"git diff {path}", f"diff of {path}", text, path=path)


def get_changed_symbols(ctx: Context, path: str):
    rows = [s.model_dump() for s in ctx.symbol_changes if not path or s.file_path == path]
    return _result(ctx, "symbol", "tree-sitter baseline/final comparison",
                   f"{len(rows)} changed symbols{' in ' + path if path else ''}", rows, path=path or None)


def read_symbol(ctx: Context, symbol_id: str, version: str):
    index = ctx.base_index if version == "baseline" else ctx.final_index
    path = symbol_id.split("::", 1)[0]
    if is_excluded(ctx.root, path):
        return f"{path} is excluded by privacy rules", []
    source = index.symbol_source(symbol_id, ctx.settings.max_snippet_lines)
    if source is None:
        short = symbol_id.split("::")[-1].rsplit(".", 1)[-1]
        similar = sorted(index.by_short_name.get(short, ()))[:5]
        return f"unknown symbol {symbol_id} in {version} snapshot. Candidates: {similar}", []
    ctx.touch(path, symbol_id)
    _, sym = index.symbols[symbol_id]
    return _result(ctx, "code", f"{version} snapshot", f"{version} source of {symbol_id}", source, path=path,
                   line_start=sym.start_line, line_end=sym.end_line)


def search_code(ctx: Context, query: str, limit: int):
    if len(query.strip()) < 2:
        return "query too short", []
    needle, hits, n = query.lower(), [], _limit(ctx, limit)
    for path, source in _sources(ctx):
        for lineno, line in enumerate(source.splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{path}:{lineno}: {line.strip()[:160]}")
                if len(hits) >= n:
                    break
        if len(hits) >= n:
            break
    return _result(ctx, "search", f"search '{query}'", f"{len(hits)} matches for '{query}'", "\n".join(hits) or "none")


def find_callers(ctx: Context, symbol_id: str, depth: int):
    max_depth = ctx.settings.max_graph_depth
    depth = max(1, int(depth or 1))
    if depth > max_depth and not ctx.extra_expansion_used:
        depth, ctx.extra_expansion_used = max_depth + 1, True  # one extra expansion allowed per requirement
    depth = min(depth, max_depth + (1 if ctx.extra_expansion_used else 0))
    if symbol_id not in ctx.final_index.symbols and symbol_id not in ctx.base_index.symbols:
        return f"unknown symbol {symbol_id}", []
    found = ctx.final_index.expand(symbol_id, depth)
    rows = [{"symbol": s, "distance": d} for s, d in sorted(found.items(), key=lambda kv: kv[1]) if d > 0]
    return _result(ctx, "graph", "impact graph (name-based, approximate)",
                   f"{len(rows)} callers of {symbol_id} within depth {depth}", rows[:30])


def find_references(ctx: Context, symbol_id: str, limit: int):
    name = symbol_id.split("::")[-1].rsplit(".", 1)[-1]
    pattern, hits, n = re.compile(rf"\b{re.escape(name)}\b"), [], _limit(ctx, limit)
    for path, source in _sources(ctx):
        hits += [f"{path}:{i}: {line.strip()[:160]}" for i, line in enumerate(source.splitlines(), 1)
                 if pattern.search(line)]
        if len(hits) >= n:
            break
    return _result(ctx, "search", f"references to {name}", f"references to {name}", "\n".join(hits[:n]) or "none")


def find_related_tests(ctx: Context, query: str, limit: int):
    name = query.split("::")[-1].rsplit(".", 1)[-1].lower()
    title = re.compile(r"""\b(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)|def\s+(test_\w+)""")
    rows = []
    for path, source in _sources(ctx):
        if git.is_test(path) and name and name in source.lower():
            ctx.touch(path)
            names = [a or b for a, b in title.findall(source)]
            rows.append({"file": path, "tests": names[:12]})
    rows = rows[: _limit(ctx, limit)]
    return _result(ctx, "tests", f"related tests for '{query}'", f"{len(rows)} test files mention '{name}'", rows)


def get_agent_events(ctx: Context, query: str, limit: int):
    q = query.lower()
    rows = [
        {"t": e.timestamp, "type": e.event_type, "tool": e.tool_name, "path": e.path, "command": e.command,
         "success": e.success}
        for e in ctx.events
        if e.event_type != "user_prompt_submit" and q in f"{e.path or ''} {e.command or ''} {e.tool_name or ''}".lower()
    ][: _limit(ctx, limit)]
    return _result(ctx, "events", "claude code hooks", f"{len(rows)} Claude tool events matching '{query}'", rows)


def get_test_results(ctx: Context):
    if not ctx.checks:
        return "no project checks have run yet; use run_project_check", []
    rows = [{"kind": c.kind, "command": " ".join(c.command), "exit_code": c.exit_code, "evidence_id": c.evidence_id}
            for c in ctx.checks.values()]
    return _dump(rows), [c.evidence_id for c in ctx.checks.values() if c.evidence_id]


def run_project_check(ctx: Context, kind: str):
    if kind not in CHECK_KINDS:
        return f"unsupported check kind {kind}", []
    result = ctx.check(kind)
    if result is None:
        return f"no {kind} command is configured for this project", []
    tail = "\n".join((result.stdout_tail + "\n" + result.stderr_tail).strip().splitlines()[-25:])
    return (f"[evidence {result.evidence_id}] {' '.join(result.command)} exit={result.exit_code} "
            f"({result.duration_s}s)\n{tail}"), [result.evidence_id]


def get_evidence(ctx: Context, evidence_id: str):
    ev = ctx.store.get(evidence_id)
    if ev is None:
        return f"unknown evidence {evidence_id}", []
    raw = "\n".join(_clip(ctx.store.raw.get(evidence_id, "").splitlines(), ctx.settings.max_snippet_lines))
    return f"{_dump(ev.model_dump())}\n{raw}", [evidence_id]


HANDLERS = {
    f.__name__: f for f in [
        get_requirement, get_changed_files, get_file_diff, get_changed_symbols, read_symbol, search_code, find_callers,
        find_references, find_related_tests, get_agent_events, get_test_results, run_project_check, get_evidence,
    ]
}


def execute(ctx: Context, name: str, args: dict) -> tuple[str, list[str], bool]:
    """Run one tool call. Returns (model-bound text, evidence ids, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"unknown tool {name}", [], True
    ctx.tool_calls += 1
    try:
        text, ids = handler(ctx, **args)
    except (TypeError, ValueError, KeyError) as exc:
        return f"invalid arguments for {name}: {exc}", [], True
    return ctx.send(text), ids, False

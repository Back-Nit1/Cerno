"""Rule-based checks that run before any model call. A rule either settles a requirement or seeds evidence."""

import fnmatch
import json
import re
import tomllib
from dataclasses import dataclass, field

from cerno.analysis import git
from cerno.models import Requirement, Verdict
from cerno.privacy import find_secrets
from cerno.verification.context import Context

NEGATION = r"\b(no|not|don't|dont|never|without|avoid|unchanged|preserve|keep)\b"
TABLE_PATTERNS = re.compile(
    r"(CREATE\s+TABLE|^\s*model\s+\w+\s*\{|__tablename__|createTable\(|pgTable\(|sqliteTable\(|mysqlTable\()", re.I
)


@dataclass
class RuleResult:
    verdict: Verdict | None = None
    evidence_ids: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)


def _verdict(req: Requirement, status: str, level: str, summary: str, ids: list[str], missing=None) -> Verdict:
    return Verdict(requirement_id=req.id, status=status, evidence_level=level, summary=summary, evidence_ids=ids,
                   missing_evidence=missing or [], files_inspected=[], tool_calls_used=0)


def added_lines(ctx: Context, path: str) -> list[str]:
    return [line for hunk in git.get_diff_hunks(ctx.diff(path)) for line in hunk["added"]]


def _deps(root, tree: str, path: str) -> set[str]:
    text = git.read_blob(root, tree, path)
    if not text:
        return set()
    try:
        if path.endswith("package.json"):
            data = json.loads(text)
            return {f"{k}:{n}" for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
                    for n in data.get(k, {}) or {}}
        if path.endswith("pyproject.toml"):
            project = tomllib.loads(text).get("project", {})
            specs = list(project.get("dependencies", []))
            specs += [d for group in project.get("optional-dependencies", {}).values() for d in group]
            return {re.split(r"[<>=!~\[; ]", s, maxsplit=1)[0].lower() for s in specs}
        if path.rsplit("/", 1)[-1].startswith("requirements"):
            return {re.split(r"[<>=!~\[; ]", s.strip(), maxsplit=1)[0].lower()
                    for s in text.splitlines() if s.strip() and not s.startswith(("#", "-"))}
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError):
        return set()
    return set()


def rule_tests_added(req: Requirement, ctx: Context, text: str) -> RuleResult | None:
    if not re.search(r"\b(add|write|include|create|cover)\w*\b.*\btests?\b|\btests?\b.*\b(added|written|coverage)\b", text):
        return None
    tests = [c for c in ctx.changes if git.is_test(c.path) and c.status != "deleted"]
    names = ", ".join(c.path for c in tests[:10]) or "none"
    ev = ctx.store.add("rule", "rule:tests_changed", f"Test files added/modified during session: {names}",
                       raw="\n".join(f"{c.status} {c.path} (+{c.additions}/-{c.deletions})" for c in tests))
    out = RuleResult(evidence_ids=[ev.id], plan=["rule: detect test files changed between baseline and final"])
    if not tests:
        out.verdict = _verdict(req, "VIOLATED", "structural", "No test files were added or modified.", [ev.id])
        return out
    check = ctx.check("test")
    out.plan.append("runtime: run allowlisted test command")
    if check is None:
        out.verdict = _verdict(req, "LIKELY", "structural", f"Tests changed ({names}) but no test command is configured.",
                               [ev.id], ["execution of the new tests"])
    elif check.exit_code == 0:
        out.evidence_ids.append(check.evidence_id)
        out.verdict = _verdict(req, "VERIFIED", "test", f"Tests were added ({names}) and the test suite passes.",
                               out.evidence_ids)
    else:
        out.evidence_ids.append(check.evidence_id)  # failing suite: let the agent judge relevance
    return out


def rule_check_passes(req: Requirement, ctx: Context, text: str) -> RuleResult | None:
    match = re.search(r"\b(tests?|build|lint|type ?check\w*|types?)\b[\w\s]{0,20}\b(pass\w*|succeed\w*|green|clean|compile\w*)", text)
    if not match:
        return None
    word = match.group(1)
    kind = "test" if word.startswith("test") else "build" if word == "build" else "lint" if word == "lint" else "typecheck"
    check = ctx.check(kind)
    out = RuleResult(plan=[f"runtime: run allowlisted {kind} command"])
    if check is None:
        out.verdict = _verdict(req, "UNCERTAIN", "insufficient", f"No {kind} command is configured for this project.",
                               [], [f"a configured {kind} command"])
        return out
    out.evidence_ids.append(check.evidence_id)
    if check.exit_code is None:
        out.verdict = _verdict(req, "UNCERTAIN", "insufficient", f"The {kind} command could not complete.",
                               [check.evidence_id], [f"a completed {kind} run"])
    else:
        passed = check.exit_code == 0
        out.verdict = _verdict(req, "VERIFIED" if passed else "VIOLATED", "test" if kind == "test" else "runtime",
                               f"`{' '.join(check.command)}` exited {check.exit_code}.", [check.evidence_id])
    return out


def rule_dependencies(req: Requirement, ctx: Context, text: str) -> RuleResult | None:
    if not (re.search(r"\b(dependenc\w*|packages?|librar\w*)\b", text) and re.search(NEGATION, text)):
        return None
    manifests = [c for c in ctx.changes if git.is_manifest(c.path)]
    added: set[str] = set()
    for c in manifests:
        if c.path.endswith(("package.json", "pyproject.toml")) or c.path.rsplit("/", 1)[-1].startswith("requirements"):
            added |= _deps(ctx.root, ctx.final_tree, c.path) - _deps(ctx.root, ctx.base_tree, c.old_path or c.path)
    summary = (f"Manifests changed: {', '.join(c.path for c in manifests) or 'none'}; "
               f"new dependencies: {', '.join(sorted(added)) or 'none'}")
    ev = ctx.store.add("rule", "rule:dependency_manifests", summary)
    out = RuleResult(evidence_ids=[ev.id], plan=["rule: compare dependency manifests baseline vs final"])
    if added:
        out.verdict = _verdict(req, "VIOLATED", "structural", f"New dependencies were added: {', '.join(sorted(added))}.",
                               [ev.id])
    elif not manifests:
        out.verdict = _verdict(req, "VERIFIED", "structural", "No dependency manifest or lockfile changed.", [ev.id])
    elif all(c.path.endswith(("package.json", "pyproject.toml")) for c in manifests):
        out.verdict = _verdict(req, "VERIFIED", "structural", "Manifests changed but declare no new dependencies.", [ev.id])
    return out


def rule_schema(req: Requirement, ctx: Context, text: str) -> RuleResult | None:
    if not (re.search(r"\b(schema|migrations?|database|db|tables?)\b", text) and re.search(NEGATION, text)):
        return None
    schema_changes = [c for c in ctx.changes if git.is_schema(c.path)]
    table_hits = [
        f"{c.path}: {line.strip()[:120]}" for c in ctx.changes if c.status != "deleted"
        for line in added_lines(ctx, c.path) if TABLE_PATTERNS.search(line)
    ]
    summary = (f"Schema/migration files changed: {', '.join(c.path for c in schema_changes) or 'none'}; "
               f"new table definitions in added lines: {len(table_hits)}")
    ev = ctx.store.add("rule", "rule:schema_changes", summary, raw="\n".join(table_hits))
    out = RuleResult(evidence_ids=[ev.id], plan=["rule: inspect schema/migration paths and added table definitions"])
    mentions_table = re.search(r"\btables?\b", text)
    if table_hits:
        out.verdict = _verdict(req, "VIOLATED", "structural", f"New table definition(s) added: {table_hits[0]}", [ev.id])
    elif not schema_changes:
        out.verdict = _verdict(req, "VERIFIED", "structural", "No schema or migration files changed and no table "
                               "definitions were added.", [ev.id])
    elif not mentions_table:
        out.verdict = _verdict(req, "VIOLATED", "structural", "Schema/migration files changed: "
                               + ", ".join(c.path for c in schema_changes), [ev.id])
    return out


def rule_protected_paths(req: Requirement, ctx: Context, text: str) -> RuleResult | None:
    if not (re.search(NEGATION, text) and re.search(r"\b(modify|change|touch|edit|alter|delete)\w*\b", text)):
        return None
    all_files = set(git.list_tree_files(ctx.root, ctx.base_tree))
    tokens = set(re.findall(r"[\w.*/-]+", req.text))
    tokens |= {a for a in ctx.contract.protected_areas if a.lower() in text}
    patterns = [
        t.rstrip("/") for t in tokens
        if ("/" in t or "." in t.strip(".")) and any(f == t or f.startswith(t.rstrip("/") + "/") or fnmatch.fnmatch(f, t)
                                                   for f in all_files)
    ]
    if not patterns:
        return None
    hits = [c.path for c in ctx.changes
            if any(c.path == p or c.path.startswith(p + "/") or fnmatch.fnmatch(c.path, p) for p in patterns)]
    ev = ctx.store.add("rule", "rule:protected_paths",
                       f"Protected paths {patterns}; changed within them: {', '.join(hits) or 'none'}")
    status = "VIOLATED" if hits else "VERIFIED"
    summary = f"Protected paths changed: {', '.join(hits)}" if hits else "No files under the protected paths changed."
    return RuleResult(_verdict(req, status, "structural", summary, [ev.id]), [ev.id], ["rule: map changes to protected paths"])


def rule_secrets(req: Requirement, ctx: Context, text: str) -> RuleResult | None:
    if not re.search(r"\b(secrets?|credentials?|api keys?|environment variables?|env vars?|hard-?coded)\b|\.env\b", text):
        return None
    hits, env_refs, scanned = [], set(), 0
    for c in ctx.changes:
        if c.status == "deleted":
            continue
        lines = added_lines(ctx, c.path)
        scanned += len(lines)
        hits += [f"{c.path}: {kind}: {line}" for kind, line in find_secrets("\n".join(lines))]
        for line in lines:
            env_refs |= set(re.findall(r"process\.env\.(\w+)|os\.environ(?:\.get)?\[?\(?['\"](\w+)", line))
    names = sorted({a or b for a, b in env_refs})
    ev = ctx.store.add(
        "rule", "rule:secret_scan",
        f"Scanned {scanned} added lines: {len(hits)} suspicious hardcoded-secret pattern(s) (suspicious, not confirmed); "
        f"environment variable references added: {', '.join(names) or 'none'}",
        raw="\n".join(hits),
    )
    return RuleResult(evidence_ids=[ev.id], plan=["rule: scan added lines for secret patterns and env references"])


RULES = [rule_check_passes, rule_tests_added, rule_dependencies, rule_schema, rule_protected_paths, rule_secrets]


def apply_rules(req: Requirement, ctx: Context) -> RuleResult:
    text = f"{req.text} {req.verification_hint or ''}".lower()
    merged = RuleResult()
    for rule in RULES:
        result = rule(req, ctx, text)
        if result is None:
            continue
        merged.evidence_ids += result.evidence_ids
        merged.plan += result.plan
        if result.verdict is not None:
            merged.verdict = result.verdict.model_copy(update={"evidence_ids": list(dict.fromkeys(merged.evidence_ids))})
            break
    return merged

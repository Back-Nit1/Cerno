"""The built-in rule pack and `.cerno.toml` loading.

Rule wording is the main precision control in this product: each rule names a concrete production harm,
says what must be true before it may be reported, and rules out the categories a reviewer does not want.
"""

import fnmatch
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cerno.review.models import SEVERITY_ORDER, ReviewRule

RULES_FILE = ".cerno.toml"
SEVERITIES = tuple(SEVERITY_ORDER)

SCOPE = (
    " Out of scope, never report: style, naming, formatting, comments, dead code, type annotations, test "
    "coverage gaps, performance, portability, and any refactor or design suggestion. Out of scope: anything "
    "in code this diff did not change, and anything you cannot tie to a specific changed line. Report a "
    "violation only when the diff itself introduces the harm above and at least one caller, test, or check "
    "result outside the diff shows it; if nothing outside the diff supports it, say so and stay UNCERTAIN."
)

BUILTIN = [
    ReviewRule(
        id="CERNO-CONTRACT",
        severity="high",
        text=(
            "The diff must not break an existing caller or a published API contract. Violated when a function, "
            "method, class, exported symbol, HTTP route, CLI flag, config key, event name, database column or "
            "response field that existed before the diff is removed or renamed, or has its parameter list, "
            "required arguments, return shape, status code, error type or null-handling changed, while a caller "
            "or consumer that still uses the old form remains in the repository or in the project's tests."
            + SCOPE
        ),
    ),
    ReviewRule(
        id="CERNO-AUTHZ",
        severity="critical",
        text=(
            "The diff must not remove or weaken an authentication, authorization, ownership, tenancy, CSRF, "
            "signature, rate-limit or input-validation check that guarded a path before the diff. Violated when "
            "such a check is deleted, made conditional, moved after the action it protected, downgraded so a "
            "failure logs or warns instead of rejecting, given a default that permits access, or replaced by a "
            "weaker comparison (for example an identity check replaced by a presence check)." + SCOPE
        ),
    ),
    ReviewRule(
        id="CERNO-DATA-EXPOSURE",
        severity="critical",
        text=(
            "The diff must not make secrets, credentials, tokens, personal data or another user's or tenant's "
            "records reach somewhere they did not reach before: a log line, an exception message, an HTTP "
            "response body, a client-side bundle, an analytics or telemetry event, or a file. Violated also "
            "when a query, serializer or response loses the filter, field allowlist or tenant/user predicate "
            "that previously limited which records or fields were returned." + SCOPE
        ),
    ),
    ReviewRule(
        id="CERNO-DESTRUCTIVE",
        severity="critical",
        text=(
            "The diff must not introduce an irreversible data or schema change. Violated when it drops or "
            "truncates a table or column, changes a column type or constraint in a way that loses or rejects "
            "existing rows, adds a migration with no safe path for existing data, or adds or widens a DELETE / "
            "UPDATE / destructive write whose restricting predicate is absent, weakened, or driven by unvalidated "
            "input." + SCOPE
        ),
    ),
    ReviewRule(
        id="CERNO-BEHAVIOR",
        severity="high",
        text=(
            "The diff must not change existing behaviour outside its apparent purpose. Violated when a default "
            "value, constant, threshold, timeout, retry count, expiry, limit, sort order, rounding, timezone, "
            "feature-flag default, or an early return or branch condition is changed so that existing inputs "
            "now produce a different result, and that change is not part of what the diff set out to do. "
            "Changing behaviour the diff clearly exists to change is not a violation." + SCOPE
        ),
    ),
]


class RulesError(ValueError):
    """Unreadable or malformed rules file. Names the offending key."""


@dataclass
class RuleSet:
    rules: list[ReviewRule]
    min_severity: str
    sources: list[str]


def matches(rule: ReviewRule, changed_paths: list[str]) -> bool:
    """A rule with `paths` applies only when a changed file matches one of them."""
    if not rule.paths:
        return True
    return any(fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p, pattern.rstrip("/*") + "/*")
               for pattern in rule.paths for p in changed_paths)


def _str(data: dict, key: str, where: str, required: bool = False) -> str | None:
    value = data.get(key)
    if value is None:
        if required:
            raise RulesError(f"{where}: missing required key '{key}'")
        return None
    if not isinstance(value, str):
        raise RulesError(f"{where}: '{key}' must be a string")
    return value


def _severity(value: str | None, where: str, default: str) -> str:
    if value is None:
        return default
    if value not in SEVERITIES:
        raise RulesError(f"{where}: 'severity' must be one of {', '.join(SEVERITIES)} (got {value!r})")
    return value


def _str_list(data: dict, key: str, where: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise RulesError(f"{where}: '{key}' must be a list of strings")
    return value


def load_rules(root: Path, rules_path: Path | None = None) -> RuleSet:
    """Built-in pack plus `.cerno.toml`. Raises RulesError for anything malformed."""
    explicit = rules_path is not None
    path = rules_path or root / RULES_FILE
    if not path.is_file():
        if explicit:
            raise RulesError(f"{path}: rules file not found")
        return RuleSet(list(BUILTIN), "high", ["builtin"])
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise RulesError(f"{path}: {exc}") from exc

    where = f"{path} [review]"
    review = data.get("review", {})
    if not isinstance(review, dict):
        raise RulesError(f"{path}: 'review' must be a table")
    use_builtin = review.get("builtin", True)
    if not isinstance(use_builtin, bool):
        raise RulesError(f"{where}: 'builtin' must be true or false")
    disabled = set(_str_list(review, "disable", where))
    min_severity = _severity(_str(review, "min_severity", where), where, "high")

    rules = [r for r in BUILTIN if use_builtin]
    entries = data.get("rule", [])
    if not isinstance(entries, list):
        raise RulesError(f"{path}: '[[rule]]' must be an array of tables")
    for n, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise RulesError(f"{path} [[rule]] #{n}: must be a table")
        spot = f"{path} [[rule]] #{n}"
        rule_id = _str(entry, "id", spot, required=True)
        rules.append(ReviewRule(
            id=rule_id, text=_str(entry, "text", f"{spot} ({rule_id})", required=True),
            severity=_severity(_str(entry, "severity", f"{spot} ({rule_id})"), f"{spot} ({rule_id})", "high"),
            source="file", paths=_str_list(entry, "paths", f"{spot} ({rule_id})"),
        ))
    rules = [r for r in rules if r.id not in disabled]
    sources = (["builtin"] if use_builtin else []) + [str(path.name if path.parent == root else path)]
    return RuleSet(rules, min_severity, sources)

"""Markdown rendering of a review result (schema_version 1).

The input is the plain result dict, so nothing is imported from the review engine and a JSON file written
by any version renders. Repository content - paths, titles, code snippets - is untrusted input and is
escaped before it reaches the output.
"""

import json
import re
import sys
from enum import Enum
from typing import Any

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_ESCAPED = "\\`*_[]<>|~"
_TRANSLATION = str.maketrans({character: "\\" + character for character in _ESCAPED})
_BLOCK_START = re.compile(r"\d+[.)] ")


def _plain(value: object) -> object:
    """`model_dump()` keeps enum members: unwrap them so severities sort and render as their value."""
    return value.value if isinstance(value, Enum) else value


def _text(value: object) -> str:
    """Inline prose from repository content: collapse newlines, escape Markdown punctuation."""
    return str(_plain(value)).replace("\n", " ").translate(_TRANSLATION)


def _block(value: object) -> str:
    """Same, for text that starts a line: a leading list or heading marker would open a block."""
    text = _text(value)
    return "\\" + text if text[:1] in "#-+" or _BLOCK_START.match(text) else text


def _code(value: object) -> str:
    """Inline code span with a backtick fence long enough to survive backticks in the content."""
    text = str(_plain(value)).replace("\n", " ")
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _short(sha: object) -> str:
    return str(sha)[:7] if sha else "?"


def _location(finding: dict[str, Any]) -> str:
    path = finding.get("path")
    if not path:
        return ""
    line, end = finding.get("line"), finding.get("end_line")
    if line and end and end != line:
        return _code(f"{path}:{line}-{end}")
    return _code(f"{path}:{line}" if line else path)


def _evidence(item: dict[str, Any]) -> str:
    bits = [_code(item.get("id") or "E-?")]
    kind, level = item.get("type"), item.get("level")
    if kind and level:
        bits.append(_text(f"{kind} ({level})"))
    elif kind or level:
        bits.append(_text(kind or level))
    if path := item.get("path"):
        start = item.get("line_start")
        bits.append(_code(f"{path}:{start}" if start else path))
    if summary := item.get("summary"):
        bits.append(_text(summary))
    return " - ".join(bits)


def _finding(finding: dict[str, Any]) -> list[str]:
    meta = [
        _code(finding.get("rule_id") or "?"),
        _text(finding.get("severity") or "unknown severity"),
        _text(finding.get("status") or "unknown status"),
    ]
    if location := _location(finding):
        meta.append(location)
    lines = [f"## {_block(finding.get('title') or '(untitled)')}", "", " · ".join(meta)]
    if explanation := finding.get("explanation"):
        lines += ["", _block(explanation)]
    if action := finding.get("next_action"):
        lines += ["", f"**Next:** {_text(action)}"]
    if evidence := finding.get("evidence") or []:
        lines += ["", "Evidence:", ""]
        lines += [f"- {_evidence(item)}" for item in evidence]
    return lines


def _insufficient(item: dict[str, Any]) -> str:
    line = f"- {_code(item.get('rule_id') or '?')} - {_text(item.get('summary') or 'no detail given')}"
    if missing := item.get("missing_evidence") or []:
        line += f" Missing: {', '.join(_text(entry) for entry in missing)}."
    return line


def _footer(result: dict[str, Any]) -> str:
    base, head = result.get("base") or {}, result.get("head") or {}
    usage = result.get("usage") or {}
    bits = [
        f"base {_code(_short(base.get('sha')))}",
        f"head {_code(_short(head.get('sha'))) if head.get('sha') else 'working tree'}",
        f"model {_code(result['model']) if result.get('model') else 'none'}",
    ]
    if (wall := usage.get("wall_time_s")) is not None:
        bits.append(f"{wall:.1f}s")
    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        bits.append(f"{usage.get('input_tokens') or 0} in / {usage.get('output_tokens') or 0} out tokens")
    if (cost := usage.get("cost_usd")) is not None:
        bits.append(f"${cost:.4f}")
    return " · ".join(bits)


def render_markdown(result: dict[str, Any]) -> str:
    """Render a review result for a reviewer deciding whether to block a merge."""
    all_findings = result.get("findings") or []
    shown = [finding for finding in all_findings if finding.get("shown", True)]
    shown.sort(key=lambda finding: SEVERITY_ORDER.get(_plain(finding.get("severity")), len(SEVERITY_ORDER)))
    suppressed = len(all_findings) - len(shown)

    if shown:
        lines = [f"# {len(shown)} finding{'' if len(shown) == 1 else 's'}"]
    else:
        lines = ["# No production risks found"]
    for finding in shown:
        lines += ["", *_finding(finding)]
    if insufficient := result.get("insufficient") or []:
        lines += ["", "## Insufficient evidence", ""]
        lines += [_insufficient(item) for item in insufficient]
    if errors := result.get("errors") or []:
        lines += ["", "## Errors", ""]
        lines += [f"- {_block(error)}" for error in errors]
    if suppressed:
        lines += ["", f"{suppressed} finding{'' if suppressed == 1 else 's'} suppressed "
                      "(below the severity threshold, outside the diff, or not evidence-backed); "
                      "see the JSON result."]
    lines += ["", _footer(result)]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m cerno.report.markdown RESULT.json", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {argv[0]}: {exc}", file=sys.stderr)
        return 2
    print(render_markdown(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cerno.report.markdown import render_markdown

SAMPLES = Path(__file__).resolve().parents[1] / "examples" / "review"
NAMES = ["clean", "critical-finding", "no-api-key"]


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text())


@pytest.mark.parametrize("name", NAMES)
def test_golden_markdown(name):
    assert render_markdown(_sample(name)) == (SAMPLES / f"{name}.md").read_text()


def test_clean_review_says_so_and_lists_no_findings():
    out = render_markdown(_sample("clean"))
    assert out.splitlines()[0] == "# No production risks found"
    assert "##" not in out
    assert "suppressed" not in out


def test_shown_finding_carries_rule_severity_status_location_and_action():
    out = render_markdown(_sample("critical-finding"))
    assert out.splitlines()[0] == "# 1 finding"
    assert "`CERNO-AUTHZ` · critical · confirmed · `src/auth/reset.js:34-41`" in out
    assert "**Next:** Restore the expiry comparison" in out
    assert "`E-test-01` - check\\_test (test) - `test/reset.test.js:48`" in out


def test_suppressed_findings_collapse_to_a_count_and_are_never_listed():
    out = render_markdown(_sample("critical-finding"))
    assert "1 finding suppressed" in out
    assert "Rate limit window widened" not in out  # the suppressed finding's title
    assert "rateLimit.js" not in out


def test_insufficient_entries_render_with_their_missing_evidence():
    out = render_markdown(_sample("no-api-key"))
    assert "## Insufficient evidence" in out
    assert "- `CERNO-AUTHZ` - Not evaluated: no model provider configured. Missing: OPENAI\\_API\\_KEY." in out


def test_footer_reports_working_tree_and_no_model_without_a_head_sha():
    footer = render_markdown(_sample("no-api-key")).strip().splitlines()[-1]
    assert footer == "base `9f1c2e8` · head working tree · model none · 0.9s · 0 in / 0 out tokens"


def test_cost_appears_only_when_supplied():
    assert "$0.1820" in render_markdown(_sample("critical-finding"))
    assert "$" not in render_markdown(_sample("clean"))


def test_findings_are_ordered_most_severe_first():
    result = {
        "findings": [
            {"title": "low one", "severity": "low", "shown": True},
            {"title": "critical one", "severity": "critical", "shown": True},
            {"title": "odd one", "severity": "banana", "shown": True},
            {"title": "high one", "severity": "high", "shown": True},
        ]
    }
    titles = [line[3:] for line in render_markdown(result).splitlines() if line.startswith("## ")]
    assert titles == ["critical one", "high one", "low one", "odd one"]


def test_missing_optional_keys_are_tolerated():
    assert render_markdown({}).splitlines()[0] == "# No production risks found"
    assert render_markdown({"findings": [{}]}) .splitlines()[0] == "# 1 finding"
    out = render_markdown({"findings": [{}], "usage": None, "base": None, "head": None})
    assert "## (untitled)" in out
    assert "unknown severity · unknown status" in out
    assert "base `?` · head working tree · model none" in out.strip().splitlines()[-1]


def test_repository_content_cannot_inject_markdown_or_html():
    out = render_markdown({
        "findings": [{
            "title": "<img src=x onerror=alert(1)>",
            "severity": "critical",
            "shown": True,
            "path": "src/[a](b)/*weird*.js",
            "line": 3,
            "explanation": "- not a bullet",
            "evidence": [{"id": "E-1", "summary": "sets __proto__ and [links](http://x)"}],
        }]
    })
    assert "\\<img src=x onerror=alert(1)\\>" in out
    assert "<img" not in out.replace("\\<", "&lt;")  # only ever as an escaped literal
    assert "\n\\- not a bullet" in out
    assert "\\_\\_proto\\_\\_" in out
    assert "\\[links\\](http://x)" in out


def test_backticks_in_a_path_do_not_break_out_of_the_code_span():
    out = render_markdown({"findings": [{"title": "t", "shown": True, "path": "src/`a``b`.js", "line": 1}]})
    assert "```src/`a``b`.js:1```" in out


def test_leading_numeric_marker_in_a_block_is_escaped():
    out = render_markdown({"findings": [{"title": "1. heading-ish", "shown": True}]})
    assert "## \\1. heading-ish" in out


@pytest.mark.parametrize("name", NAMES)
def test_module_is_runnable(name):
    proc = subprocess.run(
        [sys.executable, "-m", "cerno.report.markdown", str(SAMPLES / f"{name}.json")],
        capture_output=True, text=True, shell=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (SAMPLES / f"{name}.md").read_text()


def test_module_reports_bad_usage_and_unreadable_files():
    for argv in ([], [__file__, __file__], ["/nope/missing.json"]):
        proc = subprocess.run(
            [sys.executable, "-m", "cerno.report.markdown", *argv], capture_output=True, text=True, shell=False,
        )
        assert proc.returncode == 2, proc.stdout
        assert proc.stderr.strip()


# --- the engine hands us result.model_dump(), so render the real thing pydantic produces -------------


def _dumped_result(severity_type) -> dict:
    """A pydantic model shaped like the documented JSON, dumped the way the CLI dumps it."""
    from enum import Enum

    from pydantic import BaseModel

    class Severity(severity_type, Enum):
        CRITICAL = "critical"
        HIGH = "high"

    class Evidence(BaseModel):
        id: str
        type: str
        level: str
        path: str | None
        line_start: int
        summary: str

    class Finding(BaseModel):
        id: str
        rule_id: str
        title: str
        severity: Severity
        status: str
        shown: bool
        suppressed_reason: str | None
        path: str
        line: int
        end_line: int
        explanation: str
        next_action: str
        evidence: list[Evidence]

    class Ref(BaseModel):
        ref: str | None
        sha: str | None
        tree: str

    class Usage(BaseModel):
        wall_time_s: float
        model_calls: int
        input_tokens: int
        output_tokens: int
        cache_read_tokens: int
        cost_usd: float | None

    class ReviewResult(BaseModel):
        schema_version: int = 1
        base: Ref
        head: Ref
        merge_base: str
        model: str | None
        rules_source: list[str]
        findings: list[Finding]
        insufficient: list[dict]
        rules: list[dict]
        checks: list[dict]
        usage: Usage
        errors: list[str]

    def finding(key: str, severity: Severity, shown: bool) -> Finding:
        return Finding(
            id=key * 12, rule_id="CERNO-AUTHZ", title=f"{severity.value} problem", severity=severity,
            status="confirmed", shown=shown, suppressed_reason=None if shown else "below min_severity",
            path="src/a_b.py", line=4, end_line=4, explanation="drops the check", next_action="restore it",
            evidence=[Evidence(id="E-1", type="check_test", level="test", path="t.py", line_start=2,
                               summary="1 failing")],
        )

    result = ReviewResult(
        base=Ref(ref="main", sha="9f1c2e8a7b3d", tree="t1"),
        head=Ref(ref=None, sha=None, tree="t2"),
        merge_base="9f1c2e8a7b3d", model=None, rules_source=["builtin"],
        findings=[finding("a", Severity.HIGH, True), finding("b", Severity.CRITICAL, True),
                  finding("c", Severity.HIGH, False)],
        insufficient=[], rules=[], checks=[],
        usage=Usage(wall_time_s=1.0, model_calls=0, input_tokens=0, output_tokens=0,
                    cache_read_tokens=0, cost_usd=None),
        errors=[],
    )
    return result.model_dump()


def test_renders_a_pydantic_model_dump():
    out = render_markdown(_dumped_result(str))
    titles = [line[3:] for line in out.splitlines() if line.startswith("## ")]
    assert titles == ["critical problem", "high problem"]  # most severe first, suppressed one absent
    assert "Severity." not in out
    assert "`CERNO-AUTHZ` \u00b7 critical \u00b7 confirmed \u00b7 `src/a_b.py:4`" in out
    assert "1 finding suppressed" in out
    assert out.strip().splitlines()[-1].startswith("base `9f1c2e8` \u00b7 head working tree \u00b7 model none")


def test_non_str_enum_severities_still_sort_and_render_by_value():
    from enum import Enum

    class Severity(Enum):
        CRITICAL = "critical"
        LOW = "low"

    result = {"findings": [{"title": "second", "severity": Severity.LOW, "shown": True},
                           {"title": "first", "severity": Severity.CRITICAL, "shown": True}]}
    out = render_markdown(result)
    assert [line[3:] for line in out.splitlines() if line.startswith("## ")] == ["first", "second"]
    assert "critical ·" in out and "Severity." not in out

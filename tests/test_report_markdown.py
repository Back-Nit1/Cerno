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

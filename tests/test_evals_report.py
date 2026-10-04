import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.report import render_report, score_run  # noqa: E402


def finding(path, line, rule_id="CERNO-AUTHZ", shown=True):
    return {"path": path, "line": line, "rule_id": rule_id, "severity": "high", "shown": shown}


def record(case_id, kind, findings, expected=None, **usage):
    return {
        "case_id": case_id,
        "label": {"kind": kind, "expected": expected or []},
        "findings": findings,
        "wall_time_s": usage.get("wall_time_s", 1),
        "input_tokens": usage.get("input_tokens", 10),
        "output_tokens": usage.get("output_tokens", 2),
        "cost_usd": usage.get("cost_usd"),
    }


EXPECTED = [
    {
        "rule_id": "CERNO-AUTHZ",
        "path": "auth.py",
        "line_start": 10,
        "line_end": 12,
        "why": "permission removed",
    }
]


def test_scorer_separates_location_rule_and_false_positives():
    records = [
        record("violation", "violation", [finding("auth.py", 11, "CERNO-CONTRACT")], EXPECTED),
        record("correct", "correct", [finding("clean.py", 3)]),
        record("insufficient", "insufficient", []),
    ]
    score = score_run(records, "diff")
    assert score["violations_detected"] == 1
    assert score["violations_missed"] == 0
    assert score["rule_agreement"] == 0
    assert score["false_positive_cases"] == 1
    assert score["findings_per_correct"] == 1
    assert score["abstention_accuracy"] == 1
    assert score["location_accuracy"] == 1
    assert score["median_tokens"] == 12


def test_cerno_scores_only_shown_and_counts_suppression_loss():
    records = [record("violation", "violation", [finding("auth.py", 10, shown=False)], EXPECTED)]
    score = score_run(records, "cerno")
    assert score["violations_detected"] == 0
    assert score["violations_missed"] == 1
    assert score["suppression_losses"] == 1


def test_markdown_displays_min_max_across_runs():
    first = [record("violation", "violation", [finding("auth.py", 10)], EXPECTED)]
    second = [record("violation", "violation", [], EXPECTED)]
    report = render_report({("diff", "dev"): [first, second]})
    assert "## diff" in report
    assert "0–1 / 1" in report
    assert "minimum–maximum" in report

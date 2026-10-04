"""Score benchmark result files and render run ranges as Markdown."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def normalized_path(value: str) -> str:
    value = value.removeprefix("./")
    return value[2:] if value.startswith(("a/", "b/")) else value


def location_matches(finding: dict[str, Any], expected: dict[str, Any]) -> bool:
    line = finding.get("line")
    return (
        normalized_path(str(finding.get("path", ""))) == expected["path"]
        and isinstance(line, int)
        and expected["line_start"] <= line <= expected["line_end"]
    )


def shown_findings(record: dict[str, Any], arm: str) -> list[dict[str, Any]]:
    findings = record.get("findings", [])
    return [finding for finding in findings if arm != "cerno" or finding.get("shown") is True]


def score_run(records: list[dict[str, Any]], arm: str) -> dict[str, int | float | None]:
    score: dict[str, int | float | None] = {
        "violations_total": 0,
        "violations_detected": 0,
        "violations_missed": 0,
        "rule_agreement_n": 0,
        "rule_agreement_d": 0,
        "correct_total": 0,
        "false_positive_cases": 0,
        "correct_findings": 0,
        "insufficient_total": 0,
        "correct_abstentions": 0,
        "located_findings": 0,
        "violation_findings": 0,
        "suppression_losses": 0,
    }
    walls = []
    tokens = []
    costs = []
    any_missing_cost = False
    for record in records:
        label = record["label"]
        findings = shown_findings(record, arm)
        kind = label["kind"]
        walls.append(float(record.get("wall_time_s", 0)))
        tokens.append(int(record.get("input_tokens", 0)) + int(record.get("output_tokens", 0)))
        cost = record.get("cost_usd")
        if cost is None:
            any_missing_cost = True
        else:
            costs.append(float(cost))
        if kind == "correct":
            score["correct_total"] += 1
            score["correct_findings"] += len(findings)
            score["false_positive_cases"] += bool(findings)
        elif kind == "insufficient":
            score["insufficient_total"] += 1
            score["correct_abstentions"] += not findings
        elif kind == "violation":
            score["violations_total"] += 1
            expected = label["expected"]
            matching = [
                finding for finding in findings if any(location_matches(finding, item) for item in expected)
            ]
            detected = bool(matching)
            score["violations_detected"] += detected
            score["violations_missed"] += not detected
            score["violation_findings"] += len(findings)
            score["located_findings"] += len(matching)
            if detected:
                score["rule_agreement_d"] += 1
                agrees = any(
                    location_matches(finding, item) and finding.get("rule_id") == item["rule_id"]
                    for finding in matching
                    for item in expected
                )
                score["rule_agreement_n"] += agrees
            if arm == "cerno" and not detected:
                suppressed = [finding for finding in record.get("findings", []) if finding.get("shown") is False]
                score["suppression_losses"] += any(
                    location_matches(finding, item) for finding in suppressed for item in expected
                )
    correct_total = int(score["correct_total"])
    score["findings_per_correct"] = (
        int(score["correct_findings"]) / correct_total if correct_total else 0.0
    )
    violation_findings = int(score["violation_findings"])
    score["location_accuracy"] = (
        int(score["located_findings"]) / violation_findings if violation_findings else 0.0
    )
    score["rule_agreement"] = (
        int(score["rule_agreement_n"]) / int(score["rule_agreement_d"])
        if score["rule_agreement_d"]
        else 0.0
    )
    score["abstention_accuracy"] = (
        int(score["correct_abstentions"]) / int(score["insufficient_total"])
        if score["insufficient_total"]
        else 0.0
    )
    score["median_wall_time_s"] = statistics.median(walls) if walls else 0.0
    score["median_tokens"] = statistics.median(tokens) if tokens else 0.0
    score["median_cost_usd"] = None if any_missing_cost or not costs else statistics.median(costs)
    return score


def load_runs(path: Path) -> dict[tuple[str, str], list[list[dict[str, Any]]]]:
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    grouped: dict[tuple[str, str], list[list[dict[str, Any]]]] = defaultdict(list)
    for result_path in files:
        payload = json.loads(result_path.read_text())
        if payload.get("schema_version") != 1 or "arm" not in payload or "results" not in payload:
            continue
        by_run: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for record in payload["results"]:
            by_run[int(record["run"])].append(record)
        for records in by_run.values():
            grouped[(payload["arm"], payload["split"])].append(records)
    if not grouped:
        raise ValueError(f"no benchmark result files found under {path}")
    return grouped


def number_range(scores: list[dict[str, Any]], field: str, digits: int = 0) -> str:
    values = [score[field] for score in scores]
    if digits:
        return f"{min(values):.{digits}f}–{max(values):.{digits}f}"
    return f"{min(values):.0f}–{max(values):.0f}"


def rate_range(scores: list[dict[str, Any]], field: str) -> str:
    values = [100 * score[field] for score in scores]
    return f"{min(values):.0f}–{max(values):.0f}%"


def count_of_total(scores: list[dict[str, Any]], field: str, total: str) -> str:
    totals = {int(score[total]) for score in scores}
    suffix = str(totals.pop()) if len(totals) == 1 else number_range(scores, total)
    return f"{number_range(scores, field)} / {suffix}"


def render_report(grouped: dict[tuple[str, str], list[list[dict[str, Any]]]]) -> str:
    lines = [
        "# Cerno review evaluation",
        "",
        "Ranges are minimum–maximum across runs. With only 24 synthetic cases, these intervals are wide ",
        "and should not be read as stable population estimates.",
    ]
    for arm in sorted({key[0] for key in grouped}):
        lines.extend(
            [
                "",
                f"## {arm}",
                "",
                "| Split | Runs | Violations detected | Violations missed | Rule-id agreement | "
                "False-positive cases | Findings / correct case | Insufficient abstention | "
                "Location accuracy | Suppression losses | Median wall s | Median tokens | Median cost USD |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for key in sorted(item for item in grouped if item[0] == arm):
            split = key[1]
            scores = [score_run(records, arm) for records in grouped[key]]
            cost_values = [score["median_cost_usd"] for score in scores]
            if any(value is None for value in cost_values):
                cost = "n/a"
            else:
                cost = f"${min(cost_values):.4f}–${max(cost_values):.4f}"
            lines.append(
                f"| {split} | {len(scores)} | {count_of_total(scores, 'violations_detected', 'violations_total')} "
                f"| {count_of_total(scores, 'violations_missed', 'violations_total')} "
                f"| {rate_range(scores, 'rule_agreement')} "
                f"| {count_of_total(scores, 'false_positive_cases', 'correct_total')} "
                f"| {number_range(scores, 'findings_per_correct', 2)} "
                f"| {rate_range(scores, 'abstention_accuracy')} "
                f"| {rate_range(scores, 'location_accuracy')} "
                f"| {number_range(scores, 'suppression_losses')} "
                f"| {number_range(scores, 'median_wall_time_s', 3)} "
                f"| {number_range(scores, 'median_tokens')} | {cost} |"
            )
    lines.extend(
        [
            "",
            "A detection requires the labelled file and a line inside its labelled range; rule-id agreement is ",
            "scored separately. For Cerno, only `shown: true` findings are scored, and suppression losses count ",
            "otherwise-missed violation cases with a correctly located `shown: false` finding.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    try:
        print(render_report(load_runs(args.results)), end="")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()

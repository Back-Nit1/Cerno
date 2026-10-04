"""Run diff-only, rules-assisted, or Cerno review benchmark arms."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cerno.config import Settings
from cerno.llm.openai import LLM, call_arguments, function_calls
from evals.common import (
    BUILTIN_RULES,
    CASES_DIR,
    ROOT,
    Case,
    cases_commit,
    cases_digest,
    init_review_repo,
    load_cases,
    unified_diff,
)

FINDINGS_TOOL = {
    "type": "function",
    "name": "report_review_findings",
    "description": "Report only evidence-backed production-risk findings on changed lines.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string"},
                        "path": {"type": "string"},
                        "line": {"type": "integer", "minimum": 1},
                        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    },
                    "required": ["rule_id", "path", "line", "severity"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    },
}


def rule_texts(case: Case) -> dict[str, str]:
    rules = dict(BUILTIN_RULES)
    rules_path = case.root / "rules.toml"
    if rules_path.is_file():
        parsed = tomllib.loads(rules_path.read_text())
        for rule in parsed.get("rule", []):
            rules[rule["id"]] = rule["text"]
    return rules


def baseline_prompt(case: Case, arm: str) -> str:
    rules = ""
    if arm == "rules":
        rendered = "\n".join(f"- {rule_id}: {text}" for rule_id, text in rule_texts(case).items())
        rules = f"\nApply these review rules:\n{rendered}\n"
    return (
        "Review this unified diff for production-impacting defects only. Report broken callers or API "
        "contracts, weakened authorization or validation, data exposure, destructive data changes, and "
        "unintended behavior changes. Suppress style, maintainability, and speculative concerns. Every "
        "finding must identify a changed line and severity. Call report_review_findings once, using an "
        f"empty findings array when no supported defect is present.{rules}\nUnified diff:\n{unified_diff(case)}"
    )


def serialize_response(response: Any) -> Any:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=False)
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    return repr(response)


def validate_findings(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        raise ValueError("model output findings must be a list")
    result = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("each finding must be an object")
        required = {"rule_id", "path", "line", "severity"}
        if set(finding) != required:
            raise ValueError(f"finding keys must be exactly {sorted(required)}")
        if not isinstance(finding["line"], int) or finding["line"] < 1:
            raise ValueError("finding line must be a positive integer")
        if finding["severity"] not in {"critical", "high", "medium", "low"}:
            raise ValueError("finding severity is invalid")
        result.append(finding)
    return result


def run_baseline_case(case: Case, arm: str, llm: LLM) -> dict[str, Any]:
    before = (llm.input_tokens, llm.output_tokens, llm.cache_read_tokens)
    started = time.monotonic()
    response = llm.create(
        f"review_eval_{arm}",
        input=[{"role": "user", "content": baseline_prompt(case, arm)}],
        tools=[FINDINGS_TOOL],
        tool_choice={"type": "function", "name": FINDINGS_TOOL["name"]},
    )
    elapsed = time.monotonic() - started
    calls = function_calls(response)
    if len(calls) != 1:
        raise ValueError(f"expected one report_review_findings call, got {len(calls)}")
    findings = validate_findings(call_arguments(calls[0]).get("findings"))
    after = (llm.input_tokens, llm.output_tokens, llm.cache_read_tokens)
    return {
        "findings": findings,
        "insufficient": [],
        "wall_time_s": elapsed,
        "input_tokens": after[0] - before[0],
        "output_tokens": after[1] - before[1],
        "cache_read_tokens": after[2] - before[2],
        "cost_usd": None,
        "raw_output": serialize_response(response),
    }


def run_cerno_case(case: Case, cerno_cmd: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"cerno-review-{case.case_id}-") as temp:
        repository = Path(temp) / "repo"
        base, head = init_review_repo(case, repository)
        argv = [
            *shlex.split(cerno_cmd),
            "review",
            "--repo",
            str(repository),
            "--base",
            base,
            "--head",
            head,
            "--format",
            "json",
        ]
        rules_path = case.root / "rules.toml"
        if rules_path.is_file():
            argv.extend(["--rules", str(rules_path)])
        started = time.monotonic()
        completed = subprocess.run(argv, cwd=repository, text=True, capture_output=True)
        elapsed = time.monotonic() - started
        if completed.returncode not in {0, 1}:
            raise RuntimeError(
                f"cerno review failed for {case.case_id} ({completed.returncode}):\n{completed.stderr}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"cerno review emitted invalid JSON for {case.case_id}: {exc}") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("findings"), list):
        raise ValueError(f"cerno review emitted an invalid result contract for {case.case_id}")
    usage = payload.get("usage") or {}
    return {
        "findings": payload["findings"],
        "insufficient": payload.get("insufficient", []),
        "wall_time_s": elapsed,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_tokens", 0),
        "cost_usd": usage.get("cost_usd"),
        "raw_output": payload,
    }


def run_benchmark(
    arm: str,
    split: str,
    runs: int,
    *,
    out: Path,
    cerno_cmd: str = "cerno",
    done_tuning: bool = False,
    cases_dir: Path = CASES_DIR,
    llm: LLM | None = None,
) -> Path:
    if split == "heldout" and not done_tuning:
        raise ValueError("refusing heldout run without --i-am-done-tuning")
    if runs < 1:
        raise ValueError("--runs must be at least 1")
    cases = load_cases(split, cases_dir)
    if not cases:
        raise ValueError(f"no {split} cases found in {cases_dir}")
    settings = Settings.load(ROOT)
    if arm in {"diff", "rules"}:
        llm = llm or LLM(settings)
        if not llm.available:
            raise RuntimeError("OPENAI_API_KEY is required for diff and rules arms")
    records = []
    for run_number in range(1, runs + 1):
        for case in cases:
            measured = (
                run_cerno_case(case, cerno_cmd)
                if arm == "cerno"
                else run_baseline_case(case, arm, llm)
            )
            records.append(
                {
                    "case_id": case.case_id,
                    "run": run_number,
                    "label": case.label,
                    **measured,
                }
            )
            print(f"completed {arm} {split} run={run_number} case={case.case_id}")
    result = {
        "schema_version": 1,
        "arm": arm,
        "split": split,
        "runs": runs,
        "model": settings.model,
        "cases_commit": cases_commit(ROOT),
        "cases_digest": cases_digest(cases_dir),
        "created_at": datetime.now(UTC).isoformat(),
        "results": records,
    }
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = out / f"{arm}-{split}-{stamp}.json"
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("diff", "rules", "cerno"), required=True)
    parser.add_argument("--split", choices=("dev", "heldout"), required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "results")
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--cerno-cmd", default="cerno")
    parser.add_argument("--i-am-done-tuning", action="store_true")
    args = parser.parse_args()
    try:
        output = run_benchmark(
            args.arm,
            args.split,
            args.runs,
            out=args.out,
            cerno_cmd=args.cerno_cmd,
            done_tuning=args.i_am_done_tuning,
            cases_dir=args.cases,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(output)


if __name__ == "__main__":
    main()

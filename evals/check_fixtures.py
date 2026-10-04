"""Validate labels and executable ground truth for every benchmark case."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from evals.common import CASES_DIR, Case, changed_lines, load_cases, materialize, project_test_command, run

EXPECTED_COUNTS = {"correct": 8, "violation": 10, "insufficient": 6}
EXPECTED_SPLITS = {"dev": 16, "heldout": 8}


def validate_label(case: Case) -> list[str]:
    errors = []
    label = case.label
    required = {"split", "kind", "language", "needs_context", "expected"}
    if set(label) != required:
        errors.append(f"label keys must be exactly {sorted(required)}")
    if label.get("split") not in EXPECTED_SPLITS:
        errors.append("split must be dev or heldout")
    if label.get("kind") not in EXPECTED_COUNTS:
        errors.append("kind must be correct, violation or insufficient")
    if label.get("language") not in {"js", "python"}:
        errors.append("language must be js or python")
    if not isinstance(label.get("needs_context"), bool):
        errors.append("needs_context must be a boolean")
    expected = label.get("expected")
    if not isinstance(expected, list):
        errors.append("expected must be a list")
        return errors
    if label.get("kind") != "violation" and expected:
        errors.append("only violation cases may have expected findings")
    if label.get("kind") == "violation" and not expected:
        errors.append("violation cases need at least one expected finding")
    modifications = changed_lines(case)
    for index, finding in enumerate(expected):
        needed = {"rule_id", "path", "line_start", "line_end", "why"}
        if set(finding) != needed:
            errors.append(f"expected[{index}] keys must be exactly {sorted(needed)}")
            continue
        invalid_strings = []
        for field in ("rule_id", "path", "why"):
            if not isinstance(finding[field], str) or not finding[field].strip():
                errors.append(f"expected[{index}].{field} must be a non-empty string")
                invalid_strings.append(field)
        if invalid_strings:
            continue
        start, end = finding["line_start"], finding["line_end"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            errors.append(f"expected[{index}] has an invalid line range")
            continue
        changed = modifications.get(finding["path"], set())
        if finding["path"] not in modifications:
            errors.append(f"expected[{index}] path is not present in change/")
        if not set(range(start, end + 1)).issubset(changed):
            errors.append(f"expected[{index}] range is not wholly inside changed lines")
    return errors


def check_case(case: Case, *, verbose: bool = False) -> list[str]:
    errors = validate_label(case)
    for directory in ("base", "change"):
        if not (case.root / directory).is_dir():
            errors.append(f"missing {directory}/")
    if errors:
        return errors
    with tempfile.TemporaryDirectory(prefix=f"cerno-eval-{case.case_id}-") as temp:
        temp_root = Path(temp)
        for variant, changed in (("base", False), ("change", True)):
            root = temp_root / variant
            materialize(case, root, changed=changed)
            result = run(project_test_command(root), root)
            if result.returncode:
                errors.append(
                    f"{variant} project tests failed ({result.returncode})\n{result.stdout}{result.stderr}".rstrip()
                )
        if case.label["kind"] == "violation":
            for variant, changed, expected_code in (("corrected", False, 0), ("defective", True, 1)):
                root = temp_root / variant
                materialize(case, root, changed=changed, regression=True)
                result = run(project_test_command(root), root)
                if (result.returncode == 0) != (expected_code == 0):
                    errors.append(
                        f"{variant} regression expected {'pass' if expected_code == 0 else 'failure'}; "
                        f"got {result.returncode}\n{result.stdout}{result.stderr}".rstrip()
                    )
    if verbose and not errors:
        print(f"ok {case.case_id}")
    return errors


def check_all(cases_dir: Path = CASES_DIR, *, verbose: bool = False) -> list[str]:
    cases = load_cases(cases_dir=cases_dir)
    errors = []
    if len(cases) != 24:
        errors.append(f"benchmark must contain 24 cases, found {len(cases)}")
    kinds = Counter(case.label.get("kind") for case in cases)
    splits = Counter(case.label.get("split") for case in cases)
    if dict(kinds) != EXPECTED_COUNTS:
        errors.append(f"kind counts must be {EXPECTED_COUNTS}, found {dict(kinds)}")
    if dict(splits) != EXPECTED_SPLITS:
        errors.append(f"split counts must be {EXPECTED_SPLITS}, found {dict(splits)}")
    contextual = sum(case.label.get("kind") == "violation" and case.label.get("needs_context") for case in cases)
    if contextual < 5:
        errors.append(f"at least 5 violations must need context, found {contextual}")
    languages = {case.label.get("language") for case in cases}
    if languages != {"js", "python"}:
        errors.append(f"benchmark must mix js and python, found {languages}")
    for case in cases:
        errors.extend(f"{case.case_id}: {error}" for error in check_case(case, verbose=verbose))
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    errors = check_all(args.cases, verbose=not args.quiet)
    if errors:
        raise SystemExit("fixture validation failed:\n" + "\n".join(f"- {error}" for error in errors))
    if not args.quiet:
        summary = {case.label["kind"]: 0 for case in load_cases(cases_dir=args.cases)}
        for case in load_cases(cases_dir=args.cases):
            summary[case.label["kind"]] += 1
        print(f"validated {sum(summary.values())} cases: {json.dumps(summary, sort_keys=True)}")


if __name__ == "__main__":
    main()

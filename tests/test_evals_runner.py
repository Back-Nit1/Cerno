import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.common import load_cases  # noqa: E402
from evals.run import baseline_prompt, run_benchmark, run_cerno_case  # noqa: E402


class StubLLM:
    available = True

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0

    def create(self, purpose, **kwargs):
        assert purpose.startswith("review_eval_")
        assert kwargs["tools"][0]["strict"] is True
        self.input_tokens += 11
        self.output_tokens += 7
        call = SimpleNamespace(
            type="function_call",
            arguments=json.dumps(
                {
                    "findings": [
                        {
                            "rule_id": "CERNO-AUTHZ",
                            "path": "session.py",
                            "line": 2,
                            "severity": "high",
                        }
                    ]
                }
            ),
        )
        return SimpleNamespace(output=[call])


def test_rules_prompt_adds_rules_but_diff_prompt_does_not():
    case = next(case for case in load_cases() if case.case_id == "v11_pagination_duplicates")
    assert "PAGE-001" not in baseline_prompt(case, "diff")
    assert "PAGE-001" in baseline_prompt(case, "rules")


def test_runner_records_raw_baseline_output(tmp_path):
    output = run_benchmark(
        "diff",
        "dev",
        1,
        out=tmp_path,
        llm=StubLLM(),
    )
    payload = json.loads(output.read_text())
    assert payload["arm"] == "diff"
    assert len(payload["results"]) == 16
    assert payload["results"][0]["raw_output"]
    assert payload["results"][0]["input_tokens"] == 11


def test_heldout_requires_explicit_tuning_completion(tmp_path):
    with pytest.raises(ValueError, match="i-am-done-tuning"):
        run_benchmark("cerno", "heldout", 1, out=tmp_path)


def test_cerno_arm_accepts_contract_valid_stub(tmp_path):
    stub = tmp_path / "stub_cerno.py"
    payload = {
        "schema_version": 1,
        "base": {"ref": "base", "sha": "a", "tree": "b"},
        "head": {"ref": "head", "sha": "c", "tree": "d"},
        "merge_base": "a",
        "model": None,
        "rules_source": ["builtin"],
        "findings": [
            {
                "id": "123456789abc",
                "rule_id": "CERNO-AUTHZ",
                "title": "Expired sessions returned",
                "severity": "high",
                "status": "confirmed",
                "shown": True,
                "suppressed_reason": None,
                "path": "session.py",
                "line": 2,
                "end_line": 2,
                "explanation": "The expiry filter was removed.",
                "next_action": "Restore the filter.",
                "evidence": [
                    {
                        "id": "E-test",
                        "type": "check_test",
                        "level": "test",
                        "path": "tests/test_session.py",
                        "line_start": 1,
                        "summary": "Regression test failed.",
                    }
                ],
            }
        ],
        "insufficient": [],
        "rules": [],
        "checks": [],
        "usage": {
            "wall_time_s": 0.01,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": None,
        },
        "errors": [],
    }
    stub.write_text("import json\nprint(json.dumps(" + repr(payload) + "))\n")
    case = next(case for case in load_cases() if case.case_id == "v09_expiry_filter")
    result = run_cerno_case(case, f"{sys.executable} {stub}")
    assert result["findings"][0]["shown"] is True
    assert result["raw_output"]["schema_version"] == 1

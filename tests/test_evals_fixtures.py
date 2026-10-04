import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.check_fixtures import check_all  # noqa: E402


def test_all_evaluation_fixtures_have_executable_ground_truth():
    assert check_all(verbose=True) == []

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.random_rotate_bet_svc import _parse_random_counts, _random_targets_for_issue


def test_random_counts_are_bounded_per_path():
    assert _parse_random_counts([0, 5, 12]) == [1, 5, 9]
    assert _parse_random_counts([]) == [5, 5, 5]


def test_same_issue_returns_the_same_numbers_for_all_accounts():
    first = _random_targets_for_issue("3476801", [4, 5, 6])
    second = _random_targets_for_issue("3476801", [4, 5, 6])
    assert first == second
    assert [len(values) for values in first] == [4, 5, 6]
    for values in first:
        assert values == sorted(values)
        assert len(values) == len(set(values))
        assert all(0 <= value <= 9 for value in values)

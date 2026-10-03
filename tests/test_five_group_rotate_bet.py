import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.five_group_rotate_bet_svc import GROUPS, _CustomAmountPathState, _group_hits


def test_groups_cover_the_expected_pairs():
    assert GROUPS == {
        "A": [0, 5],
        "B": [1, 6],
        "C": [2, 7],
        "D": [3, 8],
        "E": [4, 9],
    }


def test_any_of_three_balls_hitting_a_group_counts_as_a_group_win():
    hits = _group_hits([0, 1, 2])
    assert hits == {"A": True, "B": True, "C": True, "D": False, "E": False}

    hits = _group_hits([9, 9, 9])
    assert hits == {"A": False, "B": False, "C": False, "D": False, "E": True}


def test_each_group_has_an_independent_ladder_and_last_tier_resets():
    paths = {label: _CustomAmountPathState([10, 20], 0) for label in GROUPS}
    hits = _group_hits([0, 1, 2])
    for label, hit in hits.items():
        if hit:
            paths[label].on_win()
        else:
            paths[label].on_lose()

    assert paths["A"].get_bet() == 10
    assert paths["B"].get_bet() == 10
    assert paths["C"].get_bet() == 10
    assert paths["D"].get_bet() == 20
    assert paths["E"].get_bet() == 20

    assert paths["D"].on_lose() is True
    assert paths["D"].get_bet() == 10


def test_group_total_is_six_times_the_per_number_amount():
    amount = 20
    assert len(GROUPS["A"]) * 3 * amount == 120
    assert len(GROUPS) * len(GROUPS["A"]) * 3 * amount == 600
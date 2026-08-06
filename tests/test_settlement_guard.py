"""Settlement guard and rotate chase multiplier tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.settlement_guard import (
    issue_gap,
    issue_num,
    is_newer_issue,
    read_stable_draw,
)
from services.rotate_bet_svc import _PathState


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print(f"  OK {msg}")


class SequenceReader:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self, page):
        if not self.values:
            return None
        return self.values.pop(0)


def test_issue_compare():
    print("[1] issue compare")
    check(issue_num("issue 3451376") == 3451376, "extract issue number")
    check(is_newer_issue("3451377", "3451376") is True, "newer issue accepted")
    check(is_newer_issue("3451376", "3451376") is False, "same issue rejected")
    check(is_newer_issue("3451375", "3451376") is False, "older issue rejected")
    check(issue_gap("3451378", "3451376") == 2, "issue gap calculated")
    check(issue_num("no issue") is None, "bad issue returns None")


def test_stable_draw_reader():
    print("[2] stable draw reader")
    stable = read_stable_draw(None, SequenceReader([
        ("3451376", [1, 2, 3]),
        ("3451376", [1, 2, 3]),
    ]), delay=0)
    check(stable == ("3451376", [1, 2, 3]), "same issue and balls accepted")

    changed_issue = read_stable_draw(None, SequenceReader([
        ("3451376", [1, 2, 3]),
        ("3451377", [1, 2, 3]),
    ]), delay=0)
    check(changed_issue is None, "changing issue rejected")

    changed_balls = read_stable_draw(None, SequenceReader([
        ("3451376", [1, 2, 3]),
        ("3451376", [1, 2, 4]),
    ]), delay=0)
    check(changed_balls is None, "changing balls rejected")

    bad = read_stable_draw(None, SequenceReader([("3451376", [1, 2])]), delay=0)
    check(bad is None, "incomplete draw rejected")


def test_rotate_chase_amounts():
    print("[3] rotate chase amounts")
    path = _PathState(base=100, multiplier=1.3, max_losses=3)
    check(path.get_bet() == 100, "initial bet is base")
    reset = path.on_lose(100)
    check(reset is False and path.get_bet() == 130, "first miss -> sum losses * multiplier")
    reset = path.on_lose(130)
    check(reset is False and path.get_bet() == 299, "second miss -> accumulated losses * multiplier")
    reset = path.on_lose(299)
    check(reset is True and path.get_bet() == 100, "max misses reset to base")

    path.on_lose(100)
    path.on_win()
    check(path.loss_count == 0 and path.loss_history == [] and path.get_bet() == 100,
          "hit resets chase state")


def main():
    print("=" * 56)
    print("settlement guard tests")
    print("=" * 56)
    for fn in [test_issue_compare, test_stable_draw_reader, test_rotate_chase_amounts]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()

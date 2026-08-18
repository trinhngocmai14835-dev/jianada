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
from services.rotate_bet_svc import _PathState, _observe_entry_draw, _parse_enabled_positions


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


def test_rotate_entry_trigger_state():
    print("[4] rotate entry trigger state")
    path = _PathState(base=100, multiplier=1.3, max_losses=5, entry_miss_trigger=2)
    check(path.active is False, "entry trigger starts inactive")
    check(path.observe_entry(False) is False and path.entry_loss_count == 1 and not path.active,
          "first observed miss waits")
    check(path.observe_entry(True) is False and path.entry_loss_count == 0 and not path.active,
          "observed hit resets entry count")
    path.observe_entry(False)
    check(path.observe_entry(False) is True and path.active,
          "second consecutive observed miss activates path")

    immediate = _PathState(base=100, multiplier=1.3, max_losses=5, entry_miss_trigger=0)
    check(immediate.active is True, "zero trigger keeps immediate betting compatibility")


def test_rotate_entry_observation_per_path():
    print("[5] rotate entry observation per path")
    number_sets = [([0, 1, 3, 5, 8], [2, 4, 6, 7, 9]) for _ in range(3)]
    paths = [_PathState(base=100, multiplier=1.3, max_losses=5, entry_miss_trigger=2) for _ in range(3)]
    logs = []

    activated = _observe_entry_draw(paths, number_sets, [2, 2, 0], "acct", logs.append, rotate_after=True)
    check(activated == [], "first observation activates no path")
    check([p.entry_loss_count for p in paths] == [1, 1, 0], "entry counts are independent")
    check([p.set_idx for p in paths] == [1, 1, 1], "observation rotates all inactive paths")

    activated = _observe_entry_draw(paths, number_sets, [0, 2, 0], "acct", logs.append, rotate_after=True)
    check(activated == [0], "only first path activates after second miss")
    check([p.active for p in paths] == [True, False, False], "only triggered path becomes active")
    check(paths[1].entry_loss_count == 0 and paths[2].entry_loss_count == 1,
          "other paths keep independent observation state")

def test_rotate_disabled_position_skips_observation():
    print("[6] rotate disabled position skips observation")
    number_sets = [([0, 1, 3, 5, 8], [2, 4, 6, 7, 9]) for _ in range(3)]
    paths = [_PathState(base=100, multiplier=1.3, max_losses=5, entry_miss_trigger=1) for _ in range(3)]
    logs = []

    enabled = _parse_enabled_positions([True, False, True])
    activated = _observe_entry_draw(
        paths,
        number_sets,
        [2, 2, 2],
        "acct",
        logs.append,
        rotate_after=True,
        enabled_positions=enabled,
    )

    check(enabled == [True, False, True], "enabled positions parser keeps selected paths")
    check(activated == [0, 2], "disabled middle path does not activate")
    check(paths[1].active is False and paths[1].entry_loss_count == 0,
          "disabled path keeps inactive observation state")
    check([p.set_idx for p in paths] == [1, 0, 1], "only enabled paths rotate during observation")

def main():
    print("=" * 56)
    print("settlement guard tests")
    print("=" * 56)
    for fn in [test_issue_compare, test_stable_draw_reader, test_rotate_chase_amounts, test_rotate_entry_trigger_state, test_rotate_entry_observation_per_path, test_rotate_disabled_position_skips_observation]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()

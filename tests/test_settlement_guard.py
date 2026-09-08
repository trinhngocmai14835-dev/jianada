"""Settlement guard and rotate chase multiplier tests."""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.settlement_guard import (
    issue_gap,
    issue_num,
    is_newer_issue,
    read_stable_draw,
)
from services.rotate_bet_svc import _PathState, _observe_entry_draw, _parse_enabled_positions
import services.custom_rotate_bet_svc as custom_rotate_bet_svc
from services.custom_rotate_bet_svc import (
    _CustomAmountPathState,
    _observe_entry_draw as _custom_observe_entry_draw,
    _parse_amount_steps,
    _parse_custom_number_sets,
    _ACCOUNT_STATUS,
    _ACCOUNT_STOPS,
    _MANUALLY_STOPPED,
    _STATUS_LOCK,
    _finalize_account,
    stop_account,
)


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

    path.active = True
    path.on_lose(100)
    path.on_win()
    check(path.loss_count == 0 and path.loss_history == [] and path.get_bet() == 100,
          "hit resets chase state")
    check(path.active is False and path.entry_loss_count == 0,
          "hit returns non-zero trigger path to observation")

    immediate = _PathState(base=100, multiplier=1.3, max_losses=3, entry_miss_trigger=0)
    immediate.on_lose(100)
    immediate.on_win()
    check(immediate.active is True and immediate.get_bet() == 100,
          "hit keeps zero-trigger path in direct betting mode")



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



def test_custom_rotate_amount_state_and_numbers():
    print("[7] custom rotate amount state and number parser")
    check(_parse_amount_steps([10, "20", 0, "bad", 30], 100) == [10, 20, 30], "custom amount parser keeps positive steps")
    check(_parse_amount_steps([], 88) == [88], "custom amount parser falls back to base")

    path = _CustomAmountPathState([10, 20], entry_miss_trigger=0)
    check(path.active is True and path.get_bet() == 10, "direct custom path starts active at first tier")
    check(path.on_lose() is False and path.get_bet() == 20, "custom miss advances to next tier")
    check(path.on_lose() is True and path.get_bet() == 10, "last custom tier miss resets to first tier")
    path.on_win()
    check(path.active is True and path.get_bet() == 10, "direct custom win keeps active and resets tier")

    valid_sets = [
        {"set_a": [0, 1, 2, 3], "set_b": [4, 5, 6, 7, 8]},
        {"set_a": [0, 1, 2, 3, 4], "set_b": [5, 6, 7, 8]},
        {"set_a": "0,1,2,3", "set_b": "4,5,6,7,8"},
    ]
    parsed = _parse_custom_number_sets(valid_sets)
    check(parsed[0][0] == [0, 1, 2, 3] and parsed[0][1] == [4, 5, 6, 7, 8], "custom number parser accepts 4 or 5 numbers")

    invalid_sets = [
        {"set_a": [0, 1, 2], "set_b": [4, 5, 6, 7]},
        {"set_a": [0, 1, 2, 3], "set_b": [4, 5, 6, 7]},
        {"set_a": [0, 1, 2, 3], "set_b": [4, 5, 6, 7]},
    ]
    check(_parse_custom_number_sets(invalid_sets) is None, "custom number parser rejects non 4/5 groups")



def test_custom_rotate_entry_observation_per_path():
    print("[8] custom rotate entry observation per path")
    number_sets = [([0, 1, 3, 5], [2, 4, 6, 7, 9]) for _ in range(3)]
    paths = [_CustomAmountPathState([10, 20, 30], entry_miss_trigger=2) for _ in range(3)]
    logs = []

    activated = _custom_observe_entry_draw(paths, number_sets, [2, 2, 0], "acct", logs.append, rotate_after=True)
    check(activated == [], "custom first observation activates no path")
    check([p.entry_loss_count for p in paths] == [1, 1, 0], "custom entry counts are independent")
    check([p.set_idx for p in paths] == [1, 1, 1], "custom observation rotates enabled inactive paths")

    activated = _custom_observe_entry_draw(paths, number_sets, [0, 2, 0], "acct", logs.append, rotate_after=True)
    check(activated == [0], "custom only one path activates after second miss")
    check([p.active for p in paths] == [True, False, False], "custom path activation is independent")



def test_custom_rotate_single_account_stop():
    print("[9] custom rotate single account stop")
    key = "acct@9222"
    child_stop = threading.Event()
    with _STATUS_LOCK:
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()
        _ACCOUNT_STATUS[key] = {"key": key, "account": "acct", "port": 9222, "status": "running"}
        _ACCOUNT_STOPS[key] = child_stop

    ok, msg = stop_account("acct")
    check(ok is True, "single account stop returns ok")
    check(child_stop.is_set(), "single account stop sets child event")
    with _STATUS_LOCK:
        check(_ACCOUNT_STATUS[key]["status"] == "stopping", "single account status becomes stopping")
        check(key in _MANUALLY_STOPPED, "single account is marked manual stopped")
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()


def test_custom_rotate_account_finalize_marks_exit_as_manual_stop():
    print("[10] custom rotate account finalize")
    key = "acct@9222"
    child_stop = threading.Event()
    parent_stop = threading.Event()
    with _STATUS_LOCK:
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()
        _ACCOUNT_STATUS[key] = {"key": key, "account": "acct", "port": 9222, "status": "running"}
        _ACCOUNT_STOPS[key] = child_stop

    _finalize_account(key, parent_stop)

    with _STATUS_LOCK:
        check(key in _MANUALLY_STOPPED, "custom natural account exit should not auto restart")
        check(key not in _ACCOUNT_STOPS, "custom finalize removes child stop handle")
        check(_ACCOUNT_STATUS[key]["status"] == "stopped", "custom normal exit status becomes stopped")
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()

    parent_stop.set()
    with _STATUS_LOCK:
        _ACCOUNT_STATUS[key] = {"key": key, "account": "acct", "port": 9222, "status": "running"}
        _ACCOUNT_STOPS[key] = threading.Event()

    _finalize_account(key, parent_stop)

    with _STATUS_LOCK:
        check(key not in _MANUALLY_STOPPED, "custom global stop should not mark manual stop")
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()


def test_custom_rotate_new_account_port_guard():
    print("[10] custom rotate new account occupied port guard")
    calls = []
    original_port_in_use = custom_rotate_bet_svc._port_in_use
    original_free_port = custom_rotate_bet_svc._free_port
    try:
        custom_rotate_bet_svc._port_in_use = lambda port: True
        custom_rotate_bet_svc._free_port = lambda port, log=None: calls.append(port)
        try:
            custom_rotate_bet_svc._prepare_launch_port(9555, True, lambda _msg: None)
            check(False, "new account occupied port raises")
        except RuntimeError as exc:
            check("9555" in str(exc) and "已被占用" in str(exc), "new account reports occupied port")
        check(calls == [], "new account occupied port does not clear the port")

        custom_rotate_bet_svc._prepare_launch_port(9555, False, lambda _msg: None)
        check(calls == [9555], "initial account still clears stale occupied port")
    finally:
        custom_rotate_bet_svc._port_in_use = original_port_in_use
        custom_rotate_bet_svc._free_port = original_free_port

def main():
    print("=" * 56)
    print("settlement guard tests")
    print("=" * 56)
    for fn in [test_issue_compare, test_stable_draw_reader, test_rotate_chase_amounts, test_rotate_entry_trigger_state, test_rotate_entry_observation_per_path, test_rotate_disabled_position_skips_observation, test_custom_rotate_amount_state_and_numbers, test_custom_rotate_entry_observation_per_path, test_custom_rotate_single_account_stop, test_custom_rotate_account_finalize_marks_exit_as_manual_stop, test_custom_rotate_new_account_port_guard]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()

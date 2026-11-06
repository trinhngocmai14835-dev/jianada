"""Unit checks for 1106 private nine-number selection."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from api.ws import _TASK_IDS
from api.routes import DEFAULT_NINE_CODE_PICKBET
from services.nine_code_pick_bet_svc import OFFSET_CYCLE, next_offset, numbers_for_draw


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_one_to_nine_rule_and_wraparound():
    excluded, targets = numbers_for_draw([9, 8, 0], 1)
    check(excluded == [0, 9, 1], "+1 must wrap 9 to 0")
    check(targets[0] == [1, 2, 3, 4, 5, 6, 7, 8, 9], "ball 1 must bet the remaining nine")
    check(targets[1] == [0, 1, 2, 3, 4, 5, 6, 7, 8], "ball 2 must exclude 9")
    check(targets[2] == [0, 2, 3, 4, 5, 6, 7, 8, 9], "ball 3 must exclude 1")


def test_private_offset_cycle_is_exact():
    excluded, targets = numbers_for_draw([9, 8, 0], 9)
    check(excluded == [8, 7, 9], "+9 must use modulo ten")
    check(all(len(numbers) == 9 for numbers in targets), "every active ball must contain exactly nine numbers")
    check(OFFSET_CYCLE == (1, 2, 5, 6, 9), "private offsets must match the configured rule")
    check([next_offset(value) for value in OFFSET_CYCLE] == [2, 5, 6, 9, 1], "offsets must rotate and wrap after +9")


def test_disabled_position_has_no_bet_numbers():
    excluded, targets = numbers_for_draw([1, 2, 3], 2, [True, False, True])
    check(excluded == [3, 4, 5], "disabled position still has deterministic exclusion for logs")
    check(targets[1] == [], "disabled ball must place no bet")


def test_strategy_is_registered_for_live_logs():
    check("nine_code_pickbet" in _TASK_IDS, "strategy must be registered with live log relay")


def test_default_has_fixed_amount_and_no_chasing_ladder():
    check(DEFAULT_NINE_CODE_PICKBET["base_bet_amount"] > 0, "fixed per-number amount is required")
    check("amount_steps" not in DEFAULT_NINE_CODE_PICKBET, "nine-code module must not inherit a loss ladder")
    check("loss_multiplier" not in DEFAULT_NINE_CODE_PICKBET, "nine-code module must not inherit chasing")


if __name__ == "__main__":
    test_one_to_nine_rule_and_wraparound()
    test_private_offset_cycle_is_exact()
    test_disabled_position_has_no_bet_numbers()
    test_strategy_is_registered_for_live_logs()
    test_default_has_fixed_amount_and_no_chasing_ladder()
    print("nine code pick tests passed")

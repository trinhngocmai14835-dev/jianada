"""Unit checks for the two-colour history target rule."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.color_rotate_bet_svc import _ColourHistory, _CustomAmountPathState


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_skips_repeated_colour_and_adds_zero():
    history = _ColourHistory()
    # add() receives newest first in live use; construct the same sequence:
    # latest red (9), earlier red (3), then blue (2).
    for number in (2, 3, 9):
        history.add(number)
    check(history.pair() == ("red", "blue"), "must skip repeated red")
    check(history.numbers() == [0, 3, 6, 9, 2, 5, 8], "target must be 0 + red + blue")


def test_waits_until_two_distinct_colours():
    history = _ColourHistory()
    history.add(3)
    history.add(9)
    check(history.numbers() == [], "one colour only must not bet")


def test_each_ball_tier_resets_after_win_or_final_loss():
    path = _CustomAmountPathState([99, 232], 0)
    check(path.get_bet() == 99, "first tier")
    check(path.on_lose() is False and path.get_bet() == 232, "first loss advances")
    check(path.on_lose() is True and path.get_bet() == 99, "final loss wraps")
    path.on_lose()
    path.on_win()
    check(path.get_bet() == 99, "win resets to first tier")


if __name__ == "__main__":
    for fn in (test_skips_repeated_colour_and_adds_zero, test_waits_until_two_distinct_colours, test_each_ball_tier_resets_after_win_or_final_loss):
        fn()
    print("color rotate tests passed")

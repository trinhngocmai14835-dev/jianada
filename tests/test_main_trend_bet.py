import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.main_trend_bet_svc import (  # noqa: E402
    TARGET_INPUT_IDS,
    _CustomAmountPathState,
    _main_trend_result,
    _observe_entry_draw,
    _parse_enabled_paths,
    _settlement_odds,
    _target_hit,
)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_main_trend_edges():
    r13 = _main_trend_result([4, 4, 5])
    check(r13["total"] == 13, "13 total should be parsed")
    check(r13["labels"] == ["小", "单"], "13 should be small and odd")
    check(r13["special"] is True, "13 should be special odds")
    check(_target_hit([4, 4, 5], "小"), "13 should hit small")
    check(_target_hit([4, 4, 5], "单"), "13 should hit odd")
    check(not _target_hit([4, 4, 5], "大"), "13 should not hit big")
    check(not _target_hit([4, 4, 5], "双"), "13 should not hit even")
    check(_settlement_odds([4, 4, 5], "小", 2.05) == 1.6, "13 small hit should settle at 1.6")
    check(_settlement_odds([4, 4, 5], "单", 2.05) == 1.6, "13 odd hit should settle at 1.6")

    r14 = _main_trend_result([4, 5, 5])
    check(r14["total"] == 14, "14 total should be parsed")
    check(r14["labels"] == ["大", "双"], "14 should be big and even")
    check(r14["special"] is True, "14 should be special odds")
    check(_target_hit([4, 5, 5], "大"), "14 should hit big")
    check(_target_hit([4, 5, 5], "双"), "14 should hit even")
    check(_settlement_odds([4, 5, 5], "大", 2.05) == 1.6, "14 big hit should settle at 1.6")
    check(_settlement_odds([4, 5, 5], "双", 2.05) == 1.6, "14 even hit should settle at 1.6")

    check(_settlement_odds([7, 7, 7], "大", 2.05) == 2.05, "normal hit should keep page odds")
    check(_settlement_odds([7, 7, 7], "小", 2.05) == 2.05, "non-hit leaves fallback unchanged")


def test_independent_paths_and_entry_rotation():
    paths = [_CustomAmountPathState([10, 20, 30], entry_miss_trigger=2) for _ in range(2)]
    logs = []

    activated = _observe_entry_draw(paths, [4, 4, 5], "acct", logs.append, rotate_after=True)
    check(activated == [], "first miss should not activate at trigger 2")
    check(paths[0].entry_loss_count == 1, "大小路 first target 大 should miss on 13")
    check(paths[1].entry_loss_count == 0, "单双路 first target 单 should hit on 13")
    check([p.set_idx for p in paths] == [1, 1], "both paths rotate after observation")

    activated = _observe_entry_draw(paths, [5, 5, 5], "acct", logs.append, rotate_after=True)
    check(activated == [0], "大小路 second consecutive miss should activate independently")
    check(paths[0].active is True, "大小路 should be active")
    check(paths[1].active is False, "单双路 should still be observing")
    check(paths[1].entry_loss_count == 1, "单双路 miss count should be independent")


def test_amount_steps_and_reset():
    path = _CustomAmountPathState([10, 20], entry_miss_trigger=0)
    check(path.active is True, "direct start should be active")
    check(path.get_bet() == 10, "first tier amount")
    check(path.on_lose() is False, "first loss moves to second tier")
    check(path.get_bet() == 20, "second tier amount")
    check(path.on_lose() is True, "last tier loss resets")
    check(path.get_bet() == 10, "last tier reset to first tier")
    path.on_win()
    check(path.get_bet() == 10, "win resets to first tier")


def test_enabled_paths_and_dom_ids():
    check(_parse_enabled_paths(None) == [True, True], "default enables both paths")
    check(_parse_enabled_paths([True, False]) == [True, False], "explicit disabled path should be preserved")
    check(_parse_enabled_paths([False]) == [False, True], "missing second path defaults to enabled")
    check(TARGET_INPUT_IDS == {"大": "odds_DX1", "小": "odds_DX2", "单": "odds_DS3", "双": "odds_DS4"}, "main trend input ids")


def main():
    test_main_trend_edges()
    test_independent_paths_and_entry_rotation()
    test_amount_steps_and_reset()
    test_enabled_paths_and_dom_ids()
    print("test_main_trend_bet: OK")


if __name__ == "__main__":
    main()

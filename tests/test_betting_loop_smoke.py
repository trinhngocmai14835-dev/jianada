"""Smoke tests for betting loops with fake page hooks.

These tests run the real _betting_loop functions without opening a browser or
placing real bets. Page reads, countdowns, draw rows, and place_bet are mocked.
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import services.rotate_bet_svc as rotate
import services.rush_bet_svc as rush


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  OK " + msg)


class FakePage:
    pass


def test_rotate_loop_chase_after_miss():
    print("[1] rotate loop chase after miss")
    stop = threading.Event()
    calls = []
    logs = []
    state = {"placed": 0}

    old = {
        "balance": rotate._get_balance,
        "countdown": rotate._get_countdown,
        "draw": rotate._get_last_draw_with_issue,
        "place": rotate._place_bet,
        "sleep_interruptible": rotate._sleep_interruptible,
        "sleep": rotate.time.sleep,
    }

    def fake_balance(page):
        return 1000

    def fake_countdown(page):
        return 50

    def fake_draw(page):
        if state["placed"] >= 1:
            return "101", [1, 4, 3]  # A hit, A miss, A hit
        return "100", [0, 0, 0]

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        state["placed"] += 1
        if state["placed"] >= 2:
            stop.set()
        return True

    try:
        rotate._get_balance = fake_balance
        rotate._get_countdown = fake_countdown
        rotate._get_last_draw_with_issue = fake_draw
        rotate._place_bet = fake_place
        rotate._sleep_interruptible = lambda seconds, stop_event: None
        rotate.time.sleep = lambda seconds: None

        rotate._betting_loop(FakePage(), "acct", {
            "base_bet_amount": 10,
            "loss_multiplier": 1.3,
            "max_losses": 4,
            "bet_window_min": 20,
            "bet_window_max": 90,
            "close_buffer": 10,
            "draw_delay": 73,
            "daily_stop_loss": 999999,
            "take_profit": 999999,
        }, stop, lambda msg: logs.append(msg))
    finally:
        rotate._get_balance = old["balance"]
        rotate._get_countdown = old["countdown"]
        rotate._get_last_draw_with_issue = old["draw"]
        rotate._place_bet = old["place"]
        rotate._sleep_interruptible = old["sleep_interruptible"]
        rotate.time.sleep = old["sleep"]

    check(len(calls) == 2, "real place_bet called twice")
    check(calls[0][1] == [10, 10, 10], "first rotate bet uses base amounts")
    check(calls[1][1] == [10, 13, 10], "second rotate bet chases only missed ball")
    check(any("未中" in m and "下把注码=13" in m for m in logs), "miss log shows next chase amount")


def test_fixed_rush_virtual_then_real_inherits_step():
    print("[2] fixed rush virtual trigger then real bet")
    stop = threading.Event()
    calls = []
    logs = []
    state = {"virtual_recorded": False}

    old = {
        "balance": rush._get_balance,
        "countdown": rush._get_countdown,
        "draw": rush._get_last_draw_with_issue,
        "place": rush._place_bet,
        "sleep_interruptible": rush._sleep_interruptible,
        "sleep": rush.time.sleep,
        "sample": rush.random.sample,
    }

    def fake_balance(page):
        return 1000

    def fake_countdown(page):
        return 50

    def fake_draw(page):
        if state["virtual_recorded"]:
            return "201", [0, 9, 9]  # first ball hit, other balls miss; total still negative
        return "200", [0, 0, 0]

    def fake_sleep_interruptible(seconds, stop_event):
        if not calls:
            state["virtual_recorded"] = True

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        stop.set()
        return True

    try:
        rush._get_balance = fake_balance
        rush._get_countdown = fake_countdown
        rush._get_last_draw_with_issue = fake_draw
        rush._place_bet = fake_place
        rush._sleep_interruptible = fake_sleep_interruptible
        rush.time.sleep = lambda seconds: None
        rush.random.sample = lambda population, k: [0, 1, 2, 3]

        rush._betting_loop(FakePage(), "acct", {
            "strategy_mode": "simple",
            "base_bet_amount": 10,
            "rush_bet_amount": 20,
            "virtual_loss_trigger": 1,
            "bet_window_min": 20,
            "bet_window_max": 90,
            "close_buffer": 10,
            "draw_delay": 73,
            "daily_stop_loss": 999999,
            "take_profit": 999999,
            "odds": 9.92,
            "rebate_rate": 0.0073,
        }, stop, lambda msg: logs.append(msg))
    finally:
        rush._get_balance = old["balance"]
        rush._get_countdown = old["countdown"]
        rush._get_last_draw_with_issue = old["draw"]
        rush._place_bet = old["place"]
        rush._sleep_interruptible = old["sleep_interruptible"]
        rush.time.sleep = old["sleep"]
        rush.random.sample = old["sample"]

    check(len(calls) == 1, "virtual phase did not call real place_bet")
    check(calls[0][1] == [20, 10, 10], "real bet inherits rush step from virtual settlement")
    check(any("模拟投注已记录" in m for m in logs), "virtual bet log exists")
    check(any("下一期开始实投" in m for m in logs), "trigger-to-real log exists")


def main():
    print("=" * 56)
    print("betting loop smoke tests")
    print("=" * 56)
    for fn in [test_rotate_loop_chase_after_miss, test_fixed_rush_virtual_then_real_inherits_step]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()
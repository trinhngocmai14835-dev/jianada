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
import services.custom_rotate_bet_svc as custom_rotate
import services.custom_win_bet_svc as custom_win
import services.main_trend_bet_svc as main_trend
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
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
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



def test_rotate_late_window_bets_immediately():
    print("[2] rotate late window bets immediately")
    stop = threading.Event()
    calls = []
    logs = []

    old = {
        "balance": rotate._get_balance,
        "countdown": rotate._get_countdown,
        "draw": rotate._get_last_draw_with_issue,
        "place": rotate._place_bet,
        "sleep_interruptible": rotate._sleep_interruptible,
        "sleep": rotate.time.sleep,
    }

    try:
        rotate._get_balance = lambda page: 1000
        rotate._get_countdown = lambda page: 5
        rotate._get_last_draw_with_issue = lambda page: ("300", [0, 0, 0])
        rotate._sleep_interruptible = lambda seconds, stop_event: None
        rotate.time.sleep = lambda seconds: None

        def fake_place(page, targets, amounts, log, account):
            calls.append((targets, amounts))
            stop.set()
            return True

        rotate._place_bet = fake_place
        rotate._betting_loop(FakePage(), "acct", {
            "base_bet_amount": 10,
            "loss_multiplier": 1.3,
            "max_losses": 4,
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
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

    check(len(calls) == 1, "no close buffer still attempts late bet")
    check(any("立即下注" in m for m in logs), "late window does not wait random delay")



def test_custom_rotate_loop_uses_custom_amount_steps():
    print("[3] custom rotate loop uses custom amount steps")
    stop = threading.Event()
    calls = []
    logs = []
    state = {"placed": 0}

    old = {
        "balance": custom_rotate._get_balance,
        "countdown": custom_rotate._get_countdown,
        "draw": custom_rotate._get_last_draw_with_issue,
        "place": custom_rotate._place_bet,
        "sleep_interruptible": custom_rotate._sleep_interruptible,
        "sleep": custom_rotate.time.sleep,
        "read_stable_draw": custom_rotate.read_stable_draw,
    }

    def fake_draw(page):
        if state["placed"] >= 1:
            return "101", [1, 4, 3]
        return "100", [0, 0, 0]

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        state["placed"] += 1
        if state["placed"] >= 2:
            stop.set()
        return True

    try:
        custom_rotate._get_balance = lambda page: 1000
        custom_rotate._get_countdown = lambda page: 50
        custom_rotate._get_last_draw_with_issue = fake_draw
        custom_rotate._place_bet = fake_place
        custom_rotate._sleep_interruptible = lambda seconds, stop_event: None
        custom_rotate.time.sleep = lambda seconds: None
        custom_rotate.read_stable_draw = lambda page, reader: reader(page)

        custom_rotate._betting_loop(FakePage(), "acct", {
            "amount_steps": [10, 30, 50],
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
            "number_sets": [
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
            ],
            "bet_window_max": 90,
            "draw_delay": 73,
            "daily_stop_loss": 999999,
            "take_profit": 999999,
        }, stop, logs.append)
    finally:
        custom_rotate._get_balance = old["balance"]
        custom_rotate._get_countdown = old["countdown"]
        custom_rotate._get_last_draw_with_issue = old["draw"]
        custom_rotate._place_bet = old["place"]
        custom_rotate._sleep_interruptible = old["sleep_interruptible"]
        custom_rotate.time.sleep = old["sleep"]
        custom_rotate.read_stable_draw = old["read_stable_draw"]

    check(len(calls) == 2, "custom loop place_bet called twice")
    check(calls[0][1] == [10, 10, 10], "custom first bet uses first tier")
    check(calls[1][1] == [10, 30, 10], "custom second bet advances only missed ball")

def test_main_trend_loop_chases_after_special_sum():
    print("[4] main trend loop chases after special sum")
    stop = threading.Event()
    calls = []
    logs = []
    state = {"placed": 0}

    old = {
        "balance": main_trend._get_balance,
        "settled_balance": main_trend._get_settled_balance,
        "countdown": main_trend._get_countdown,
        "place": main_trend._place_main_trend_bet,
        "sleep_interruptible": main_trend._sleep_interruptible,
        "sleep": main_trend.time.sleep,
        "read_stable_draw": main_trend.read_stable_draw,
    }

    def fake_draw(page, reader):
        if state["placed"] >= 1:
            return "101", [4, 4, 5]  # sum 13: 小/单, special odds 1.6
        return "100", [0, 0, 0]

    def fake_place(page, target_amounts, log, account):
        calls.append(dict(target_amounts))
        state["placed"] += 1
        if state["placed"] >= 2:
            stop.set()
        return True, {"大": 2.05, "小": 2.05, "单": 2.05, "双": 2.05}

    try:
        main_trend._get_balance = lambda page: 1000
        main_trend._get_settled_balance = lambda page, samples=3, interval=0.35: 1000
        main_trend._get_countdown = lambda page: 50
        main_trend._place_main_trend_bet = fake_place
        main_trend._sleep_interruptible = lambda seconds, stop_event: None
        main_trend.time.sleep = lambda seconds: None
        main_trend.read_stable_draw = fake_draw

        main_trend._betting_loop(FakePage(), "acct", {
            "amount_steps": [10, 20, 30],
            "entry_miss_trigger": 0,
            "enabled_paths": [True, True],
            "bet_window_max": 90,
            "draw_delay": 73,
            "daily_stop_loss": 999999,
            "take_profit": 999999,
        }, stop, logs.append)
    finally:
        main_trend._get_balance = old["balance"]
        main_trend._get_settled_balance = old["settled_balance"]
        main_trend._get_countdown = old["countdown"]
        main_trend._place_main_trend_bet = old["place"]
        main_trend._sleep_interruptible = old["sleep_interruptible"]
        main_trend.time.sleep = old["sleep"]
        main_trend.read_stable_draw = old["read_stable_draw"]

    check(len(calls) == 2, "main trend place called twice")
    check(calls[0] == {"大": 10, "单": 10}, "first main trend bet uses 大/单 first tier")
    check(calls[1] == {"小": 20, "双": 10}, "sum 13 makes 大 lose to second tier and 单 reset first tier")
    check(any("结算赔率=1.6" in m for m in logs), "sum 13 winning path logs special odds")


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


def test_rotate_stop_loss_ignores_unsettled_bet_deduction():
    print("[6] rotate stop loss ignores unsettled bet deduction")
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
        "read_stable_draw": rotate.read_stable_draw,
    }

    def fake_balance(page):
        return 700 if state["placed"] == 1 else 1000

    def fake_draw(page):
        if state["placed"] >= 1:
            return "101", [1, 4, 3]
        return "100", [0, 0, 0]

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        state["placed"] += 1
        if state["placed"] >= 2:
            stop.set()
        return True

    try:
        rotate._get_balance = fake_balance
        rotate._get_countdown = lambda page: 50
        rotate._get_last_draw_with_issue = fake_draw
        rotate._place_bet = fake_place
        rotate._sleep_interruptible = lambda seconds, stop_event: None
        rotate.time.sleep = lambda seconds: None
        rotate.read_stable_draw = lambda page, reader: reader(page)

        rotate._betting_loop(FakePage(), "acct", {
            "base_bet_amount": 100,
            "loss_multiplier": 1.3,
            "max_losses": 4,
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
            "bet_window_min": 20,
            "bet_window_max": 90,
            "close_buffer": 10,
            "draw_delay": 73,
            "daily_stop_loss": "50",
            "take_profit": "999999",
        }, stop, logs.append)
    finally:
        rotate._get_balance = old["balance"]
        rotate._get_countdown = old["countdown"]
        rotate._get_last_draw_with_issue = old["draw"]
        rotate._place_bet = old["place"]
        rotate._sleep_interruptible = old["sleep_interruptible"]
        rotate.time.sleep = old["sleep"]
        rotate.read_stable_draw = old["read_stable_draw"]

    check(len(calls) == 2, "temporary bet deduction should not stop rotate before settlement")
    check(not any("已触发止损" in m for m in logs), "rotate stop loss waits for settled balance")


def test_custom_rotate_stop_loss_ignores_unsettled_bet_deduction():
    print("[7] custom rotate stop loss ignores unsettled bet deduction")
    stop = threading.Event()
    calls = []
    logs = []
    state = {"placed": 0}

    old = {
        "balance": custom_rotate._get_balance,
        "settled_balance": custom_rotate._get_settled_balance,
        "countdown": custom_rotate._get_countdown,
        "draw": custom_rotate._get_last_draw_with_issue,
        "place": custom_rotate._place_bet,
        "sleep_interruptible": custom_rotate._sleep_interruptible,
        "sleep": custom_rotate.time.sleep,
        "read_stable_draw": custom_rotate.read_stable_draw,
    }

    def fake_draw(page):
        if state["placed"] >= 1:
            return "101", [1, 4, 3]
        return "100", [0, 0, 0]

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        state["placed"] += 1
        if state["placed"] >= 2:
            stop.set()
        return True

    try:
        custom_rotate._get_balance = lambda page: 700 if state["placed"] == 1 else 1000
        custom_rotate._get_settled_balance = lambda page, samples=3, interval=0.35: 1000
        custom_rotate._get_countdown = lambda page: 50
        custom_rotate._get_last_draw_with_issue = fake_draw
        custom_rotate._place_bet = fake_place
        custom_rotate._sleep_interruptible = lambda seconds, stop_event: None
        custom_rotate.time.sleep = lambda seconds: None
        custom_rotate.read_stable_draw = lambda page, reader: reader(page)

        custom_rotate._betting_loop(FakePage(), "acct", {
            "amount_steps": [100, 130, 299],
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
            "number_sets": [
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
            ],
            "bet_window_max": 90,
            "draw_delay": 73,
            "daily_stop_loss": "50",
            "take_profit": "999999",
        }, stop, logs.append)
    finally:
        custom_rotate._get_balance = old["balance"]
        custom_rotate._get_settled_balance = old["settled_balance"]
        custom_rotate._get_countdown = old["countdown"]
        custom_rotate._get_last_draw_with_issue = old["draw"]
        custom_rotate._place_bet = old["place"]
        custom_rotate._sleep_interruptible = old["sleep_interruptible"]
        custom_rotate.time.sleep = old["sleep"]
        custom_rotate.read_stable_draw = old["read_stable_draw"]

    check(len(calls) == 2, "temporary bet deduction should not stop custom rotate before settlement")
    check(not any("已触发止损" in m for m in logs), "custom rotate stop loss waits for settled balance")

def test_custom_win_state_and_loop_progression():
    print("[8] custom win advances on hit and resets on miss")
    state_obj = custom_win._CustomWinPathState([10, 20, 30], 0)
    check(state_obj.active is True, "win mode starts active when entry trigger is direct")
    check(state_obj.get_bet() == 10, "win mode starts at first tier")
    check(state_obj.on_win() is False and state_obj.get_bet() == 20, "first win advances to second tier")
    check(state_obj.on_win() is False and state_obj.get_bet() == 30, "second win advances to third tier")
    check(state_obj.on_win() is True and state_obj.get_bet() == 10, "last tier win resets to first tier")
    check(state_obj.on_win() is False and state_obj.get_bet() == 20, "new sequence can advance again")
    check(state_obj.on_lose() is True and state_obj.get_bet() == 10, "any miss resets to first tier")

    gated = custom_win._CustomWinPathState([10, 20], 1)
    check(gated.active is False, "entry trigger starts in observation")
    check(gated.observe_entry(False) is True and gated.active is True, "entry miss trigger activates real betting")
    gated.on_lose()
    check(gated.active is True and gated.get_bet() == 10, "miss after activation stays active at first tier")

    stop = threading.Event()
    calls = []
    logs = []
    loop_state = {"placed": 0}

    old = {
        "balance": custom_win._get_balance,
        "settled_balance": custom_win._get_settled_balance,
        "countdown": custom_win._get_countdown,
        "draw": custom_win._get_last_draw_with_issue,
        "place": custom_win._place_bet,
        "sleep_interruptible": custom_win._sleep_interruptible,
        "sleep": custom_win.time.sleep,
        "read_stable_draw": custom_win.read_stable_draw,
    }

    def fake_draw(page):
        if loop_state["placed"] >= 2:
            return "102", [1, 2, 2]  # B target: ball1 miss, ball2/3 hit
        if loop_state["placed"] >= 1:
            return "101", [1, 1, 9]  # A target: ball1/2 hit, ball3 miss
        return "100", [0, 0, 0]

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        loop_state["placed"] += 1
        if loop_state["placed"] >= 3:
            stop.set()
        return True

    try:
        custom_win._get_balance = lambda page: 1000
        custom_win._get_settled_balance = lambda page, samples=3, interval=0.35: 1000
        custom_win._get_countdown = lambda page: 50
        custom_win._get_last_draw_with_issue = fake_draw
        custom_win._place_bet = fake_place
        custom_win._sleep_interruptible = lambda seconds, stop_event: None
        custom_win.time.sleep = lambda seconds: None
        custom_win.read_stable_draw = lambda page, reader: reader(page)

        custom_win._betting_loop(FakePage(), "acct", {
            "amount_steps": [10, 20, 30],
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
            "number_sets": [
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
            ],
            "bet_window_max": 90,
            "draw_delay": 73,
            "daily_stop_loss": 999999,
            "take_profit": 999999,
        }, stop, logs.append)
    finally:
        custom_win._get_balance = old["balance"]
        custom_win._get_settled_balance = old["settled_balance"]
        custom_win._get_countdown = old["countdown"]
        custom_win._get_last_draw_with_issue = old["draw"]
        custom_win._place_bet = old["place"]
        custom_win._sleep_interruptible = old["sleep_interruptible"]
        custom_win.time.sleep = old["sleep"]
        custom_win.read_stable_draw = old["read_stable_draw"]

    check(len(calls) == 3, "custom win loop places three mocked bets")
    check(calls[0][1] == [10, 10, 10], "custom win first bet uses first tier")
    check(calls[1][1] == [20, 20, 10], "wins advance independently and miss stays first tier")
    check(calls[2][1] == [10, 30, 20], "miss resets one path while other paths continue win rush")


def test_custom_win_stop_loss_ignores_unsettled_bet_deduction():
    print("[9] custom win stop loss ignores unsettled bet deduction")
    stop = threading.Event()
    calls = []
    logs = []
    state = {"placed": 0}

    old = {
        "balance": custom_win._get_balance,
        "settled_balance": custom_win._get_settled_balance,
        "countdown": custom_win._get_countdown,
        "draw": custom_win._get_last_draw_with_issue,
        "place": custom_win._place_bet,
        "sleep_interruptible": custom_win._sleep_interruptible,
        "sleep": custom_win.time.sleep,
        "read_stable_draw": custom_win.read_stable_draw,
    }

    def fake_draw(page):
        if state["placed"] >= 1:
            return "101", [1, 4, 3]
        return "100", [0, 0, 0]

    def fake_place(page, targets, amounts, log, account):
        calls.append((targets, amounts))
        state["placed"] += 1
        if state["placed"] >= 2:
            stop.set()
        return True

    try:
        custom_win._get_balance = lambda page: 700 if state["placed"] == 1 else 1000
        custom_win._get_settled_balance = lambda page, samples=3, interval=0.35: 1000
        custom_win._get_countdown = lambda page: 50
        custom_win._get_last_draw_with_issue = fake_draw
        custom_win._place_bet = fake_place
        custom_win._sleep_interruptible = lambda seconds, stop_event: None
        custom_win.time.sleep = lambda seconds: None
        custom_win.read_stable_draw = lambda page, reader: reader(page)

        custom_win._betting_loop(FakePage(), "acct", {
            "amount_steps": [100, 130, 299],
            "entry_miss_trigger": 0,
            "enabled_positions": [True, True, True],
            "number_sets": [
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
                {"set_a": [0, 1, 3, 5], "set_b": [2, 4, 6, 7, 9]},
            ],
            "bet_window_max": 90,
            "draw_delay": 73,
            "daily_stop_loss": "50",
            "take_profit": "999999",
        }, stop, logs.append)
    finally:
        custom_win._get_balance = old["balance"]
        custom_win._get_settled_balance = old["settled_balance"]
        custom_win._get_countdown = old["countdown"]
        custom_win._get_last_draw_with_issue = old["draw"]
        custom_win._place_bet = old["place"]
        custom_win._sleep_interruptible = old["sleep_interruptible"]
        custom_win.time.sleep = old["sleep"]
        custom_win.read_stable_draw = old["read_stable_draw"]

    check(len(calls) == 2, "temporary bet deduction should not stop custom win before settlement")
    check(not any("已触发止损" in m for m in logs), "custom win stop loss waits for settled balance")

def main():
    print("=" * 56)
    print("betting loop smoke tests")
    print("=" * 56)
    for fn in [test_rotate_loop_chase_after_miss, test_rotate_late_window_bets_immediately, test_custom_rotate_loop_uses_custom_amount_steps, test_main_trend_loop_chases_after_special_sum, test_fixed_rush_virtual_then_real_inherits_step, test_rotate_stop_loss_ignores_unsettled_bet_deduction, test_custom_rotate_stop_loss_ignores_unsettled_bet_deduction, test_custom_win_state_and_loop_progression, test_custom_win_stop_loss_ignores_unsettled_bet_deduction]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()
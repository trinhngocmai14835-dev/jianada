from datetime import datetime

from services.rotate_bet_svc import _next_alarm, _parse_hhmm
from services.trend_follow_bet_svc import _choose_target, _risk_stop_reason, _streak


def test_trend_streak_and_follow_target():
    draws = [[1, 1, 1], [2, 2, 2], [3, 3, 3]]  # 小、小、小
    assert _streak(draws, "size") == ("小", 3)
    assert _choose_target("size", {"dx": "小", "ds": "双"}, draws, {}, {"size_strategy": "follow"}) == "小"


def test_confirm_reverse_and_auto_modes():
    draws = [[1, 1, 1], [2, 2, 2]]
    result = {"dx": "小", "ds": "双"}
    assert _choose_target("size", result, draws, {}, {"size_strategy": "confirm", "size_confirm_streak": 3}) is None
    assert _choose_target("size", result, draws, {}, {"size_strategy": "reverse", "size_confirm_streak": 2}) == "大"
    alternating = [[1, 1, 1], [9, 9, 9], [1, 1, 1], [9, 9, 9], [1, 1, 1], [9, 9, 9]]
    assert _choose_target("size", {"dx": "大", "ds": "双"}, alternating, {}, {"size_strategy": "auto", "size_confirm_streak": 1}) == "小"


def test_take_profit_and_stop_loss():
    assert _risk_stop_reason(100, {"take_profit": 100, "daily_stop_loss": 0}) == "take_profit"
    assert _risk_stop_reason(-100, {"take_profit": 0, "daily_stop_loss": 100}) == "daily_stop_loss"
    assert _risk_stop_reason(50, {"take_profit": 100, "daily_stop_loss": 100}) is None


def test_scheduled_start_time_parser_and_next_alarm():
    assert _parse_hhmm("09:30") == (9, 30)
    assert _parse_hhmm("25:00") is None
    now = datetime(2026, 11, 15, 8, 0)
    assert _next_alarm((9, 0), now).hour == 9
    assert _next_alarm((7, 0), now).day == 16

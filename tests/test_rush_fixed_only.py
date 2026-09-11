"""Fixed rush-only compatibility tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from api.routes import DEFAULT_RUSHBET, _normalize_rushbet_config

REMOVED_KEYS = {"conditional_tiers", "loss_thresholds", "sleep_periods"}


def test_default_rushbet_is_fixed_only():
    assert DEFAULT_RUSHBET["strategy_mode"] == "simple"
    assert not (REMOVED_KEYS & set(DEFAULT_RUSHBET))


def test_old_conditional_config_is_sanitized_to_fixed():
    cfg = _normalize_rushbet_config({
        **DEFAULT_RUSHBET,
        "strategy_mode": "conditional",
        "conditional_tiers": [{"base": 50, "rush": 70}],
        "loss_thresholds": [2000],
        "sleep_periods": 3,
        "base_bet_amount": 11,
        "rush_bet_amount": 22,
        "start_mode": "scheduled",
        "start_time": "8:5",
    })

    assert cfg["strategy_mode"] == "simple"
    assert cfg["base_bet_amount"] == 11
    assert cfg["rush_bet_amount"] == 22
    assert cfg["start_mode"] == "scheduled"
    assert cfg["start_time"] == "08:05"
    assert not (REMOVED_KEYS & set(cfg))

import pytest

from services.follow_bet_svc import _follow_amount_config, _prepare_follow_bets
from api import routes


def test_fixed_amount_follows_each_source_bet_and_aggregates_same_selection():
    bets = [
        {"betId": "a", "pos": "第一球", "num": "3", "amount": 500},
        {"betId": "b", "pos": "第一球", "num": "3", "amount": 2000},
        {"betId": "c", "pos": "第二球", "num": "4", "amount": 10},
    ]
    assert _prepare_follow_bets(bets, "number", _follow_amount_config({"amount_mode": "fixed", "fixed_amount": 100})) == {
        "第一球": {"3": 200}, "第二球": {"4": 100}}
    assert _prepare_follow_bets(bets, "number", _follow_amount_config({"multiplier": .5})) == {
        "第一球": {"3": 1250}, "第二球": {"4": 5}}


def test_fixed_main_trend_and_accounts_are_independent():
    bets = [{"num": "小", "amount": 500}, {"num": "小", "amount": 50}, {"num": "单", "amount": 200}]
    assert _prepare_follow_bets(bets, "main_trend", _follow_amount_config({"amount_mode": "fixed", "fixed_amount": 20})) == {"小": 40, "单": 20}
    assert _prepare_follow_bets(bets, "main_trend", _follow_amount_config({"amount_mode": "fixed", "fixed_amount": 50})) == {"小": 100, "单": 50}
    assert _prepare_follow_bets(bets, "main_trend", _follow_amount_config({"multiplier": 2})) == {"小": 1100, "单": 400}


@pytest.mark.parametrize("config", [
    {"amount_mode": "fixed"}, {"amount_mode": "fixed", "fixed_amount": 0},
    {"amount_mode": "fixed", "fixed_amount": 1.5}, {"amount_mode": "fixed", "fixed_amount": "nan"},
    {"amount_mode": "multiplier", "multiplier": 0}, {"amount_mode": "other"},
])
def test_invalid_amounts_rejected(config):
    with pytest.raises(ValueError):
        _follow_amount_config(config)


def test_save_rejects_invalid_without_mutating_existing_config(monkeypatch):
    saved = []
    monkeypatch.setattr(routes, 'get_config', lambda *args: {"followers": [{"port": "9223", "multiplier": 2}]})
    monkeypatch.setattr(routes, 'set_config', lambda *args: saved.append(args))
    result = routes.save_followbet_config({"followers": [{"amount_mode": "fixed", "fixed_amount": 0}]})
    assert not result['ok']
    assert not saved
    result = routes.save_followbet_config({"followers": [{"amount_mode": "fixed", "fixed_amount": 100}]})
    assert result['ok']
    assert saved[0][1]['followers'][0]['fixed_amount'] == 100


def test_existing_config_stays_multiplier():
    config = routes._normalize_followbet({"followers": [{"multiplier": 3}]})
    assert _follow_amount_config(config['followers'][0]) == ('multiplier', 3)

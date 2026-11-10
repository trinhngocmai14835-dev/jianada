from services.follow_bet_svc import _fetch_bets, _parse_main_trend_targets


def test_main_trend_target_parser_ignores_market_name():
    assert _parse_main_trend_targets("主势盘 大小【大】") == ["大"]
    assert _parse_main_trend_targets("主势盘 单双：双") == ["双"]
    assert _parse_main_trend_targets("第1球【3】") == []


def test_main_trend_fetch_groups_rows(monkeypatch):
    class Page:
        def evaluate(self, _script):
            return [{
                "account": "client-a", "period": "20261110001",
                "content": "主势盘 大小【小】", "amount": "20", "betId": "X1",
            }]

    result = _fetch_bets(Page(), "main_trend")
    assert result[("client-a", "20261110001")][0]["num"] == "小"
    assert result[("client-a", "20261110001")][0]["pos"] == "main"

from services.follow_bet_svc import (
    _fetch_bets,
    _parse_main_trend_targets,
    _rebuild_report_url,
)
from urllib.parse import parse_qs, urlparse
import json


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


def test_rebuild_report_url_rotates_date_domain_and_agent():
    source = (
        "https://old.example/ReportNew/BettingDetail?"
        "uid=old&loginId=old-login&querydata=%7B%22userid%22%3A%22C1%22%2C"
        "%22startDate%22%3A%222026-11-10%22%2C%22endDate%22%3A%222026-11-10%22%7D"
    )
    rebuilt = _rebuild_report_url(source, "https://new.example", "2026-11-11", "U2", "L2")
    assert rebuilt.startswith("https://new.example/ReportNew/BettingDetail?")
    assert "uid=U2" in rebuilt
    assert "loginId=L2" in rebuilt
    assert '2026-11-11' in rebuilt
    qd = json.loads(parse_qs(urlparse(rebuilt).query)["querydata"][0])
    assert qd["userid"] == "C1"

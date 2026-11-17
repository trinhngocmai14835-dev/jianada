import json
from urllib.parse import urlencode
from types import SimpleNamespace

from api import routes, ws
from services.follow_bet_svc import _business_day_str, _current_domain, _rebuild_report_url, _target_page_matches
from datetime import datetime


def link(host="https://old.example", customer="c1", day="2026-11-16"):
    return host + "/ReportNew/BettingDetail?" + urlencode({"querydata": json.dumps({"userid":customer,"startDate":day,"endDate":day}),"uid":"u","loginId":"a"})


def test_domain_waits_for_backend_and_prefers_home_to_old_report():
    page=lambda url: SimpleNamespace(url=url)
    browser=SimpleNamespace(contexts=[SimpleNamespace(pages=[page(link()),page("https://new.example/Home/Index")])])
    assert _current_domain(browser) == "https://new.example"
    browser.contexts[0].pages=[page("https://new.example/Member/Login")]
    assert _current_domain(browser) is None


def test_target_url_requires_identity_and_date_and_confirms_redirect():
    rebuilt=_rebuild_report_url(link(),"https://new.example","2026-11-17")
    page=lambda url: SimpleNamespace(url=url)
    assert _target_page_matches(page(rebuilt), rebuilt,"https://new.example","2026-11-17")
    assert not _target_page_matches(page("https://new.example/Member/Login"), rebuilt,"https://new.example","2026-11-17")
    assert not _target_page_matches(page(link("https://new.example","c2","2026-11-17")), rebuilt,"https://new.example","2026-11-17")
    assert not _target_page_matches(page(link("https://new.example","c1","2026-11-16")), rebuilt,"https://new.example","2026-11-17")
    try:
        _rebuild_report_url("https://old.example/ReportNew/BettingDetail", "https://new.example", "2026-11-17")
        assert False
    except ValueError:
        pass
    assert _business_day_str(datetime(2026,11,17,6,59)) == "2026-11-16"
    assert _business_day_str(datetime(2026,11,17,7,0)) == "2026-11-17"


def test_http_logs_read_broadcast_history_without_consuming_queue(monkeypatch):
    task="followbet"
    original=list(ws._history[task]); seq=ws._seq[task]
    try:
        ws._history[task].clear()
        ws._history[task].append({"seq":42,"msg":"working"})
        ws._seq[task]=42
        assert routes.get_task_logs(task, 41)["messages"][0]["msg"] == "working"
        assert routes.get_task_logs(task, 41)["messages"][0]["msg"] == "working"
        assert routes.get_task_logs(task, 42)["messages"] == []
    finally:
        ws._history[task].clear(); ws._history[task].extend(original); ws._seq[task]=seq

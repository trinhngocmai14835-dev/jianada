import os
import sys
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.main_trend_bet_svc import (  # noqa: E402
    TARGET_INPUT_IDS,
    _ACCOUNT_STATUS,
    _ACCOUNT_STOPS,
    _MANUALLY_STOPPED,
    _STATUS_LOCK,
    _CustomAmountPathState,
    _main_trend_result,
    _observe_entry_draw,
    _parse_enabled_paths,
    _settlement_odds,
    _target_hit,
    _result_desc,
    _place_main_trend_bet,
    _goto_main_trend_page,
    _finalize_account,
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
    desc = _result_desc([4, 4, 5])
    check("开奖记录三球=[4,4,5]" in desc, "result log should expose draw numbers from history")
    check("和值=13" in desc, "result log should expose computed total")


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




class _NoopLocator:
    @property
    def first(self):
        return self

    def click(self, *args, **kwargs):
        return None


class _FakeFrame:
    def __init__(self, url):
        self.url = url
        self.evaluations = []
        self.goto_urls = []

    def evaluate(self, script, *args):
        self.evaluations.append((script, args))
        if "oddsValue" in script:
            return '{"大":"2.05","小":"2.05","单":"2.05","双":"2.05"}'
        return None

    def locator(self, selector):
        return _NoopLocator()

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        self.url = url


class _FakePage:
    def __init__(self, frame):
        self._frame = frame
        self.evaluations = []

    def frame(self, name=None):
        return self._frame

    def evaluate(self, script, *args):
        self.evaluations.append((script, args))
        return None

    def locator(self, selector):
        return _NoopLocator()


def test_main_trend_bet_fill_uses_no_argument_evaluate():
    frame = _FakeFrame("https://example.test/PlaceBet/Index?lotteryType=JND282&page=zsp")
    page = _FakePage(frame)
    logs = []

    ok, odds = _place_main_trend_bet(page, {"大": 20, "单": 20}, logs.append, "acct")

    check(ok is True, "main trend bet should submit when odds are open")
    check(odds["大"] == 2.05 and odds["单"] == 2.05, "odds should be read before submit")
    fill_calls = [item for item in frame.evaluations if "var clearIds" in item[0]]
    check(len(fill_calls) == 1, "amount fill script should run once")
    check(fill_calls[0][1] == (), "amount fill script should not pass a Playwright payload argument")


def test_goto_main_trend_page_uses_zsp_not_hm13():
    frame = _FakeFrame("https://example.test/PlaceBet/Index?lotteryType=JND282&page=hm13")
    page = _FakePage(frame)
    logs = []

    _goto_main_trend_page(page, "acct", logs.append)

    check("page=zsp" in frame.url, "main trend mode must land on zsp page")
    check(frame.goto_urls and "page=zsp" in frame.goto_urls[-1], "fallback navigation should rewrite page to zsp")


def test_goto_main_trend_page_refuses_unknown_url():
    frame = _FakeFrame("")
    page = _FakePage(frame)
    raised = False
    try:
        _goto_main_trend_page(page, "acct", lambda msg: None)
    except RuntimeError as exc:
        raised = "主势盘页面" in str(exc)
    check(raised, "unknown page should stop instead of betting on the wrong board")

def test_account_finalize_marks_exit_as_manual_stop():
    key = "acct@9222"
    child_stop = threading.Event()
    parent_stop = threading.Event()
    with _STATUS_LOCK:
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()
        _ACCOUNT_STATUS[key] = {"key": key, "account": "acct", "port": 9222, "status": "running"}
        _ACCOUNT_STOPS[key] = child_stop

    _finalize_account(key, parent_stop)

    with _STATUS_LOCK:
        check(key in _MANUALLY_STOPPED, "natural account exit should not auto restart")
        check(key not in _ACCOUNT_STOPS, "finalize removes child stop handle")
        check(_ACCOUNT_STATUS[key]["status"] == "stopped", "normal exit status becomes stopped")
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()


def test_account_finalize_keeps_all_stop_clean():
    key = "acct@9222"
    parent_stop = threading.Event()
    parent_stop.set()
    with _STATUS_LOCK:
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()
        _ACCOUNT_STATUS[key] = {"key": key, "account": "acct", "port": 9222, "status": "running"}
        _ACCOUNT_STOPS[key] = threading.Event()

    _finalize_account(key, parent_stop)

    with _STATUS_LOCK:
        check(key not in _MANUALLY_STOPPED, "global stop should not mark account as manually stopped")
        check(_ACCOUNT_STATUS[key]["status"] == "stopped", "global stop still marks status stopped")
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()

def main():
    test_main_trend_edges()
    test_independent_paths_and_entry_rotation()
    test_amount_steps_and_reset()
    test_enabled_paths_and_dom_ids()
    test_main_trend_bet_fill_uses_no_argument_evaluate()
    test_goto_main_trend_page_uses_zsp_not_hm13()
    test_goto_main_trend_page_refuses_unknown_url()
    test_account_finalize_marks_exit_as_manual_stop()
    test_account_finalize_keeps_all_stop_clean()
    print("test_main_trend_bet: OK")


if __name__ == "__main__":
    main()

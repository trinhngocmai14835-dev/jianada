import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import auto_bet_svc as auto


class FakePage:
    def __init__(self, url="https://example.test/Login", closed=False, goto_error=None):
        self.url = url
        self.closed = closed
        self.goto_error = goto_error
        self.goto_calls = []
        self.dialog_handlers = 0

    def is_closed(self):
        return self.closed

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self.goto_error:
            error = self.goto_error
            self.goto_error = None
            raise error
        self.url = url

    def on(self, event, handler):
        if event == "dialog":
            self.dialog_handlers += 1


class FakeContext:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.new_pages = []

    def new_page(self):
        page = FakePage(url="about:blank")
        self.new_pages.append(page)
        self.pages.append(page)
        return page


def test_browser_closed_error_detection():
    assert auto._is_browser_closed_error(Exception("Target page, context or browser has been closed"))
    assert auto._is_browser_closed_error(Exception("Target closed"))
    assert not auto._is_browser_closed_error(Exception("账号密码错误"))


def test_select_context_page_prefers_logged_in_page():
    login = FakePage("https://example.test/Login")
    home = FakePage("https://example.test/Home/Index")
    context = FakeContext([login, home])

    assert auto._select_context_page(context, login) is home


def test_refresh_login_page_reopens_when_page_closed():
    old_sleep = auto.time.sleep
    logs = []
    closed_login = FakePage(closed=True)
    context = FakeContext([closed_login])
    try:
        auto.time.sleep = lambda seconds: None
        page = auto._refresh_login_page(closed_login, context, "https://example.test/Login", "acct", logs.append)
    finally:
        auto.time.sleep = old_sleep

    assert page is context.new_pages[0]
    assert page.goto_calls[0][0] == "https://example.test/Login"
    assert page.dialog_handlers == 1
    assert any("登录页已关闭" in msg for msg in logs)


def test_refresh_login_page_uses_existing_logged_in_page():
    old_sleep = auto.time.sleep
    logs = []
    closed_login = FakePage(closed=True)
    home = FakePage("https://example.test/Member/Agreement")
    context = FakeContext([closed_login, home])
    try:
        auto.time.sleep = lambda seconds: None
        page = auto._refresh_login_page(closed_login, context, "https://example.test/Login", "acct", logs.append)
    finally:
        auto.time.sleep = old_sleep

    assert page is home
    assert context.new_pages == []
    assert home.goto_calls == []
    assert logs == []
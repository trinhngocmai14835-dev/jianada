"""Account whitelist guard tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import account_whitelist as aw


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  OK " + msg)


class Patch:
    def __init__(self):
        self.originals = {}
        self.store = {}

    def set(self, name, value):
        self.originals[name] = getattr(aw, name)
        setattr(aw, name, value)

    def restore(self):
        for name, value in self.originals.items():
            setattr(aw, name, value)


def with_store(patch):
    patch.set("get_machine_id", lambda: "ABCDEF1234567890")
    patch.set("get_config", lambda key, default=None: patch.store.get(key, default))
    patch.set("set_config", lambda key, value: patch.store.__setitem__(key, value))


def test_remote_allows_and_blocks():
    print("[1] remote whitelist allow/block")
    p = Patch()
    try:
        with_store(p)
        p.set("_fetch_remote_status", lambda mid: {
            "ok": True,
            "machine_id": mid,
            "enabled": True,
            "accounts": ["user-a", "user-b"],
            "source": "remote",
        })
        ok, msg, status = aw.check_accounts_allowed(["USER-A", "user-b"])
        check(ok is True, "authorized accounts pass")
        check(status["source"] == "remote", "remote source reported")

        ok, msg, _ = aw.check_accounts_allowed(["user-c"])
        check(ok is False and "user-c" in msg, "unauthorized account blocked")

        ok, msg, _ = aw.check_accounts_allowed([""])
        check(ok is False and "空账号" in msg, "blank account blocked")
    finally:
        p.restore()


def test_cache_fallback_and_no_cache_block():
    print("[2] cache fallback")
    p = Patch()
    try:
        with_store(p)
        p.store[aw.CACHE_KEY] = {
            "ok": True,
            "machine_id": "ABCDEF1234567890",
            "enabled": True,
            "accounts": ["cached-user"],
            "source": "remote",
        }
        p.set("_fetch_remote_status", lambda mid: (_ for _ in ()).throw(RuntimeError("network down")))
        ok, msg, status = aw.check_accounts_allowed(["cached-user"])
        check(ok is True and status["source"] == "cache", "cache allows known account")

        p.store.clear()
        ok, msg, status = aw.check_accounts_allowed(["cached-user"])
        check(ok is False and status["source"] == "none", "no cache blocks startup")
    finally:
        p.restore()


def test_missing_r2_object_is_empty_whitelist():
    print("[3] R2 missing object handling")
    original_urlopen = aw.urllib.request.urlopen

    def raise_403(req, timeout=None, **_kwargs):
        url = getattr(req, "full_url", "")
        raise aw.urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    try:
        aw.urllib.request.urlopen = raise_403
        status = aw._fetch_remote_status("ABCDEF1234567890")
        check(status["ok"] is True, "missing object still returns a status")
        check(status["accounts"] == [], "missing object means no accounts configured")
    finally:
        aw.urllib.request.urlopen = original_urlopen


def main():
    print("=" * 56)
    print("account whitelist tests")
    print("=" * 56)
    test_remote_allows_and_blocks()
    test_cache_fallback_and_no_cache_block()
    test_missing_r2_object_is_empty_whitelist()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()

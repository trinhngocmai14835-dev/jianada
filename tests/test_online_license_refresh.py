"""Tests for safe online renewal from the R2 machine record."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import account_whitelist as aw


def check(condition, message):
    if not condition:
        raise AssertionError("FAIL: " + message)
    print("  OK " + message)


class Patch:
    def __init__(self):
        self.originals = {}

    def set(self, name, value):
        self.originals[name] = getattr(aw, name)
        setattr(aw, name, value)

    def restore(self):
        for name, value in self.originals.items():
            setattr(aw, name, value)


def test_only_newer_signed_remote_code_is_saved():
    print("[1] accepts only newer signed remote license")
    p = Patch()
    saved = []
    try:
        p.set("get_account_whitelist_status", lambda: {"license_key": "new-key"})
        p.set("validate_license", lambda key: (True, "ok", "20271028"))
        p.set("get_license", lambda: {"key": "old-key", "expiry": "20261028"})
        p.set("save_license", lambda key, expiry: saved.append((key, expiry)))
        result = aw.refresh_license_from_remote()
        check(result["updated"] is True, "newer signed code accepted")
        check(saved == [("new-key", "20271028")], "newer code saved")
    finally:
        p.restore()


def test_old_or_invalid_remote_code_never_replaces_local():
    print("[2] rejects old and invalid remote license")
    p = Patch()
    saved = []
    try:
        p.set("get_account_whitelist_status", lambda: {"license_key": "old-key"})
        p.set("validate_license", lambda key: (True, "ok", "20261028"))
        p.set("get_license", lambda: {"key": "new-key", "expiry": "20271028"})
        p.set("save_license", lambda key, expiry: saved.append((key, expiry)))
        result = aw.refresh_license_from_remote()
        check(result["updated"] is False, "older remote code ignored")
        check(saved == [], "local license not downgraded")

        p.set("validate_license", lambda key: (False, "signature mismatch", ""))
        result = aw.refresh_license_from_remote()
        check(result["updated"] is False, "invalid remote code ignored")
        check(saved == [], "invalid code not saved")
    finally:
        p.restore()


if __name__ == "__main__":
    test_only_newer_signed_remote_code_is_saved()
    test_old_or_invalid_remote_code_never_replaces_local()
    print("ALL OK")

"""Updater safety checks."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import updater


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  OK " + msg)


def test_version_compare():
    print("[1] version compare")
    check(updater._is_newer("2026.08.20.3", "2026.08.20.2") is True, "newer patch accepted")
    check(updater._is_newer("2026.08.20.2", "2026.08.20.2") is False, "same version ignored")
    check(updater._is_newer("2026.08.19.9", "2026.08.20.2") is False, "older version ignored")


def test_trusted_update_url():
    print("[2] trusted update URL")
    good = "https://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/releases/app.zip"
    check(updater._validate_update_url(good) == good, "fixed R2 release URL accepted")
    try:
        updater._validate_update_url("https://example.com/releases/app.zip")
    except ValueError:
        check(True, "foreign host rejected")
    else:
        raise AssertionError("FAIL: foreign host rejected")
    try:
        updater._validate_update_url("http://pub-465f078b4f484662b30eb39d27ae5155.r2.dev/releases/app.zip")
    except ValueError:
        check(True, "non-HTTPS URL rejected")
    else:
        raise AssertionError("FAIL: non-HTTPS URL rejected")


def test_non_frozen_install_blocked():
    print("[3] non-frozen install blocked")
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    try:
        if had_frozen:
            delattr(sys, "frozen")
        res = updater.prepare_update_install()
        check(res["ok"] is False and "EXE" in res["message"], "source runtime cannot replace executable")
    finally:
        if had_frozen:
            sys.frozen = old_frozen


def main():
    print("=" * 56)
    print("updater tests")
    print("=" * 56)
    for fn in [test_version_compare, test_trusted_update_url, test_non_frozen_install_blocked]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()
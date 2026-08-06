"""Fixed rush virtual trigger tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.rush_bet_svc import _parse_virtual_loss_trigger, _virtual_ready_to_real


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  OK " + msg)


def test_parse_virtual_loss_trigger():
    print("[1] parse virtual trigger")
    check(_parse_virtual_loss_trigger(None) == 0, "None -> 0")
    check(_parse_virtual_loss_trigger("") == 0, "empty -> 0")
    check(_parse_virtual_loss_trigger("8000") == 8000, "string amount")
    check(_parse_virtual_loss_trigger(1200.9) == 1200, "float amount floors through int")
    check(_parse_virtual_loss_trigger(-100) == 0, "negative -> 0")
    check(_parse_virtual_loss_trigger("bad") == 0, "bad input -> 0")


def test_virtual_ready_to_real():
    print("[2] virtual ready to real")
    check(_virtual_ready_to_real(-7999.99, 8000) is False, "below trigger keeps simulating")
    check(_virtual_ready_to_real(-8000, 8000) is True, "exact trigger starts real next")
    check(_virtual_ready_to_real(-8500, 8000) is True, "beyond trigger starts real next")
    check(_virtual_ready_to_real(-100, 0) is False, "0 means no virtual trigger")
    check(_virtual_ready_to_real(500, 8000) is False, "virtual profit does not start real")


def main():
    print("=" * 56)
    print("fixed rush virtual trigger tests")
    print("=" * 56)
    for fn in [test_parse_virtual_loss_trigger, test_virtual_ready_to_real]:
        fn()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()
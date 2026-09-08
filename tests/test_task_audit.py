"""Task start/stop audit log tests."""
import os
import sys
import time
import queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from core.task_manager import TaskManager


def check(cond, msg):
    if not cond:
        raise AssertionError("FAIL: " + msg)
    print("  OK " + msg)


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            break
    return out


def test_start_stop_audit_logs():
    print("[1] start/stop audit logs")
    tm = TaskManager()

    def target(config, stop_event, log_queue):
        while not stop_event.is_set():
            time.sleep(0.01)

    ok, msg = tm.start("audit_test", target, {
        "accounts": [{"account": "a1"}, {"account": "a2"}],
        "strategy_mode": "simple",
        "start_mode": "now",
        "virtual_loss_trigger": 8000,
    })
    check(ok is True and msg == "启动成功", "task starts")
    q = tm.log_queue("audit_test")
    logs = drain(q)
    check(any("[审计] 启动任务" in item["msg"] for item in logs), "start audit log exists")
    check(any("账号=a1,a2" in item["msg"] for item in logs), "start audit masks passwords and lists accounts")

    ok, msg = tm.stop("audit_test")
    check(ok is True and msg == "正在停止...", "task stop requested")
    tm._tasks["audit_test"]["thread"].join(timeout=2)
    logs = drain(q)
    check(any("[审计] 请求停止任务" in item["msg"] for item in logs), "stop request audit log exists")
    check(any("[审计] 任务已停止" in item["msg"] for item in logs), "stopped audit log exists")
    check(tm.status("audit_test") == "stopped", "final status stopped")


def test_start_mode_normalization():
    print("[2] start_mode normalization")
    from api.routes import _normalize_start_config

    now_cfg = _normalize_start_config({"start_mode": "now", "start_time": "9:5"}, "08:00")
    check(now_cfg["start_mode"] == "now", "now mode stays immediate")
    check(now_cfg["start_time"] == "09:05", "start_time is normalized to HH:MM")

    scheduled_cfg = _normalize_start_config({"start_mode": "scheduled", "start_time": "23:59"}, "08:00")
    check(scheduled_cfg["start_mode"] == "scheduled", "scheduled mode is preserved")
    check(scheduled_cfg["start_time"] == "23:59", "valid scheduled time is preserved")

    bad_cfg = _normalize_start_config({"start_mode": "both", "start_time": "99:00"}, "08:00")
    check(bad_cfg["start_mode"] == "now", "invalid mixed mode falls back to now")
    check(bad_cfg["start_time"] == "08:00", "invalid time falls back to default")



def test_update_install_status():
    print("[3] update install progress status")
    from services.updater import get_update_install_status, start_update_install

    status = get_update_install_status()
    for key in ["ok", "running", "phase", "percent", "message", "downloaded_bytes", "total_bytes"]:
        check(key in status, f"status has {key}")
    check(status["running"] is False, "idle updater is not running")
    check(status["phase"] == "idle", "idle phase is exposed")

    res = start_update_install()
    check(res["ok"] is False, "non-frozen updater refuses install without starting background job")
    check("status" in res, "failed start still returns status")



def test_resource_conflict_guards():
    print("[4] task account and port conflict guards")
    tm = TaskManager()

    def target(config, stop_event, log_queue):
        while not stop_event.is_set():
            time.sleep(0.01)

    ok, msg = tm.start("mode_a", target, {"accounts": [{"account": "acct1", "port": 9222}]})
    check(ok is True, "first resource owner starts")

    ok, msg = tm.update_config("mode_a", {"accounts": [
        {"account": "acct1", "port": 9222},
        {"account": "acct3", "port": 9222},
    ]})
    check(ok is False and "9222" in msg, "running config update cannot duplicate port")
    check(len(tm._tasks["mode_a"]["config"].get("accounts", [])) == 1, "rejected running config update keeps previous config")
    ok, msg = tm.start("mode_b", target, {"accounts": [{"account": "acct2", "port": 9222}]})
    check(ok is False and "9222" in msg, "second task cannot reuse running port")

    ok, msg = tm.start("mode_c", target, {"accounts": [{"account": "acct1", "port": 9223}]})
    check(ok is False and "acct1" in msg.lower(), "second task cannot reuse running account")

    ok, msg = tm.start("mode_d", target, {"accounts": [
        {"account": "dup", "port": 9301},
        {"account": "dup", "port": 9302},
    ]})
    check(ok is False and "dup" in msg.lower(), "single task config cannot duplicate account")

    ok, msg = tm.stop("mode_a")
    check(ok is True, "resource owner stop requested")
    tm._tasks["mode_a"]["thread"].join(timeout=2)
    check(tm.status("mode_a") == "stopped", "resource owner stopped")

    ok, msg = tm.start("mode_b", target, {"accounts": [{"account": "acct2", "port": 9222}]})
    check(ok is True, "port can be reused after owner stops")
    tm.stop("mode_b")
    tm._tasks["mode_b"]["thread"].join(timeout=2)

def main():
    print("=" * 56)
    print("task audit tests")
    print("=" * 56)
    test_start_stop_audit_logs()
    test_start_mode_normalization()
    test_update_install_status()
    test_resource_conflict_guards()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()

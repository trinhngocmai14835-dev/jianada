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


def main():
    print("=" * 56)
    print("task audit tests")
    print("=" * 56)
    test_start_stop_audit_logs()
    print("=" * 56)
    print("ALL OK")


if __name__ == "__main__":
    main()
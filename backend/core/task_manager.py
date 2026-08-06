import threading
import queue
from datetime import datetime
from typing import Callable, Optional


class TaskManager:
    _instance: Optional["TaskManager"] = None
    _init_lock = threading.Lock()

    def __init__(self):
        self._tasks: dict = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "TaskManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _put_log(self, log_queue: queue.Queue, msg: str, level: str = "info"):
        item = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg": msg,
            "level": level,
        }
        try:
            log_queue.put_nowait(item)
        except queue.Full:
            try:
                log_queue.get_nowait()
            except Exception:
                pass
            try:
                log_queue.put_nowait(item)
            except Exception:
                pass

    def _audit_start_msg(self, task_id: str, config: dict) -> str:
        accounts = config.get("accounts") or []
        names = [
            str(a.get("account") or "").strip()
            for a in accounts
            if isinstance(a, dict) and str(a.get("account") or "").strip()
        ]
        parts = [f"任务={task_id}", f"账号数={len(accounts)}"]
        if names:
            parts.append("账号=" + ",".join(names))
        if config.get("strategy_mode"):
            parts.append(f"策略={config.get('strategy_mode')}")
        if config.get("start_mode"):
            parts.append(f"启动方式={config.get('start_mode')}")
        if config.get("virtual_loss_trigger") not in (None, ""):
            parts.append(f"虚拟触发={config.get('virtual_loss_trigger')}")
        return "[审计] 启动任务 | " + " | ".join(parts)

    def start(self, task_id: str, target: Callable, config: dict):
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t["status"] == "running":
                return False, "已在运行中"

            stop_event = threading.Event()
            log_queue: queue.Queue = queue.Queue(maxsize=2000)
            self._put_log(log_queue, self._audit_start_msg(task_id, config), "info")

            def _run():
                try:
                    target(config, stop_event, log_queue)
                except Exception as e:
                    self._put_log(log_queue, f"❌ 异常退出: {e}", "error")
                finally:
                    self._put_log(log_queue, f"[审计] 任务已停止 | 任务={task_id}", "info")
                    with self._lock:
                        if task_id in self._tasks:
                            self._tasks[task_id]["status"] = "stopped"

            thread = threading.Thread(target=_run, daemon=True, name=f"task-{task_id}")
            self._tasks[task_id] = {
                "thread": thread,
                "stop_event": stop_event,
                "log_queue": log_queue,
                "status": "running",
                "started_at": datetime.now().isoformat(),
            }
            thread.start()
        return True, "启动成功"

    def stop(self, task_id: str):
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return False, "任务不存在"
            self._put_log(t["log_queue"], f"[审计] 请求停止任务 | 任务={task_id}", "warn")
            t["stop_event"].set()
            t["status"] = "stopping"
        return True, "正在停止..."

    def status(self, task_id: str) -> str:
        t = self._tasks.get(task_id)
        if not t:
            return "stopped"
        if t["status"] == "running" and not t["thread"].is_alive():
            t["status"] = "stopped"
        return t["status"]

    def log_queue(self, task_id: str) -> Optional[queue.Queue]:
        t = self._tasks.get(task_id)
        return t["log_queue"] if t else None

    def all_status(self) -> dict:
        return {tid: self.status(tid) for tid in self._tasks}
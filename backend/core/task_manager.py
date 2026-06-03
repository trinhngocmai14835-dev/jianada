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

    def start(self, task_id: str, target: Callable, config: dict):
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t["status"] == "running":
                return False, "已在运行中"

            stop_event = threading.Event()
            log_queue: queue.Queue = queue.Queue(maxsize=2000)

            def _run():
                try:
                    target(config, stop_event, log_queue)
                except Exception as e:
                    log_queue.put({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "msg": f"❌ 异常退出: {e}",
                        "level": "error",
                    })
                finally:
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

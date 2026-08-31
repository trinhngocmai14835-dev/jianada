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

    def _refresh_locked(self, task_id: str, task: dict):
        if task["status"] == "running" and not task["thread"].is_alive():
            task["status"] = "stopped"
        return task["status"]

    def _resource_entries(self, task_id: str, config: dict):
        entries = []

        def add(kind: str, value):
            raw = str(value or "").strip()
            if not raw:
                return
            normalized = raw.lower() if kind == "account" else raw
            entries.append((kind, normalized, raw))

        if task_id == "followbet":
            add("account", config.get("source_account"))
            add("port", config.get("source_port"))
            for follower in config.get("followers") or []:
                if not isinstance(follower, dict):
                    continue
                add("account", follower.get("account"))
                add("port", follower.get("port"))
            return entries

        for account in config.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            add("account", account.get("account"))
            add("port", account.get("port"))
        return entries

    def _duplicate_resource_msg(self, task_id: str, config: dict):
        seen = {}
        labels = {"account": "账号", "port": "端口"}
        for kind, normalized, raw in self._resource_entries(task_id, config):
            key = (kind, normalized)
            if key in seen:
                return f"同一任务配置里重复使用{labels[kind]} {raw}"
            seen[key] = raw
        return None

    def _resource_conflict_msg_locked(self, task_id: str, config: dict):
        requested = self._resource_entries(task_id, config)
        if not requested:
            return None
        labels = {"account": "账号", "port": "端口"}
        for other_id, task in self._tasks.items():
            if other_id == task_id:
                continue
            if self._refresh_locked(other_id, task) == "stopped":
                continue
            occupied = {(kind, normalized) for kind, normalized, _raw in self._resource_entries(other_id, task.get("config") or {})}
            for kind, normalized, raw in requested:
                if (kind, normalized) in occupied:
                    return f"{labels[kind]} {raw} 已被任务 {other_id} 使用，请先停止该任务"
        return None

    def active_resources(self, exclude_task_id: str = None) -> dict:
        with self._lock:
            resources = {"accounts": {}, "ports": {}}
            for task_id, task in self._tasks.items():
                if exclude_task_id and task_id == exclude_task_id:
                    continue
                if self._refresh_locked(task_id, task) == "stopped":
                    continue
                for kind, normalized, _raw in self._resource_entries(task_id, task.get("config") or {}):
                    bucket = "accounts" if kind == "account" else "ports"
                    resources[bucket].setdefault(normalized, task_id)
            return resources

    def update_config(self, task_id: str, config: dict):
        with self._lock:
            task = self._tasks.get(task_id)
            if task and self._refresh_locked(task_id, task) != "stopped":
                task["config"] = dict(config or {})
                return True
        return False

    def start(self, task_id: str, target: Callable, config: dict):
        with self._lock:
            t = self._tasks.get(task_id)
            if t and self._refresh_locked(task_id, t) == "running":
                return False, "已在运行中"

            duplicate_msg = self._duplicate_resource_msg(task_id, config or {})
            if duplicate_msg:
                return False, duplicate_msg
            conflict_msg = self._resource_conflict_msg_locked(task_id, config or {})
            if conflict_msg:
                return False, conflict_msg

            stop_event = threading.Event()
            log_queue: queue.Queue = queue.Queue(maxsize=2000)
            self._put_log(log_queue, self._audit_start_msg(task_id, config or {}), "info")

            def _run():
                try:
                    target(config, stop_event, log_queue)
                except Exception as e:
                    self._put_log(log_queue, f"异常退出: {e}", "error")
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
                "config": dict(config or {}),
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
        return self._refresh_locked(task_id, t)

    def log_queue(self, task_id: str) -> Optional[queue.Queue]:
        t = self._tasks.get(task_id)
        return t["log_queue"] if t else None

    def all_status(self) -> dict:
        return {tid: self.status(tid) for tid in self._tasks}

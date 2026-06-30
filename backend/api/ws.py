import asyncio
import json
from collections import deque
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.task_manager import TaskManager

router = APIRouter()

_subscribers: dict[str, set] = {}

# 每个 task 保留最近 1000 条，每条带全局单调 seq
_TASK_IDS = ["autobet", "rushbet", "pickbet", "followbet"]

_history: dict[str, deque] = {tid: deque(maxlen=1000) for tid in _TASK_IDS}
_seq: dict[str, int] = {tid: 0 for tid in _TASK_IDS}


async def _broadcast_loop():
    while True:
        tm = TaskManager.get()
        for task_id in _TASK_IDS:
            q = tm.log_queue(task_id)
            if q is None:
                continue
            msgs = []
            while not q.empty():
                try:
                    msgs.append(q.get_nowait())
                except Exception:
                    break
            if not msgs:
                continue
            for m in msgs:
                _seq[task_id] += 1
                m["seq"] = _seq[task_id]
                _history[task_id].append(m)
            subs = _subscribers.get(task_id, set())
            dead = set()
            for ws in list(subs):
                for msg in msgs:
                    try:
                        await ws.send_text(json.dumps(msg, ensure_ascii=False))
                    except Exception:
                        dead.add(ws)
                        break
            for ws in dead:
                subs.discard(ws)
        await asyncio.sleep(0.1)


@router.websocket("/ws/logs/{task_id}")
async def ws_logs(ws: WebSocket, task_id: str):
    if task_id not in _TASK_IDS:
        await ws.close(code=1008)
        return
    await ws.accept()
    _subscribers.setdefault(task_id, set())
    for msg in list(_history.get(task_id, [])):
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            await ws.close()
            return
    _subscribers[task_id].add(ws)
    try:
        while True:
            data = await asyncio.wait_for(ws.receive_text(), timeout=30)
            if data == "ping":
                await ws.send_text("pong")
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        _subscribers.get(task_id, set()).discard(ws)


@router.get("/api/logs/{task_id}")
def get_recent_logs(task_id: str, since: int = 0):
    """返回 seq > since 的日志，前端用 seq 做增量拉取，不受 buffer 滚动影响"""
    history = list(_history.get(task_id, []))
    new_msgs = [m for m in history if m.get("seq", 0) > since]
    return {"seq": _seq.get(task_id, 0), "messages": new_msgs}

import asyncio
import json
import re
from collections import deque
from datetime import date
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.task_manager import TaskManager
from core.db import add_flow

router = APIRouter()

# 只把"有用流水"落盘：登录/下注/结算/止盈止损；其余啰嗦日志不入库
_FLOW_MARKERS = ('登录完成', '本期选号', '下注成功', '跟客户', '📊 开奖',
                 '实投结算 |', '模拟结算 |', '开奖结算 |',
                 '本期自算', '本期盈亏', '本期总盈亏', '止盈', '止损', '锁利')
_FLOW_SKIP_MARKERS = ('等待开奖结算', '等待下注窗口', '观察到新开奖')
_ACC_RE = re.compile(r'\[([^\]]+)\]')


def _persist_flow(task_id, m):
    text = m.get("msg", "") or ""
    if text.startswith("[审计]") or any(k in text for k in _FLOW_SKIP_MARKERS):
        return
    if not any(k in text for k in _FLOW_MARKERS):
        return
    am = _ACC_RE.search(text)
    account = am.group(1).strip() if am else ''
    ts = f"{date.today().strftime('%Y-%m-%d')} {m.get('time', '')}".strip()
    try:
        add_flow(task_id, account, text, ts)
    except Exception:
        pass

_subscribers: dict[str, set] = {}

# 每个 task 保留最近 1000 条，每条带全局单调 seq
_TASK_IDS = ["rushbet", "followbet", "rotatebet", "custom_rotatebet", "custom_winbet", "main_trend_bet"]

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
                _persist_flow(task_id, m)
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

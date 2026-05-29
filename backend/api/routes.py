from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

from core.db import get_config, set_config, get_license, save_license
from core.license import get_machine_id, validate_license
from core.task_manager import TaskManager
from services.auto_bet_svc import run as auto_bet_run
from services.follow_bet_svc import run as follow_bet_run, _launch_source_browser
from services.rush_bet_svc import run as rush_bet_run

router = APIRouter()

# ── 默认配置 ──────────────────────────────────────────────────

DEFAULT_AUTOBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    "base_bet_amount": 188,
    "numbers_per_pos": 9,
    "run_start_hour": 9,
    "run_end_hour": 21,
    "daily_stop_loss": 70000,
    "take_profit": 40000,
    "locked_profit": 30000,
    "profit_tier": 30000,
    "odds": 9.92,
    "rebate_rate": 0.0073,
    "tg_token": "",
    "tg_chat_id": "",
}

DEFAULT_RUSHBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    "base_bet_amount": 500,
    "rush_bet_amount": 700,
    "run_start_hour": 9,
    "run_end_hour": 21,
    "daily_stop_loss": 29000,
    "take_profit": 25000,
    "odds": 9.92,
    "rebate_rate": 0.0073,
}

DEFAULT_FOLLOWBET = {
    "entry_url": "",
    "source_port": "9222",
    "followers": [{"port": "9223", "bet_amount": 100}],
    "odds": 9.92,
    "rebate": 0.0073,
    "bet_window_start": 120,
    "bet_window_end": 35,
    "refresh_sec": 5,
}

# ── 授权 ──────────────────────────────────────────────────────

@router.get("/license")
def get_license_status():
    mid = get_machine_id()
    lic = get_license()
    if not lic:
        return {"valid": False, "message": "未激活", "machine_id": mid, "expiry": ""}
    valid, msg, expiry = validate_license(lic["key"])
    return {"valid": valid, "message": msg, "machine_id": mid, "expiry": expiry}


class ActivateRequest(BaseModel):
    key: str


@router.post("/license/activate")
def activate_license(req: ActivateRequest):
    valid, msg, expiry = validate_license(req.key)
    if valid:
        save_license(req.key, expiry)
    return {"valid": valid, "message": msg, "expiry": expiry}


# ── 配置 ──────────────────────────────────────────────────────

@router.get("/config/autobet")
def get_autobet_config():
    return get_config("autobet_config", DEFAULT_AUTOBET)


@router.post("/config/autobet")
def save_autobet_config(data: dict):
    existing = get_config("autobet_config", DEFAULT_AUTOBET)
    existing.update(data)
    set_config("autobet_config", existing)
    return {"ok": True}


@router.get("/debug/queue/{task_id}")
def debug_queue(task_id: str):
    import queue as _queue
    tm = TaskManager.get()
    q = tm.log_queue(task_id)
    if q is None:
        return {"error": "no queue", "status": tm.status(task_id)}
    msgs = []
    tmp = []
    while True:
        try:
            m = q.get_nowait()
            msgs.append(m)
            tmp.append(m)
        except _queue.Empty:
            break
    for m in tmp:
        q.put(m)
    return {"queue_size_after": q.qsize(), "messages_drained": msgs, "status": tm.status(task_id)}


@router.get("/config/rushbet")
def get_rushbet_config():
    return get_config("rushbet_config", DEFAULT_RUSHBET)


@router.post("/config/rushbet")
def save_rushbet_config(data: dict):
    existing = get_config("rushbet_config", DEFAULT_RUSHBET)
    existing.update(data)
    set_config("rushbet_config", existing)
    return {"ok": True}


@router.get("/config/followbet")
def get_followbet_config():
    return get_config("followbet_config", DEFAULT_FOLLOWBET)


@router.post("/config/followbet")
def save_followbet_config(data: dict):
    existing = get_config("followbet_config", DEFAULT_FOLLOWBET)
    existing.update(data)
    set_config("followbet_config", existing)
    return {"ok": True}


# ── 任务控制 ──────────────────────────────────────────────────

@router.get("/status")
def get_status():
    tm = TaskManager.get()
    return {
        "autobet": tm.status("autobet"),
        "rushbet": tm.status("rushbet"),
        "followbet": tm.status("followbet"),
    }


@router.post("/autobet/start")
def start_autobet():
    cfg = get_config("autobet_config", DEFAULT_AUTOBET)
    ok, msg = TaskManager.get().start("autobet", auto_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/autobet/stop")
def stop_autobet():
    ok, msg = TaskManager.get().stop("autobet")
    return {"ok": ok, "message": msg}


@router.post("/rushbet/start")
def start_rushbet():
    cfg = get_config("rushbet_config", DEFAULT_RUSHBET)
    ok, msg = TaskManager.get().start("rushbet", rush_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/rushbet/stop")
def stop_rushbet():
    ok, msg = TaskManager.get().stop("rushbet")
    return {"ok": ok, "message": msg}


@router.post("/followbet/start")
def start_followbet():
    cfg = get_config("followbet_config", DEFAULT_FOLLOWBET)
    ok, msg = TaskManager.get().start("followbet", follow_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/followbet/stop")
def stop_followbet():
    ok, msg = TaskManager.get().stop("followbet")
    return {"ok": ok, "message": msg}


class OpenBrowserRequest(BaseModel):
    port: str
    entry_url: str = ""

@router.post("/followbet/open-browser")
def open_follower_browser(req: OpenBrowserRequest):
    logs = []
    ok = _launch_source_browser(req.port, req.entry_url, lambda msg: logs.append(msg))
    return {"ok": ok, "message": logs[-1] if logs else ""}

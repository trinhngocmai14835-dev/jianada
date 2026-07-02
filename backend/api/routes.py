from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

from core.db import get_config, set_config, get_license, save_license
from core.license import get_machine_id, validate_license
from core.task_manager import TaskManager
from services.auto_bet_svc import run as auto_bet_run
from services.follow_bet_svc import run as follow_bet_run, _launch_source_browser
from services.rush_bet_svc import run as rush_bet_run
from services.pick_bet_svc import run as pick_bet_run

router = APIRouter()

# ── 默认配置 ──────────────────────────────────────────────────

DEFAULT_AUTOBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    "base_bet_amount": 188,
    "numbers_per_pos": 9,
    "daily_stop_loss": 70000,
    "take_profit": 40000,
    "locked_profit": 30000,
    "profit_tier": 30000,
    "odds": 9.92,
    "rebate_rate": 0.0073,
    "tg_token": "",
    "tg_chat_id": "",
    # 封盘/开奖时间参数（实测加拿大2.0：cdClose峰值~135s，封盘→开奖恒73s）
    "bet_window_min": 60,
    "bet_window_max": 120,
    "close_buffer": 10,
    "draw_delay": 73,
}

DEFAULT_RUSHBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    "strategy_mode": "conditional",
    "base_bet_amount": 500,
    "rush_bet_amount": 700,
    # 条件赢冲输缩档位参数（可在前端手动修改）
    "conditional_tiers": [
        {"base": 50, "rush": 70},
        {"base": 70, "rush": 98},
        {"base": 100, "rush": 140},
    ],
    "loss_thresholds": [2000, 3000],
    "sleep_periods": 3,
    "daily_stop_loss": 29000,
    "take_profit": 25000,
    "odds": 9.92,
    "rebate_rate": 0.0073,
    # 封盘/开奖时间参数（随平台节奏变动时在此调）
    # 实测加拿大2.0：cdClose峰值~135s，封盘→开奖恒为73s，整期~208s
    "bet_window_min": 60,    # 距封盘倒计时落在 [min,max] 才下注（峰值135>120，窗口有效）
    "bet_window_max": 120,
    "close_buffer": 10,      # 延时后仍需 >该秒数才下注，否则判封盘太快
    "draw_delay": 73,        # 封盘到开奖的间隔，下注后睡 remain+该值（实测73s）
}

DEFAULT_PICKBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    # 三路各自独立的 6 个号码（0~9），固定每期投注
    "pos_numbers": [
        [0, 1, 2, 3, 4, 5],
        [0, 1, 2, 3, 4, 5],
        [0, 1, 2, 3, 4, 5],
    ],
    "base_bet_amount": 500,   # 一阶底注
    "rush_bet_amount": 700,   # 二阶赢冲
    "daily_stop_loss": 29000,
    "take_profit": 25000,
    "odds": 9.92,
    "rebate_rate": 0.0073,
    # 封盘/开奖时间参数（实测加拿大2.0：cdClose峰值~135s，封盘→开奖恒73s）
    "bet_window_min": 60,
    "bet_window_max": 120,
    "close_buffer": 10,
    "draw_delay": 73,
}

DEFAULT_FOLLOWBET = {
    "entry_url": "",
    "safe_code": "",           # 平台入口安全码（关键字），采集+所有跟投账号共用
    "source_port": "9222",
    "source_account": "",      # 采集账号（填了则自动登录，否则手动登录）
    "source_password": "",
    # 倍数跟投：每注=客户金额×倍数；填了账号密码则点开始后自动开浏览器并登录
    "followers": [{"port": "9223", "multiplier": 1, "account": "", "password": ""}],
    # 目标客户未结明细：粘贴一次该客户的注单明细URL，工具每天自动用"今天+当前域名"重拼并打开
    "follow_targets": [],   # [{"label": "ab1351", "url": "https://.../ReportNew/BettingDetail?..."}]
    "odds": 9.92,
    "rebate": 0.0073,
    "bet_window_start": 120,
    "bet_window_end": 35,
    "refresh_sec": 3,   # 注单明细轮询间隔(秒)，越小跟得越贴身（但刷新越频繁）
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
    # 合并默认值：老客户已保存的配置可能缺少新增字段（封盘/开奖时间参数），用默认补齐
    return {**DEFAULT_AUTOBET, **get_config("autobet_config", {})}


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
    # 合并默认值：老客户已保存的配置可能缺少新增字段（档位/阈值/休眠），用默认补齐
    return {**DEFAULT_RUSHBET, **get_config("rushbet_config", {})}


@router.post("/config/rushbet")
def save_rushbet_config(data: dict):
    existing = get_config("rushbet_config", DEFAULT_RUSHBET)
    existing.update(data)
    set_config("rushbet_config", existing)
    return {"ok": True}


@router.get("/config/pickbet")
def get_pickbet_config():
    # 合并默认值：老配置缺新增字段时用默认补齐
    return {**DEFAULT_PICKBET, **get_config("pickbet_config", {})}


@router.post("/config/pickbet")
def save_pickbet_config(data: dict):
    existing = get_config("pickbet_config", DEFAULT_PICKBET)
    existing.update(data)
    set_config("pickbet_config", existing)
    return {"ok": True}


def _normalize_followbet(cfg: dict) -> dict:
    """迁移老配置：跟投账号从固定金额(bet_amount)改为倍数(multiplier)。
    缺 multiplier 的旧 follower 补默认 1 倍，并去掉废弃的 bet_amount。"""
    for f in (cfg.get("followers") or []):
        if not isinstance(f, dict):
            continue
        if f.get("multiplier") in (None, ""):
            f["multiplier"] = 1
        f.pop("bet_amount", None)
    return cfg


@router.get("/config/followbet")
def get_followbet_config():
    # 合并默认值 + 迁移老的 bet_amount → multiplier
    return _normalize_followbet({**DEFAULT_FOLLOWBET, **get_config("followbet_config", {})})


@router.post("/config/followbet")
def save_followbet_config(data: dict):
    existing = get_config("followbet_config", DEFAULT_FOLLOWBET)
    existing.update(data)
    set_config("followbet_config", existing)
    return {"ok": True}


# ── 任务控制 ──────────────────────────────────────────────────

def _check_license():
    """返回 (ok: bool, msg: str)，任务启动前调用"""
    lic = get_license()
    if not lic:
        return False, "未激活授权码，请先激活"
    valid, msg, _ = validate_license(lic["key"])
    return valid, msg


@router.get("/status")
def get_status():
    tm = TaskManager.get()
    return {
        "autobet": tm.status("autobet"),
        "rushbet": tm.status("rushbet"),
        "pickbet": tm.status("pickbet"),
        "followbet": tm.status("followbet"),
    }


@router.post("/autobet/start")
def start_autobet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = {**DEFAULT_AUTOBET, **get_config("autobet_config", {})}
    ok, msg = TaskManager.get().start("autobet", auto_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/autobet/stop")
def stop_autobet():
    ok, msg = TaskManager.get().stop("autobet")
    return {"ok": ok, "message": msg}


@router.post("/rushbet/start")
def start_rushbet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = {**DEFAULT_RUSHBET, **get_config("rushbet_config", {})}
    ok, msg = TaskManager.get().start("rushbet", rush_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/rushbet/stop")
def stop_rushbet():
    ok, msg = TaskManager.get().stop("rushbet")
    return {"ok": ok, "message": msg}


@router.post("/pickbet/start")
def start_pickbet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = {**DEFAULT_PICKBET, **get_config("pickbet_config", {})}
    ok, msg = TaskManager.get().start("pickbet", pick_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/pickbet/stop")
def stop_pickbet():
    ok, msg = TaskManager.get().stop("pickbet")
    return {"ok": ok, "message": msg}


@router.post("/followbet/start")
def start_followbet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = _normalize_followbet({**DEFAULT_FOLLOWBET, **get_config("followbet_config", {})})
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

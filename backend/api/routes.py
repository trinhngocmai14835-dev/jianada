from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

from core.db import (get_config, set_config, get_license, save_license,
                     get_flow, flow_accounts, clear_flow)
from core.license import get_machine_id, validate_license
from core.task_manager import TaskManager
from services.auto_bet_svc import run as auto_bet_run
from services.follow_bet_svc import run as follow_bet_run, _launch_source_browser
from services.rush_bet_svc import run as rush_bet_run
from services.pick_bet_svc import run as pick_bet_run
from services.rotate_bet_svc import run as rotate_bet_run
from services.custom_rotate_bet_svc import (
    run as custom_rotate_bet_run,
    get_account_statuses as custom_rotatebet_account_statuses,
    stop_account as stop_custom_rotatebet_account,
)
from services.custom_win_bet_svc import (
    run as custom_win_bet_run,
    get_account_statuses as custom_winbet_account_statuses,
    stop_account as stop_custom_winbet_account,
)
from services.main_trend_bet_svc import (
    run as main_trend_bet_run,
    get_account_statuses as main_trend_bet_account_statuses,
    stop_account as stop_main_trend_bet_account,
)
from services.account_whitelist import get_account_whitelist_status, check_accounts_allowed
from services.updater import check_for_update, get_update_install_status, start_update_install
from services.draw_analysis_svc import analyze_draw_payload, sample_draw_records, sample_draw_text

router = APIRouter()

TASK_LABELS = {
    "autobet": "自动下注",
    "rushbet": "赢冲输缩",
    "pickbet": "自选助赢冲",
    "followbet": "多账号跟投",
    "rotatebet": "轮换追损",
    "custom_rotatebet": "自定义金额轮换追损",
    "custom_winbet": "自定义金额轮换赢冲",
    "main_trend_bet": "主势大小单双追损",
}


def _task_statuses():
    tm = TaskManager.get()
    return {task_id: tm.status(task_id) for task_id in TASK_LABELS}


def _normalize_start_config(cfg: dict, default_time: str = "08:00") -> dict:
    out = dict(cfg or {})
    out["start_mode"] = "scheduled" if out.get("start_mode") == "scheduled" else "now"
    raw_time = str(out.get("start_time") or default_time).strip()
    try:
        h, m = [int(x) for x in raw_time.split(":", 1)]
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        out["start_time"] = f"{h:02d}:{m:02d}"
    except Exception:
        out["start_time"] = default_time
    return out

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
    # 封盘/开奖时间参数（与赢冲输缩一致）
    # 实测加拿大2.0：cdClose峰值~135s，封盘→开奖恒为73s，整期~208s
    "bet_window_min": 20,    # 距封盘倒计时落在 [min,max] 才下注
    "bet_window_max": 90,    # 下注触发点：cd≤90才下（比原120延后约30秒）
    "close_buffer": 10,      # 延时后仍需 >该秒数才下注，否则判封盘太快
    "draw_delay": 73,        # 封盘到开奖的间隔，下注后睡 remain+该值（实测73s）
}

DEFAULT_RUSHBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    # 启动方式：now=随开随跑（点开始就下注） / scheduled=闹钟定时（先登录待机，到点才下注）
    "start_mode": "now",
    "start_time": "08:00",   # 闹钟时刻 HH:MM，仅 start_mode=scheduled 时生效
    "strategy_mode": "conditional",
    "base_bet_amount": 500,
    "rush_bet_amount": 700,
    "virtual_loss_trigger": 0,  # 固定赢冲输缩：0=立即实投；>0=先模拟，虚拟累计亏损达到该金额后实投
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
    "bet_window_min": 20,    # 距封盘倒计时落在 [min,max] 才下注
    "bet_window_max": 90,    # 下注触发点：cd≤90才下（比原120延后约30秒）
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
    # 封盘/开奖时间参数（与赢冲输缩一致）
    # 实测加拿大2.0：cdClose峰值~135s，封盘→开奖恒为73s，整期~208s
    "bet_window_min": 20,    # 距封盘倒计时落在 [min,max] 才下注
    "bet_window_max": 90,    # 下注触发点：cd≤90才下（比原120延后约30秒）
    "close_buffer": 10,      # 延时后仍需 >该秒数才下注，否则判封盘太快
    "draw_delay": 73,        # 封盘到开奖的间隔，下注后睡 remain+该值（实测73s）
}

DEFAULT_ROTATEBET = {
    "entry_url": "https://166.tt",
    "safe_code": "",
    "accounts": [{"account": "", "password": "", "port": 9222}],
    "number_sets": [
        {"set_a": [0, 1, 3, 5, 8], "set_b": [2, 4, 6, 7, 9]},
        {"set_a": [0, 1, 3, 5, 8], "set_b": [2, 4, 6, 7, 9]},
        {"set_a": [0, 1, 3, 5, 8], "set_b": [2, 4, 6, 7, 9]},
    ],
    "base_bet_amount": 100,
    "entry_miss_trigger": 1,
    "enabled_positions": [True, True, True],
    "loss_multiplier": 1.3,
    "max_losses": 5,
    "daily_stop_loss": 29000,
    "take_profit": 25000,
    "start_mode": "now",
    "start_time": "09:00",
    "bet_window_min": 0,
    "bet_window_max": 90,
    "close_buffer": 0,
    "draw_delay": 73,
}

DEFAULT_CUSTOM_ROTATEBET = {
    **DEFAULT_ROTATEBET,
    "amount_steps": [100, 130, 299, 389, 506],
    "base_bet_amount": 100,
    "entry_miss_trigger": 1,
    "enabled_positions": [True, True, True],
    "start_mode": "now",
    "start_time": "09:00",
}

DEFAULT_CUSTOM_WINBET = {
    **DEFAULT_CUSTOM_ROTATEBET,
    "amount_steps": [100, 130, 299, 389, 506],
    "base_bet_amount": 100,
    "entry_miss_trigger": 1,
    "enabled_positions": [True, True, True],
    "start_mode": "now",
    "start_time": "09:00",
}

DEFAULT_MAIN_TREND_BET = {
    **DEFAULT_ROTATEBET,
    "amount_steps": [100, 130, 299, 389, 506],
    "base_bet_amount": 100,
    "entry_miss_trigger": 1,
    "enabled_paths": [True, True],
    "start_mode": "now",
    "start_time": "09:00",
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


@router.get("/account-whitelist")
def account_whitelist_status():
    return get_account_whitelist_status()


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
    return _normalize_start_config({**DEFAULT_RUSHBET, **get_config("rushbet_config", {})}, DEFAULT_RUSHBET["start_time"])


@router.post("/config/rushbet")
def save_rushbet_config(data: dict):
    existing = get_config("rushbet_config", DEFAULT_RUSHBET)
    existing.update(data)
    existing = _normalize_start_config(existing, DEFAULT_RUSHBET["start_time"])
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


def _login_accounts_for_task(task_id: str, cfg: dict):
    if task_id == "followbet":
        accounts = [cfg.get("source_account", "")]
        accounts.extend(
            f.get("account", "")
            for f in (cfg.get("followers") or [])
            if isinstance(f, dict)
        )
        return accounts
    return [
        a.get("account", "")
        for a in (cfg.get("accounts") or [])
        if isinstance(a, dict)
    ]


def _check_account_whitelist(task_id: str, cfg: dict):
    ok, msg, _status = check_accounts_allowed(_login_accounts_for_task(task_id, cfg))
    return ok, msg


def _check_license():
    """返回 (ok: bool, msg: str)，任务启动前调用"""
    lic = get_license()
    if not lic:
        return False, "未激活授权码，请先激活"
    valid, msg, _ = validate_license(lic["key"])
    return valid, msg


@router.get("/config/rotatebet")
def get_rotatebet_config():
    return _normalize_start_config({**DEFAULT_ROTATEBET, **get_config("rotatebet_config", {})}, DEFAULT_ROTATEBET["start_time"])


@router.post("/config/rotatebet")
def save_rotatebet_config(data: dict):
    existing = get_config("rotatebet_config", DEFAULT_ROTATEBET)
    existing.update(data)
    existing = _normalize_start_config(existing, DEFAULT_ROTATEBET["start_time"])
    set_config("rotatebet_config", existing)
    return {"ok": True}


@router.get("/config/custom-rotatebet")
def get_custom_rotatebet_config():
    return _normalize_start_config({**DEFAULT_CUSTOM_ROTATEBET, **get_config("custom_rotatebet_config", {})}, DEFAULT_CUSTOM_ROTATEBET["start_time"])


@router.post("/config/custom-rotatebet")
def save_custom_rotatebet_config(data: dict):
    existing = get_config("custom_rotatebet_config", DEFAULT_CUSTOM_ROTATEBET)
    existing.update(data)
    existing = _normalize_start_config(existing, DEFAULT_CUSTOM_ROTATEBET["start_time"])
    set_config("custom_rotatebet_config", existing)
    return {"ok": True}


@router.get("/config/custom-winbet")
def get_custom_winbet_config():
    return _normalize_start_config({**DEFAULT_CUSTOM_WINBET, **get_config("custom_winbet_config", {})}, DEFAULT_CUSTOM_WINBET["start_time"])


@router.post("/config/custom-winbet")
def save_custom_winbet_config(data: dict):
    existing = get_config("custom_winbet_config", DEFAULT_CUSTOM_WINBET)
    existing.update(data)
    existing = _normalize_start_config(existing, DEFAULT_CUSTOM_WINBET["start_time"])
    set_config("custom_winbet_config", existing)
    return {"ok": True}


@router.get("/config/main-trend-bet")
def get_main_trend_bet_config():
    return _normalize_start_config({**DEFAULT_MAIN_TREND_BET, **get_config("main_trend_bet_config", {})}, DEFAULT_MAIN_TREND_BET["start_time"])


@router.post("/config/main-trend-bet")
def save_main_trend_bet_config(data: dict):
    existing = get_config("main_trend_bet_config", DEFAULT_MAIN_TREND_BET)
    existing.update(data)
    existing = _normalize_start_config(existing, DEFAULT_MAIN_TREND_BET["start_time"])
    set_config("main_trend_bet_config", existing)
    return {"ok": True}

@router.get("/status")
def get_status():
    return _task_statuses()


@router.get("/update/check")
def api_update_check():
    return check_for_update()


@router.get("/update/status")
def api_update_status():
    return get_update_install_status()


@router.post("/update/install")
def api_update_install():
    statuses = _task_statuses()
    active = [TASK_LABELS[k] for k, v in statuses.items() if v != "stopped"]
    if active:
        return {
            "ok": False,
            "message": "请先停止所有投注任务，再执行更新",
            "active_tasks": active,
            "statuses": statuses,
        }
    return start_update_install()

@router.post("/autobet/start")
def start_autobet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = {**DEFAULT_AUTOBET, **get_config("autobet_config", {})}
    ok, msg = _check_account_whitelist("autobet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
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
    cfg = _normalize_start_config({**DEFAULT_RUSHBET, **get_config("rushbet_config", {})}, DEFAULT_RUSHBET["start_time"])
    ok, msg = _check_account_whitelist("rushbet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
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
    ok, msg = _check_account_whitelist("pickbet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
    ok, msg = TaskManager.get().start("pickbet", pick_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/pickbet/stop")
def stop_pickbet():
    ok, msg = TaskManager.get().stop("pickbet")
    return {"ok": ok, "message": msg}


@router.post("/rotatebet/start")
def start_rotatebet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = _normalize_start_config({**DEFAULT_ROTATEBET, **get_config("rotatebet_config", {})}, DEFAULT_ROTATEBET["start_time"])
    ok, msg = _check_account_whitelist("rotatebet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
    ok, msg = TaskManager.get().start("rotatebet", rotate_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/rotatebet/stop")
def stop_rotatebet():
    ok, msg = TaskManager.get().stop("rotatebet")
    return {"ok": ok, "message": msg}


@router.post("/custom-rotatebet/start")
def start_custom_rotatebet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = _normalize_start_config({**DEFAULT_CUSTOM_ROTATEBET, **get_config("custom_rotatebet_config", {})}, DEFAULT_CUSTOM_ROTATEBET["start_time"])
    ok, msg = _check_account_whitelist("custom_rotatebet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
    ok, msg = TaskManager.get().start("custom_rotatebet", custom_rotate_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/custom-rotatebet/stop")
def stop_custom_rotatebet():
    ok, msg = TaskManager.get().stop("custom_rotatebet")
    return {"ok": ok, "message": msg}


@router.get("/custom-rotatebet/accounts/status")
def custom_rotatebet_account_status():
    return {"accounts": custom_rotatebet_account_statuses()}


class StopCustomRotateBetAccountRequest(BaseModel):
    key: str


@router.post("/custom-rotatebet/accounts/stop")
def stop_custom_rotatebet_account_route(req: StopCustomRotateBetAccountRequest):
    ok, msg = stop_custom_rotatebet_account(req.key)
    return {"ok": ok, "message": msg}


@router.post("/custom-winbet/start")
def start_custom_winbet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = _normalize_start_config({**DEFAULT_CUSTOM_WINBET, **get_config("custom_winbet_config", {})}, DEFAULT_CUSTOM_WINBET["start_time"])
    ok, msg = _check_account_whitelist("custom_winbet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
    ok, msg = TaskManager.get().start("custom_winbet", custom_win_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/custom-winbet/stop")
def stop_custom_winbet():
    ok, msg = TaskManager.get().stop("custom_winbet")
    return {"ok": ok, "message": msg}


@router.get("/custom-winbet/accounts/status")
def custom_winbet_account_status():
    return {"accounts": custom_winbet_account_statuses()}


class StopCustomWinBetAccountRequest(BaseModel):
    key: str


@router.post("/custom-winbet/accounts/stop")
def stop_custom_winbet_account_route(req: StopCustomWinBetAccountRequest):
    ok, msg = stop_custom_winbet_account(req.key)
    return {"ok": ok, "message": msg}


@router.post("/main-trend-bet/start")
def start_main_trend_bet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = _normalize_start_config({**DEFAULT_MAIN_TREND_BET, **get_config("main_trend_bet_config", {})}, DEFAULT_MAIN_TREND_BET["start_time"])
    ok, msg = _check_account_whitelist("main_trend_bet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
    ok, msg = TaskManager.get().start("main_trend_bet", main_trend_bet_run, cfg)
    return {"ok": ok, "message": msg}


@router.post("/main-trend-bet/stop")
def stop_main_trend_bet():
    ok, msg = TaskManager.get().stop("main_trend_bet")
    return {"ok": ok, "message": msg}


@router.get("/main-trend-bet/accounts/status")
def main_trend_bet_account_status():
    return {"accounts": main_trend_bet_account_statuses()}


class StopMainTrendBetAccountRequest(BaseModel):
    key: str


@router.post("/main-trend-bet/accounts/stop")
def stop_main_trend_bet_account_route(req: StopMainTrendBetAccountRequest):
    ok, msg = stop_main_trend_bet_account(req.key)
    return {"ok": ok, "message": msg}

@router.post("/followbet/start")
def start_followbet():
    ok, msg = _check_license()
    if not ok:
        return {"ok": False, "message": msg}
    cfg = _normalize_followbet({**DEFAULT_FOLLOWBET, **get_config("followbet_config", {})})
    ok, msg = _check_account_whitelist("followbet", cfg)
    if not ok:
        return {"ok": False, "message": msg}
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


# ── 投注流水 ──────────────────────────────────────────────────



class DrawAnalysisRequest(BaseModel):
    text: str = ""
    records: list[dict[str, Any]] = []
    lookback: int = 80
    backtest_window: int = 300
    group_size: int = 5


@router.post("/draw-analysis/analyze")
def api_draw_analysis(req: DrawAnalysisRequest):
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return analyze_draw_payload(data)


@router.get("/draw-analysis/sample")
def api_draw_analysis_sample(limit: int = 160):
    return {"records": sample_draw_records(limit), "text": sample_draw_text(limit)}
@router.get("/flow")
def api_get_flow(account: str = "", mode: str = "", limit: int = 800):
    return {"records": get_flow(account or None, mode or None, limit)}


@router.get("/flow/accounts")
def api_flow_accounts():
    return {"accounts": flow_accounts()}


@router.post("/flow/clear")
def api_flow_clear(data: dict):
    clear_flow(account=(data.get("account") or None), days=(data.get("days") or None))
    return {"ok": True}

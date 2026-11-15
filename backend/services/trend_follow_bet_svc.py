"""基于开奖记录分析的主势大小/单双跟随投注。"""
import asyncio
import queue
import re
import sys
import threading
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

from core.db import get_config
from services.auto_bet_svc import (
    _free_port, _get_balance, _get_chrome, _get_countdown,
    _get_last_draw_with_issue, _login, _sleep_interruptible,
)
from services.main_trend_bet_svc import _main_trend_result, _place_main_trend_bet
from services.rotate_bet_svc import _wait_until_start

TASK_ID = "trend_follow_bet"
PATHS = {"size": ("大", "小"), "parity": ("单", "双")}


def _parse_steps(raw, fallback=100):
    vals = raw if isinstance(raw, (list, tuple)) else []
    out = []
    for value in vals:
        try:
            n = int(value)
            if n > 0: out.append(n)
        except (TypeError, ValueError):
            pass
    return out or [fallback]


def _streak(records, path):
    labels = PATHS[path]
    current = None; count = 0
    for draw in reversed(records or []):
        label = _main_trend_result(draw).get("dx" if path == "size" else "ds")
        if label not in labels: break
        if current is None: current = label
        if label != current: break
        count += 1
    return current, count


def _choose_target(path, result, records, state, cfg):
    labels = PATHS[path]
    current = result.get("dx" if path == "size" else "ds")
    if current not in labels: return None
    mode = cfg.get(f"{path}_strategy", cfg.get("strategy_mode", "follow"))
    if mode == "manual": return cfg.get(f"{path}_manual_target") if cfg.get(f"{path}_manual_target") in labels else None
    _last, streak = _streak(records, path)
    trigger = max(1, int(cfg.get(f"{path}_confirm_streak", cfg.get("confirm_streak", 2))))
    if mode == "confirm" and streak < trigger: return None
    if mode == "reverse" and streak >= trigger: return labels[1] if current == labels[0] else labels[0]
    if mode == "auto":
        recent = [(_main_trend_result(d).get("dx" if path == "size" else "ds")) for d in (records or [])[-6:]]
        switches = sum(1 for a, b in zip(recent, recent[1:]) if a in labels and b in labels and a != b)
        if switches >= 4: return labels[1] if current == labels[0] else labels[0]
        if streak < trigger: return None
    return current


def _goto_zsp(page):
    frame = page.frame(name="frame") or page
    url = getattr(frame, "url", "") or ""
    if "page=zsp" in url: return
    if "page=" not in url: raise RuntimeError("无法切换到主势盘")
    frame.goto(re.sub(r"page=[^&]+", "page=zsp", url), wait_until="domcontentloaded", timeout=15000)
    time.sleep(1)


def _run_account(acc, cfg, entry_url, safe_code, chrome_path, stop_event, log):
    if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    account, password, port = acc.get("account", ""), acc.get("password", ""), acc.get("port", 9222)
    try:
        _free_port(port, log)
        with sync_playwright() as p:
            args = [f"--remote-debugging-port={port}", "--no-sandbox", "--disable-blink-features=AutomationControlled", "--window-size=1280,900"]
            kw = {"headless": False, "args": args}
            if chrome_path: kw["executable_path"] = chrome_path
            browser = p.chromium.launch(**kw)
            context = browser.new_context(viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
            page = context.new_page()
            try:
                page = _login(page, context, account, password, entry_url, safe_code, log, target_page="zsp")
                _wait_until_start(cfg, account, stop_event, log)
                _betting_loop(page, account, cfg, stop_event, log)
            finally: browser.close()
    except Exception as exc: log(f"[{account}] 分析跟随异常: {exc}")


def _betting_loop(page, account, cfg, stop_event, log):
    _goto_zsp(page)
    last_issue = None; records = []; pending = {}; last_bet_targets = {}; steps = {p: 0 for p in PATHS}; start_balance = _get_balance(page) or 0; heartbeat = 0
    log(f"[{account}] 主势分析跟随启动 | 大小={'开' if cfg.get('enable_size', True) else '关'} | 单双={'开' if cfg.get('enable_parity', True) else '关'}")
    while not stop_event.is_set():
        live = get_config("trend_follow_bet_config", cfg) or {}; cfg.update(live)
        bal = _get_balance(page)
        if bal is not None:
            profit = bal - start_balance
            if float(cfg.get("take_profit", 0) or 0) > 0 and profit >= float(cfg["take_profit"]): log(f"[{account}] 达到止盈 {profit:+.2f}"); break
            if float(cfg.get("daily_stop_loss", 0) or 0) > 0 and profit <= -float(cfg["daily_stop_loss"]): log(f"[{account}] 达到止损 {profit:+.2f}"); break
        result = _get_last_draw_with_issue(page)
        if result and result[0] != last_issue:
            issue, draw = result; last_issue = issue; records.append(draw)
            labels = _main_trend_result(draw); log(f"[{account}] 开奖 {issue}: 和值{labels.get('total')} {labels.get('dx')}/{labels.get('ds')}")
            for path in PATHS:
                if path not in last_bet_targets or not last_bet_targets[path]: continue
                hit = last_bet_targets[path] == labels.get("dx" if path == "size" else "ds")
                steps[path] = 0 if hit else min(steps[path] + 1, len(_parse_steps(cfg.get(f"{path}_amount_steps"))) - 1)
                log(f"[{account}] {path} {'命中' if hit else '未中'}：投注{last_bet_targets[path]}，下一阶金额={_parse_steps(cfg.get(f'{path}_amount_steps'))[steps[path]]}")
            last_bet_targets = {}
            for path in PATHS:
                if (path == "size" and not cfg.get("enable_size", True)) or (path == "parity" and not cfg.get("enable_parity", True)): continue
                target = _choose_target(path, labels, records, {}, cfg)
                pending[path] = target
                if target: log(f"[{account}] {path}策略={cfg.get(f'{path}_strategy', cfg.get('strategy_mode','follow'))}，下一期方向={target}")
        cd = _get_countdown(page)
        if cd >= int(cfg.get("bet_window_min", 20)) and cd <= int(cfg.get("bet_window_max", 90)) and pending:
            amounts = {}; target_steps = {}
            for path, target in pending.items():
                if not target: continue
                raw = cfg.get(f"{path}_amount_steps", [100]); vals = _parse_steps(raw); amount = vals[min(steps[path], len(vals)-1)]; amounts[target] = amounts.get(target, 0) + amount; target_steps[path] = amount
            if amounts and _place_main_trend_bet(page, amounts, log, account)[0]:
                log(f"[{account}] ✅下注生效：{amounts}")
                last_bet_targets = dict(pending)
                pending = {}
                _sleep_interruptible(max(1, _get_countdown(page)) + int(cfg.get("draw_delay", 73)), stop_event)
                continue
        if time.time() - heartbeat > 30: log(f"[{account}] 等待投注窗口，倒计时{cd}s"); heartbeat = time.time()
        time.sleep(3)


def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg): log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})
    accounts = config.get("accounts") or []
    if not accounts: log("❌ 未配置账号"); return
    threads = []
    for acc in accounts:
        t = threading.Thread(target=_run_account, args=(acc, config, config.get("entry_url", "https://166.tt"), config.get("safe_code", ""), _get_chrome(), stop_event, log), daemon=True); t.start(); threads.append(t); time.sleep(3)
    for t in threads: t.join()

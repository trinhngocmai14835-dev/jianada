"""主势盘 + 1-3 球组合投注。

独立于多账号跟投和主势追损：每期按固定配置，在同一投注窗口依次完成
主势和值/两面与单球 1-3 定位下注。
"""
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
    _free_port, _get_chrome, _get_countdown, _get_last_draw_with_issue,
    _login, _place_bet, _sleep_interruptible,
)
from services.main_trend_bet_svc import _place_main_trend_bet
from services.rotate_bet_svc import _wait_until_start

TASK_ID = "combo_bet"


def _goto_page(page, target: str, log, account: str):
    frame = page.frame(name="frame") or page
    url = getattr(frame, "url", "") or ""
    if f"page={target}" in url:
        return
    if "page=" in url:
        target_url = re.sub(r"page=[^&]+", f"page={target}", url)
    elif "?" in url:
        target_url = url + f"&page={target}"
    else:
        raise RuntimeError(f"[{account}] 无法从当前页面切换到 {target}")
    frame.goto(target_url, wait_until="domcontentloaded", timeout=15000)
    time.sleep(1.2)
    log(f"[{account}] 已切换到 {'主势盘' if target == 'zsp' else '单球1-3'}")


def _clean_config(cfg: dict):
    sums = {}
    for raw_key, raw_amount in (cfg.get("main_sums") or {}).items():
        try:
            key, amount = int(raw_key), int(raw_amount)
            if 0 <= key <= 27 and amount > 0:
                sums[key] = amount
        except (TypeError, ValueError):
            continue
    sides = {}
    for key, raw_amount in (cfg.get("main_sides") or {}).items():
        try:
            amount = int(raw_amount)
            if key in {"大", "小", "单", "双", "极大", "极小", "大单", "大双", "小单", "小双"} and amount > 0:
                sides[key] = amount
        except (TypeError, ValueError):
            continue
    positions = []
    amounts = cfg.get("position_amounts") or [100, 100, 100]
    raw_positions = cfg.get("positions") or [[], [], []]
    for i in range(3):
        nums = sorted({int(n) for n in (raw_positions[i] if i < len(raw_positions) else []) if str(n).isdigit() and 0 <= int(n) <= 9})
        try:
            amount = max(0, int(amounts[i]))
        except (TypeError, ValueError, IndexError):
            amount = 0
        positions.append((nums, amount))
    return sums, sides, positions


def _place_combo(page, cfg: dict, log, account: str) -> bool:
    sums, sides, positions = _clean_config(cfg)
    if not sums and not sides and not any(nums and amount > 0 for nums, amount in positions):
        log(f"[{account}] 组合投注配置为空，跳过")
        return False
    _goto_page(page, "zsp", log, account)
    main_amounts = {str(k): v for k, v in sums.items()}
    # 主势和值的输入规则：和值 n 对应 odds_HZ(n+1)。
    # 页面金额输入由独立 JS 填写，避免依赖 React/旧 jQuery 事件。
    frame = page.frame(name="frame") or page
    fill = {f"odds_HZ{k + 1}": v for k, v in sums.items()}
    main_amounts.update(sides)
    side_ids = {"大": "odds_DX1", "小": "odds_DX2", "单": "odds_DS3", "双": "odds_DS4"}
    fill.update({side_ids[k]: v for k, v in sides.items() if k in side_ids})
    ok_main = True
    if fill:
        import json
        result = frame.evaluate("""(function(m){
          var count=0; Object.keys(m).forEach(function(id){var e=document.querySelector('#'+id);if(e){
            var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');if(d&&d.set)d.set.call(e,String(m[id]));else e.value=String(m[id]);
            e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));count++;}});
          var b=document.querySelector('#btnOk');if(b)b.click();
          var bs=document.querySelectorAll('input[type=button],button');for(var i=0;i<bs.length;i++){if((bs[i].value==='确定'||(bs[i].innerText||'').includes('确定'))&&bs[i].id!=='btnOk'){bs[i].click();break;}}
          return count;
        })(%s)""" % json.dumps(fill, ensure_ascii=False))
        time.sleep(1.5)
        ok_main = int(result or 0) == len(fill)
        if ok_main: log(f"[{account}] ✅ 主势下注完成：和值{list(sums)} | 两面{list(sides)}")
        else: log(f"[{account}] ⚠️ 主势控件未全部找到：找到{result}/{len(fill)}")
    if not ok_main:
        return False
    _goto_page(page, "hm13", log, account)
    targets = [nums for nums, amount in positions]
    amounts = [amount for _, amount in positions]
    active = any(nums and amount > 0 for nums, amount in positions)
    return _place_bet(page, targets, amounts, log, account) if active else True


def _betting_loop(page, account, cfg, stop_event, log):
    min_cd = int(cfg.get("bet_window_min", 20))
    max_cd = int(cfg.get("bet_window_max", 90))
    close_buffer = int(cfg.get("close_buffer", 10))
    draw_delay = int(cfg.get("draw_delay", 73))
    last_issue = None
    placed_issue = None
    heartbeat = 0.0
    last_cfg_signature = repr(cfg)
    log(f"[{account}] 主势+1-3组合投注启动")
    while not stop_event.is_set():
        # 热更新：保存配置后不重启浏览器、不重新登录，下一期直接使用新配置。
        try:
            latest = get_config("combo_bet_config", cfg) or {}
            merged = dict(cfg)
            merged.update(latest)
            signature = repr(merged)
            if signature != last_cfg_signature:
                cfg.clear()
                cfg.update(merged)
                last_cfg_signature = signature
                log(f"[{account}] 🔄 已加载最新组合投注配置，将从下一期生效")
        except Exception as exc:
            log(f"[{account}] 配置热更新读取失败，继续使用当前配置: {exc}")
        cd = _get_countdown(page)
        if cd < 0:
            time.sleep(2)
            continue
        res = _get_last_draw_with_issue(page)
        if res and res[0] != last_issue:
            last_issue = res[0]
            log(f"[{account}] 最新开奖: {res[0]}期 {res[1]}")
        if min_cd <= cd <= max_cd and placed_issue != last_issue:
            if _get_countdown(page) <= close_buffer:
                time.sleep(2)
                continue
            if _place_combo(page, cfg, log, account):
                placed_issue = last_issue
                remain = _get_countdown(page)
                log(f"[{account}] ✅ 组合投注完成，等待本期结算")
                _sleep_interruptible(max(1, remain) + draw_delay, stop_event)
                continue
        now = time.time()
        if now - heartbeat >= 30:
            log(f"[{account}] 等待组合投注窗口 | 倒计时 {cd}s")
            heartbeat = now
        time.sleep(3)


def _run_account(acc, cfg, entry_url, safe_code, chrome_path, stop_event, log):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    account, password, port = acc.get("account", ""), acc.get("password", ""), acc.get("port", 9222)
    _free_port(port, log)
    args = [f"--remote-debugging-port={port}", "--no-sandbox", "--disable-blink-features=AutomationControlled", "--window-size=1280,900"]
    try:
        with sync_playwright() as p:
            kw = {"headless": False, "args": args}
            if chrome_path: kw["executable_path"] = chrome_path
            browser = p.chromium.launch(**kw)
            context = browser.new_context(viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
            page = context.new_page()
            try:
                page = _login(page, context, account, password, entry_url, safe_code, log, target_page="hm13")
                _wait_until_start(cfg, account, stop_event, log)
                if stop_event.is_set():
                    return
                _betting_loop(page, account, cfg, stop_event, log)
            finally:
                browser.close()
    except Exception as exc:
        log(f"[{account}] 组合投注异常: {exc}")


def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg):
        log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})
    accounts = config.get("accounts") or []
    if not accounts:
        log("❌ 未配置组合投注账号")
        return
    threads = []
    for acc in accounts:
        t = threading.Thread(target=_run_account, args=(acc, config, config.get("entry_url", "https://166.tt"), config.get("safe_code", ""), _get_chrome(), stop_event, log), daemon=True)
        t.start(); threads.append(t); time.sleep(3)
    for t in threads: t.join()
    log("组合投注任务已结束")

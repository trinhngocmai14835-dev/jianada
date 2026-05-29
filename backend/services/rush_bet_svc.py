"""
赢冲输缩下注服务 — 3路4球随机 · 每期必投 · 赢冲输缩
"""

import asyncio
import sys
import time
import random
import threading
import queue
from datetime import datetime
from playwright.sync_api import sync_playwright

from services.auto_bet_svc import (
    _get_chrome, _login,
    _get_balance, _get_countdown, _get_last_draw, _place_bet,
)

NUMBERS_PER_POS = 4
NUM_POSITIONS = 3


def _betting_loop(page, account, cfg, stop_event, log):
    BASE_BET = cfg.get("base_bet_amount", 500)
    RUSH_BET = cfg.get("rush_bet_amount", 700)
    START_H = cfg.get("run_start_hour", 9)
    END_H = cfg.get("run_end_hour", 21)
    STOP_LOSS = cfg.get("daily_stop_loss", 29000)
    TAKE_PROFIT = cfg.get("take_profit", 25000)
    ODDS = cfg.get("odds", 9.92)
    REBATE = cfg.get("rebate_rate", 0.0073)

    start_balance = _get_balance(page) or 0
    pos_steps = [1, 1, 1]                          # 1=底注, 2=赢冲（下一期使用）
    last_targets = [None, None, None]               # 上期投注号码（结算用）
    last_amounts = [BASE_BET, BASE_BET, BASE_BET]   # 上期注码（结算用）
    last_draw = None
    bet_placed = False
    pending_settlement = False
    last_heartbeat = 0.0

    log(f"[{account}] 赢冲输缩启动 | 一阶{BASE_BET} / 二阶{RUSH_BET} | 起始余额: {start_balance}")

    while not stop_event.is_set():
        h = datetime.now().hour
        in_window = (START_H <= h < END_H) if START_H <= END_H else (h >= START_H or h < END_H)
        if not in_window:
            log(f"[{account}] 宵禁时间 ({h}点，窗口{START_H}-{END_H})，待机中...")
            time.sleep(60)
            continue

        bal = _get_balance(page)
        if bal is None:
            time.sleep(3)
            continue

        profit = bal - start_balance

        if profit >= TAKE_PROFIT:
            log(f"[{account}] 🎉 止盈! 利润={profit:.0f} ≥ {TAKE_PROFIT}")
            break
        if profit <= -STOP_LOSS:
            log(f"[{account}] 🩸 止损! 亏损={profit:.0f}")
            break

        draw = _get_last_draw(page)
        if draw and draw != last_draw:
            log(f"[{account}] 📊 开奖: {draw} | 利润: {profit:+.0f}")

            if pending_settlement and last_targets[0] is not None:
                total = 0.0
                for i in range(NUM_POSITIONS):
                    if last_targets[i] is None:
                        continue
                    amt = last_amounts[i]
                    ball_cost = amt * NUMBERS_PER_POS
                    rebate = ball_cost * REBATE
                    hit = draw[i] in last_targets[i]
                    if hit:
                        win = amt * ODDS
                        bp = win - ball_cost + rebate
                        log(f"[{account}]   球{i+1} 开{draw[i]} ✅中 | 赢{win:.2f}-投{ball_cost}+退{rebate:.2f}={bp:+.2f}")
                        if pos_steps[i] < 2:
                            pos_steps[i] = 2
                            log(f"[{account}]   → 升二阶赢冲 ({BASE_BET}→{RUSH_BET})")
                        else:
                            log(f"[{account}]   → 继续二阶赢冲 ({RUSH_BET})")
                    else:
                        bp = -ball_cost + rebate
                        log(f"[{account}]   球{i+1} 开{draw[i]} ❌未中 | -投{ball_cost}+退{rebate:.2f}={bp:+.2f}")
                        if pos_steps[i] > 1:
                            pos_steps[i] = 1
                            log(f"[{account}]   → 降一阶输缩 ({RUSH_BET}→{BASE_BET})")
                        else:
                            log(f"[{account}]   → 保持一阶底注 ({BASE_BET})")
                    total += bp
                log(f"[{account}]   本期自算: {total:+.2f} | 累计利润: {profit:+.0f}")
                pending_settlement = False

            last_draw = draw

        cd = _get_countdown(page)
        if cd < 0:
            time.sleep(2)
            continue

        if 60 <= cd <= 120 and not bet_placed:
            targets = [sorted(random.sample(range(10), NUMBERS_PER_POS)) for _ in range(NUM_POSITIONS)]
            amounts = [RUSH_BET if pos_steps[i] == 2 else BASE_BET for i in range(NUM_POSITIONS)]

            step_info = " | ".join(
                f"球{i+1}:{'二阶' if pos_steps[i]==2 else '一阶'}({amounts[i]})" for i in range(NUM_POSITIONS)
            )
            log(f"[{account}] 🎲 本期选号: {targets}")
            log(f"[{account}] ⚡ {step_info}")

            delay = random.uniform(2.0, 6.0)
            log(f"[{account}] ⏳ 距封盘{cd}s，延时{delay:.1f}s后下注...")
            time.sleep(delay)

            if stop_event.is_set():
                break

            if _get_countdown(page) > 10:
                ok = _place_bet(page, targets, amounts, log, account)
                if ok:
                    bet_placed = True
                    pending_settlement = True
                    last_targets = targets
                    last_amounts = amounts
                    remain = _get_countdown(page)
                    log(f"[{account}] ✅ 下注成功，等待开奖 ({remain+10}s)...")
                    time.sleep(remain + 10)
                    bet_placed = False
                    log(f"[{account}] 🔄 新一局准备中...")
            else:
                log(f"[{account}] ⚠️ 封盘太快，取消本期")
        elif bet_placed:
            time.sleep(3)
        else:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] ⏱ 等待下注窗口 | 倒计时{cd}s | 余额{bal:.0f}")
                last_heartbeat = now
            time.sleep(5)

    log(f"[{account}] 赢冲输缩循环已停止")


def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg):
        log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})

    accounts = config.get("accounts", [])
    if not accounts:
        log("❌ 未配置账号，请先添加账号")
        return

    entry_url = config.get("entry_url", "https://166.tt")
    safe_code = config.get("safe_code", "")
    chrome_path = _get_chrome()

    threads = []
    for acc_info in accounts:
        t = threading.Thread(
            target=_run_account,
            args=(acc_info, config, entry_url, safe_code, chrome_path, stop_event, log),
            daemon=True,
        )
        t.start()
        threads.append(t)
        time.sleep(5)

    for t in threads:
        t.join()

    log("所有账号线程已结束")


def _run_account(acc_info, config, entry_url, safe_code, chrome_path, stop_event, log):
    # Windows 子线程默认 SelectorEventLoop，不支持 subprocess，强制改为 ProactorEventLoop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    account = acc_info.get("account", "")
    password = acc_info.get("password", "")
    port = acc_info.get("port", 9222)

    launch_args = [
        f"--remote-debugging-port={port}",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--window-size=1280,900",
    ]

    try:
        with sync_playwright() as p:
            kwargs = {"headless": False, "args": launch_args}
            if chrome_path:
                kwargs["executable_path"] = chrome_path
                log(f"[{account}] 使用系统Chrome: {chrome_path}")
            browser = p.chromium.launch(**kwargs)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                ignore_https_errors=True,
            )
            context.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                window.chrome={runtime:{}};
            """)
            page = context.new_page()
            page.on("dialog", lambda d: d.accept())

            try:
                login_page = _login(page, context, account, password, entry_url, safe_code, log)
                _betting_loop(login_page, account, config, stop_event, log)
            except Exception as e:
                log(f"[{account}] ❌ 运行异常: {e}")
            finally:
                browser.close()
                log(f"[{account}] 浏览器已关闭")
    except Exception as e:
        log(f"[{account}] ❌ 启动失败: {e}")

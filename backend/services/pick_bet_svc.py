"""
自选号·赢冲输缩下注服务 — 3路·每路号码自选(个数不限，勾几个用几个) · 每期必投 · 赢冲输缩两阶
与 rush_bet_svc 的区别：号码由用户自选(三路各自任意个数)，不随机；注码为固定两阶赢冲输缩。
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
    _free_port, _sleep_interruptible,
)

NUM_POSITIONS = 3

# 三路默认号码（前端未配置时回退）：各取 0~5（个数仅为默认，用户可勾任意个数）
DEFAULT_POS_NUMBERS = [[0, 1, 2, 3, 4, 5] for _ in range(NUM_POSITIONS)]


def _parse_pos_numbers(raw):
    """把前端传来的三路号码规整成 [[..],[..],[..]]，每路为 0~9 去重升序，个数不限。
    任一路非法/空则该路回退默认；整体非法返回 None 由调用方兜底。"""
    if not raw:
        return None
    out = []
    try:
        for i in range(NUM_POSITIONS):
            row = raw[i] if i < len(raw) else None
            nums = []
            for n in (row or []):
                n = int(n)
                if 0 <= n <= 9 and n not in nums:
                    nums.append(n)
            out.append(sorted(nums) if nums else list(DEFAULT_POS_NUMBERS[i]))
    except (TypeError, ValueError):
        return None
    return out


def _betting_loop(page, account, cfg, stop_event, log):
    START_H = cfg.get("run_start_hour", 9)
    END_H = cfg.get("run_end_hour", 21)
    STOP_LOSS = cfg.get("daily_stop_loss", 29000)
    TAKE_PROFIT = cfg.get("take_profit", 25000)
    ODDS = cfg.get("odds", 9.92)
    REBATE = cfg.get("rebate_rate", 0.0073)

    # ===== 封盘/开奖时间参数（可被前端配置覆盖）=====
    WIN_MIN = int(cfg.get("bet_window_min", 60))      # 距封盘倒计时落在 [min,max] 才下注
    WIN_MAX = int(cfg.get("bet_window_max", 120))
    CLOSE_BUFFER = int(cfg.get("close_buffer", 10))   # 延时后仍需 >该秒数才下注
    DRAW_DELAY = int(cfg.get("draw_delay", 73))       # 封盘到开奖间隔（实测加拿大2.0=73s）

    BASE_BET = cfg.get("base_bet_amount", 500)   # 一阶底注
    RUSH_BET = cfg.get("rush_bet_amount", 700)   # 二阶赢冲

    POS_NUMBERS = _parse_pos_numbers(cfg.get("pos_numbers")) or [list(x) for x in DEFAULT_POS_NUMBERS]

    start_balance = _get_balance(page) or 0
    pos_steps = [1, 1, 1]                            # 1=一阶底注, 2=二阶赢冲（下一期使用）
    last_amounts = [BASE_BET, BASE_BET, BASE_BET]    # 上期注码（结算用）
    last_draw = None
    bet_placed = False
    pending_settlement = False
    last_heartbeat = 0.0

    nums_desc = " | ".join(f"球{i+1}:{POS_NUMBERS[i]}" for i in range(NUM_POSITIONS))
    log(f"[{account}] 自选号赢冲输缩启动 | 一阶{BASE_BET}/二阶{RUSH_BET} | {nums_desc} | 起始余额: {start_balance}")

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

            if pending_settlement:
                total = 0.0
                for i in range(NUM_POSITIONS):
                    amt = last_amounts[i]
                    ball_cost = amt * len(POS_NUMBERS[i])
                    rebate = ball_cost * REBATE
                    hit = draw[i] in POS_NUMBERS[i]
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

        # ⚠️ 必须等上一期开奖结算完才下注：赢冲输缩依赖上期中/未中决定本期 冲/缩
        if pending_settlement:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] ⏳ 等待上期开奖结算后再下注 | 倒计时{cd}s")
                last_heartbeat = now
            time.sleep(2)
            continue

        if WIN_MIN <= cd <= WIN_MAX and not bet_placed:
            targets = [list(POS_NUMBERS[i]) for i in range(NUM_POSITIONS)]
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

            if _get_countdown(page) > CLOSE_BUFFER:
                ok = _place_bet(page, targets, amounts, log, account)
                if ok:
                    bet_placed = True
                    pending_settlement = True
                    last_amounts = amounts
                    remain = _get_countdown(page)
                    log(f"[{account}] ✅ 下注成功，等待开奖 ({remain+DRAW_DELAY}s)...")
                    _sleep_interruptible(remain + DRAW_DELAY, stop_event)
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

    log(f"[{account}] 自选号赢冲输缩循环已停止")


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

    _free_port(port, log)
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

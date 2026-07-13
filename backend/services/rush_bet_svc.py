"""
赢冲输缩下注服务 — 3路4球随机 · 每期必投 · 赢冲输缩
"""

import asyncio
import sys
import time
import re
import random
import threading
import queue
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

from services.auto_bet_svc import (
    _get_chrome, _login,
    _get_balance, _get_countdown, _get_last_draw_with_issue, _place_bet,
    _free_port, _sleep_interruptible,
)

NUMBERS_PER_POS = 4
NUM_POSITIONS = 3

# ===== 条件赢冲输缩 档位表（内置默认，可被前端配置覆盖）=====
# 每档 = (一阶底注, 二阶赢冲)；二阶 ≈ 一阶 × 1.4
TIERS = [(50, 70), (70, 98), (100, 140)]
# 升档阈值：会话累计亏损绝对值（从启动余额算起，含之前已输金额，故为绝对累计）
#   档1 累计亏损 > 2000 → 档2；档2 累计亏损 > 3000 → 档3
LOSS_THRESHOLDS = [2000, 3000]
# 升档前休眠期数（这几期不下注，等待后再用新档位开打）
SLEEP_PERIODS = 3


def _parse_tiers(raw):
    """把前端传来的档位配置规整成 [(base, rush), ...]；非法/空返回 None 以便回退默认。
    兼容两种形态：[{'base':50,'rush':70}, ...] 或 [[50,70], ...]。"""
    if not raw:
        return None
    out = []
    try:
        for t in raw:
            if isinstance(t, dict):
                b, r = t.get("base"), t.get("rush")
            else:
                b, r = t[0], t[1]
            b, r = int(b), int(r)
            if b <= 0 or r <= 0:
                return None
            out.append((b, r))
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return out or None


def _parse_thresholds(raw):
    """规整升档阈值为 [int, ...]；非法/空返回 None 以便回退默认。"""
    if not raw:
        return None
    try:
        out = [int(x) for x in raw if x is not None and int(x) > 0]
    except (TypeError, ValueError):
        return None
    return out or None


def _next_tier(tier, profit, tiers=TIERS, loss_thresholds=LOSS_THRESHOLDS):
    """根据当前档位与会话累计利润，决定下一步档位动作（纯函数，便于测试）。

    tiers / loss_thresholds 默认用内置常量（保证旧测试 2 参调用不变），
    运行时由 _betting_loop 传入用户配置的实际档位表。

    返回 (new_tier, action)，action 取值：
      'reset'   — 已回正(利润>=0)，归位档1
      'upgrade' — 累计亏损突破当前档阈值，升一档
      'hold'    — 维持当前档位
    """
    if profit >= 0 and tier > 0:
        return 0, "reset"
    loss = -profit
    if tier < len(tiers) - 1 and tier < len(loss_thresholds) and loss > loss_thresholds[tier]:
        return tier + 1, "upgrade"
    return tier, "hold"


def _parse_hhmm(raw):
    """'08:00' -> (8, 0)；空/非法返回 None。"""
    if not isinstance(raw, str):
        return None
    m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$", raw)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def _next_alarm(hhmm, now=None):
    """算闹钟的下一次出现：今天的 HH:MM，已过则顺延次日（纯函数，便于测试）。

    严格取「下一次出现」，不做「已过就立即开跑」的宽限：
    否则「晚上23点挂上、等明早8点」会直接变成立刻开跑，那个错更危险。
    """
    now = now or datetime.now()
    h, mi = hhmm
    target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _fmt_span(seconds):
    """把剩余秒数说成「3小时07分」/「7分」/「不到1分」。"""
    total_min = max(0, int(seconds // 60))
    h, m = divmod(total_min, 60)
    if h:
        return f"{h}小时{m:02d}分"
    return f"{m}分" if m else "不到1分"


def _wait_until_start(cfg, account, stop_event, log):
    """闹钟定时：登录已完成，停在这里等到点再进下注循环。

    随开随跑(start_mode != 'scheduled') 直接返回，行为与原来完全一致。
    时刻非法时按随开随跑处理并告警 —— 配置填错不该让工具卡死不投注。
    """
    if cfg.get("start_mode", "now") != "scheduled":
        return

    hhmm = _parse_hhmm(cfg.get("start_time"))
    if not hhmm:
        log(f"[{account}] ⚠️ 闹钟时刻无效({cfg.get('start_time')!r})，按随开随跑处理，立即开始下注")
        return

    target = _next_alarm(hhmm)
    remain = (target - datetime.now()).total_seconds()
    log(f"[{account}] ⏰ 闹钟已设: {target:%Y-%m-%d %H:%M} 开始下注"
        f"(距现在 {_fmt_span(remain)}) | 登录已完成，待机中...")

    last_beat = time.time()
    while not stop_event.is_set():
        remain = (target - datetime.now()).total_seconds()
        if remain <= 0:
            break
        _sleep_interruptible(min(60.0, remain), stop_event)
        now = time.time()
        if now - last_beat >= 600:   # 每10分钟一条心跳，让前端看得出还活着
            log(f"[{account}] ⏳ 距开跑还有 {_fmt_span((target - datetime.now()).total_seconds())}")
            last_beat = now

    if not stop_event.is_set():
        log(f"[{account}] ▶️ 闹钟到点({target:%H:%M})，开始下注")


def _betting_loop(page, account, cfg, stop_event, log):
    STOP_LOSS = cfg.get("daily_stop_loss", 29000)
    TAKE_PROFIT = cfg.get("take_profit", 25000)
    ODDS = cfg.get("odds", 9.92)
    REBATE = cfg.get("rebate_rate", 0.0073)

    # ===== 封盘/开奖时间参数（可被前端配置覆盖，默认=原写死值）=====
    # 下注窗口：仅当「距封盘倒计时」落在 [WIN_MIN, WIN_MAX] 才下注
    WIN_MIN = int(cfg.get("bet_window_min", 20))
    WIN_MAX = int(cfg.get("bet_window_max", 90))   # 下注触发点：cd≤90才下（比原120延后约30秒，更靠近封盘）
    # 封盘缓冲：延时后仍需 >CLOSE_BUFFER 秒才真正下注，否则判「封盘太快」放弃
    CLOSE_BUFFER = int(cfg.get("close_buffer", 10))
    # 开奖延迟：封盘到开奖的间隔，下注后睡 remain+DRAW_DELAY 等开奖
    # 实测加拿大2.0：封盘后约 73s 开奖（cdDraw-cdClose 恒为 73），故默认 73
    DRAW_DELAY = int(cfg.get("draw_delay", 73))

    # 策略模式：conditional=条件赢冲输缩(档位升级) / simple=固定赢冲输缩(原版)
    conditional = cfg.get("strategy_mode", "conditional") == "conditional"

    # 条件模式档位参数：前端可配置，缺省/非法回退到内置默认
    TIER_LIST = _parse_tiers(cfg.get("conditional_tiers")) or TIERS
    THRESHOLDS = _parse_thresholds(cfg.get("loss_thresholds")) or LOSS_THRESHOLDS
    try:
        SLEEPS = max(0, int(cfg.get("sleep_periods", SLEEP_PERIODS)))
    except (TypeError, ValueError):
        SLEEPS = SLEEP_PERIODS

    # 闹钟定时：登录已完成，停在这里等到点；随开随跑则立即返回。
    # 放在读 start_balance 之前 —— 盈亏基准必须是真正开投那一刻的余额，不能被空等的几小时污染。
    _wait_until_start(cfg, account, stop_event, log)
    if stop_event.is_set():
        log(f"[{account}] 赢冲输缩循环已停止")
        return

    start_balance = _get_balance(page) or 0
    tier = 0                                        # 当前档位下标，对应 TIER_LIST（仅条件模式用）
    sleep_remaining = 0                             # 升档后剩余休眠期数（仅条件模式用）
    if conditional:
        BASE_BET, RUSH_BET = TIER_LIST[tier]        # 当前档位的 一阶/二阶 注码
    else:
        BASE_BET = cfg.get("base_bet_amount", 500)  # 固定一阶底注
        RUSH_BET = cfg.get("rush_bet_amount", 700)  # 固定二阶赢冲
    pos_steps = [1, 1, 1]                          # 1=底注, 2=赢冲（下一期使用）
    last_targets = [None, None, None]               # 上期投注号码（结算用）
    last_amounts = [BASE_BET, BASE_BET, BASE_BET]   # 上期注码（结算用）
    last_issue = None                               # 上次已结算的开奖期号（判新开奖用，不用号码值）
    bet_placed = False
    pending_settlement = False
    last_heartbeat = 0.0

    if conditional:
        tier_desc = " → ".join(f"档{i + 1}({b}/{r})" for i, (b, r) in enumerate(TIER_LIST))
        log(f"[{account}] 条件赢冲输缩启动 | {tier_desc} | 升档阈值(累计亏损){THRESHOLDS} 休眠{SLEEPS}期 | 回正归档1 | 起始余额: {start_balance}")
        log(f"[{account}] 当前档位: 档{tier + 1} 一阶{BASE_BET}/二阶{RUSH_BET}")
    else:
        log(f"[{account}] 固定赢冲输缩启动 | 一阶{BASE_BET} / 二阶{RUSH_BET}（二阶只冲1期，之后无论中否都回一阶）| 起始余额: {start_balance}")
    log(f"[{account}] ⏱ 时间参数 | 下注窗口{WIN_MIN}~{WIN_MAX}s | 封盘缓冲>{CLOSE_BUFFER}s | 开奖延迟+{DRAW_DELAY}s")

    while not stop_event.is_set():
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

        res = _get_last_draw_with_issue(page)
        if res and res[0] != last_issue:
            issue, draw = res
            log(f"[{account}] 📊 开奖: {issue}期 {draw} | 利润: {profit:+.0f}")

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
                        elif conditional:
                            log(f"[{account}]   → 继续二阶赢冲 ({RUSH_BET})")
                        else:
                            # 固定模式：二阶只冲一期，中了也收回一阶重新开始
                            pos_steps[i] = 1
                            log(f"[{account}]   → 二阶已完成，回一阶底注 ({RUSH_BET}→{BASE_BET})")
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

            # ===== 条件档位管理（每期评估一次，仅条件模式）=====
            if conditional:
                new_tier, action = _next_tier(tier, profit, TIER_LIST, THRESHOLDS)
                if action == "reset":
                    # 回正：整个过程中只要回到不亏，立即归位档1重新循环
                    tier = new_tier
                    BASE_BET, RUSH_BET = TIER_LIST[tier]
                    pos_steps = [1, 1, 1]
                    sleep_remaining = 0
                    log(f"[{account}] ↩️ 已回正(利润{profit:+.0f})，档位重置 → 档1 一阶{BASE_BET}/二阶{RUSH_BET}")
                elif action == "upgrade":
                    # 升档：累计亏损突破当前档阈值，先休眠再用新档位开打
                    crossed = THRESHOLDS[tier]
                    tier = new_tier
                    BASE_BET, RUSH_BET = TIER_LIST[tier]
                    pos_steps = [1, 1, 1]
                    sleep_remaining = SLEEPS
                    log(f"[{account}] ⬆️ 累计亏损{-profit:.0f}>{crossed}，升至 档{tier + 1} 一阶{BASE_BET}/二阶{RUSH_BET}，先休眠{SLEEPS}期")

            last_issue = issue

        cd = _get_countdown(page)
        if cd < 0:
            time.sleep(2)
            continue

        # ⚠️ 必须等上一期开奖结算完(pending_settlement=False)才下注：
        # 赢冲输缩依赖上期中/未中来决定本期 冲/缩，开奖未确认就下注会用错档位
        if pending_settlement:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] ⏳ 等待上期开奖结算后再下注 | 倒计时{cd}s")
                last_heartbeat = now
            time.sleep(2)
            continue

        if WIN_MIN <= cd <= WIN_MAX and not bet_placed:
            if sleep_remaining > 0:
                # 升档休眠：本期不下注，消耗一期后继续
                sleep_remaining -= 1
                log(f"[{account}] 😴 升档休眠中，本期跳过下注（剩余{sleep_remaining}期）| 档{tier + 1} {BASE_BET}/{RUSH_BET}")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + 10, stop_event)
                bet_placed = False
                continue

            targets = [sorted(random.sample(range(10), NUMBERS_PER_POS)) for _ in range(NUM_POSITIONS)]
            amounts = [RUSH_BET if pos_steps[i] == 2 else BASE_BET for i in range(NUM_POSITIONS)]

            step_info = " | ".join(
                f"球{i+1}:{'二阶' if pos_steps[i]==2 else '一阶'}({amounts[i]})" for i in range(NUM_POSITIONS)
            )
            prefix = f"档{tier + 1} " if conditional else ""
            log(f"[{account}] 🎲 {prefix}本期选号: {targets}")
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
                    last_targets = targets
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

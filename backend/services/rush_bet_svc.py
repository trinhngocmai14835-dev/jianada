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
from services.settlement_guard import (
    issue_gap,
    is_newer_issue,
    read_stable_draw,
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


def _parse_virtual_loss_trigger(raw):
    """Return fixed-mode virtual loss trigger amount. 0 means real bet immediately."""
    try:
        return max(0, int(float(raw or 0)))
    except (TypeError, ValueError):
        return 0


def _virtual_ready_to_real(virtual_profit, trigger_amount):
    return trigger_amount > 0 and virtual_profit <= -trigger_amount


def _format_rush_plan(targets, amounts, pos_steps):
    parts = []
    for i, nums in enumerate(targets):
        step = "二阶赢冲" if pos_steps[i] == 2 else "一阶底注"
        num_text = ",".join(str(n) for n in nums)
        parts.append(f"球{i+1}:{step} {num_text} x {amounts[i]}")
    return " | ".join(parts)

def _betting_loop(page, account, cfg, stop_event, log):
    STOP_LOSS = cfg.get("daily_stop_loss", 29000)
    TAKE_PROFIT = cfg.get("take_profit", 25000)
    ODDS = cfg.get("odds", 9.92)
    REBATE = cfg.get("rebate_rate", 0.0073)

    WIN_MIN = int(cfg.get("bet_window_min", 20))
    WIN_MAX = int(cfg.get("bet_window_max", 90))
    CLOSE_BUFFER = int(cfg.get("close_buffer", 10))
    DRAW_DELAY = int(cfg.get("draw_delay", 73))

    conditional = cfg.get("strategy_mode", "conditional") == "conditional"
    virtual_trigger = 0 if conditional else _parse_virtual_loss_trigger(cfg.get("virtual_loss_trigger", 0))
    virtual_active = virtual_trigger > 0
    virtual_profit = 0.0
    settled_profit = 0.0

    TIER_LIST = _parse_tiers(cfg.get("conditional_tiers")) or TIERS
    THRESHOLDS = _parse_thresholds(cfg.get("loss_thresholds")) or LOSS_THRESHOLDS
    try:
        SLEEPS = max(0, int(cfg.get("sleep_periods", SLEEP_PERIODS)))
    except (TypeError, ValueError):
        SLEEPS = SLEEP_PERIODS

    _wait_until_start(cfg, account, stop_event, log)
    if stop_event.is_set():
        log(f"[{account}] 赢冲输缩循环已停止")
        return

    start_balance = _get_balance(page) or 0
    tier = 0
    sleep_remaining = 0
    if conditional:
        BASE_BET, RUSH_BET = TIER_LIST[tier]
    else:
        BASE_BET = int(cfg.get("base_bet_amount", 500))
        RUSH_BET = int(cfg.get("rush_bet_amount", 700))
    pos_steps = [1, 1, 1]
    last_targets = [None, None, None]
    last_amounts = [BASE_BET, BASE_BET, BASE_BET]
    initial_draw = read_stable_draw(page, _get_last_draw_with_issue)
    last_issue = initial_draw[0] if initial_draw else None
    pending_issue = None
    pending_virtual = False
    if last_issue:
        log(f"[{account}] 结算锚点初始化：当前已开奖期号 {last_issue}")
    else:
        log(f"[{account}] 警告：暂未稳定读取到初始开奖期号，首次下注前会再次确认")
    bet_placed = False
    pending_settlement = False
    last_heartbeat = 0.0

    if conditional:
        tier_desc = " -> ".join(f"档{i + 1}({b}/{r})" for i, (b, r) in enumerate(TIER_LIST))
        log(f"[{account}] 条件赢冲输缩启动 | {tier_desc} | 升档阈值={THRESHOLDS} 休眠={SLEEPS}期 | 起始余额={start_balance}")
        log(f"[{account}] 当前档位=档{tier + 1} 一阶={BASE_BET} 二阶={RUSH_BET}")
    else:
        if virtual_active:
            log(f"[{account}] 固定赢冲输缩启动 | 一阶={BASE_BET} 二阶={RUSH_BET} | 先模拟投注，虚拟累计亏损达到{virtual_trigger}元后开始实投 | 起始余额={start_balance}")
        else:
            log(f"[{account}] 固定赢冲输缩启动 | 一阶={BASE_BET} 二阶={RUSH_BET} | 立即实投 | 起始余额={start_balance}")
    log(f"[{account}] 时间参数 | 下注窗口={WIN_MIN}-{WIN_MAX}秒 封盘缓冲>{CLOSE_BUFFER}秒 开奖延迟+{DRAW_DELAY}秒")

    while not stop_event.is_set():
        bal = _get_balance(page)
        if bal is None:
            time.sleep(3)
            continue

        profit = bal - start_balance

        if not virtual_active:
            if profit >= TAKE_PROFIT:
                log(f"[{account}] 已触发止盈：利润={profit:.0f} >= {TAKE_PROFIT}")
                break
            if profit <= -STOP_LOSS:
                log(f"[{account}] 已触发止损：利润={profit:.0f}")
                break

        res = read_stable_draw(page, _get_last_draw_with_issue)
        if res:
            issue, draw = res
            handled_draw = False

            if pending_settlement:
                if pending_issue is None:
                    log(f"[{account}] 错误：缺少投注期号锚点，为避免错期结算，停止当前账号")
                    break
                if last_targets[0] is not None and is_newer_issue(issue, pending_issue):
                    gap = issue_gap(issue, pending_issue)
                    if gap is not None and gap > 1:
                        log(f"[{account}] 警告：开奖期号从 {pending_issue} 跳到 {issue}，按最新稳定开奖结果结算，请核对记录")
                    phase = "模拟结算" if pending_virtual else "实投结算"
                    log(f"[{account}] {phase} | 期号={issue} 开奖={draw} | 投注锚点={pending_issue} | 平台利润={profit:+.0f}")

                    total = 0.0
                    settle_details = []
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
                            log(f"[{account}]   球{i+1} 开{draw[i]}【中奖】| 赢={win:.2f}-投注={ball_cost}+返水={rebate:.2f}={bp:+.2f}")
                            if pos_steps[i] < 2:
                                pos_steps[i] = 2
                                log(f"[{account}]   -> 下期升二阶赢冲 ({BASE_BET}->{RUSH_BET})")
                            elif conditional:
                                log(f"[{account}]   -> 下期继续二阶赢冲 ({RUSH_BET})")
                            else:
                                pos_steps[i] = 1
                                log(f"[{account}]   -> 固定模式二阶已完成，下期回一阶底注 ({RUSH_BET}->{BASE_BET})")
                        else:
                            bp = -ball_cost + rebate
                            log(f"[{account}]   球{i+1} 开{draw[i]}【未中】| -投注={ball_cost}+返水={rebate:.2f}={bp:+.2f}")
                            if pos_steps[i] > 1:
                                pos_steps[i] = 1
                                log(f"[{account}]   -> 下期输缩回一阶 ({RUSH_BET}->{BASE_BET})")
                            else:
                                log(f"[{account}]   -> 下期保持一阶底注 ({BASE_BET})")
                        result = "中" if hit else "未中"
                        next_step = "二阶赢冲" if pos_steps[i] == 2 else "一阶底注"
                        nums = ",".join(str(n) for n in last_targets[i])
                        settle_details.append(
                            f"球{i+1}:开{draw[i]}{result} 投{nums}x{amt} 盈亏={bp:+.2f} 下期={next_step}"
                        )
                        total += bp

                    detail_text = " | ".join(settle_details) if settle_details else "无明细"
                    if pending_virtual:
                        virtual_profit += total
                        log(f"[{account}]   模拟本期盈亏={total:+.2f} | 虚拟累计盈亏={virtual_profit:+.2f} | 触发线=-{virtual_trigger} | 明细: {detail_text}")
                        if _virtual_ready_to_real(virtual_profit, virtual_trigger):
                            virtual_active = False
                            start_balance = _get_balance(page) or start_balance
                            log(f"[{account}] 虚拟累计亏损已达到{virtual_trigger}元；下一期开始实投，并继承当前一阶/二阶状态 | 实投起始余额={start_balance}")
                    else:
                        settled_profit += total
                        log(f"[{account}]   本期自算盈亏={total:+.2f} | 自算累计={settled_profit:+.2f} | 平台利润={profit:+.0f} | 明细: {detail_text}")

                    pending_settlement = False
                    pending_virtual = False
                    pending_issue = None
                    last_issue = issue
                    handled_draw = True
            elif issue != last_issue:
                log(f"[{account}] 观察到新开奖 | 期号={issue} 开奖={draw} | 平台利润={profit:+.0f}")
                last_issue = issue
                handled_draw = True

            if handled_draw and conditional:
                new_tier, action = _next_tier(tier, profit, TIER_LIST, THRESHOLDS)
                if action == "reset":
                    tier = new_tier
                    BASE_BET, RUSH_BET = TIER_LIST[tier]
                    pos_steps = [1, 1, 1]
                    sleep_remaining = 0
                    log(f"[{account}] 利润回正，档位重置 | 当前利润={profit:+.0f} | 档{tier + 1} 一阶={BASE_BET} 二阶={RUSH_BET}")
                elif action == "upgrade":
                    crossed = THRESHOLDS[tier]
                    tier = new_tier
                    BASE_BET, RUSH_BET = TIER_LIST[tier]
                    pos_steps = [1, 1, 1]
                    sleep_remaining = SLEEPS
                    log(f"[{account}] 累计亏损{-profit:.0f}>{crossed}，升至档{tier + 1} | 一阶={BASE_BET} 二阶={RUSH_BET} | 先休眠{SLEEPS}期")

        cd = _get_countdown(page)
        if cd < 0:
            time.sleep(2)
            continue

        if pending_settlement:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] 等待开奖结算 | 投注锚点={pending_issue}，结算前不会继续下注 | 倒计时={cd}秒")
                last_heartbeat = now
            time.sleep(2)
            continue

        if WIN_MIN <= cd <= WIN_MAX and not bet_placed:
            if sleep_remaining > 0:
                sleep_remaining -= 1
                log(f"[{account}] 升档休眠中，本期跳过下注 | 剩余={sleep_remaining}期 | 档{tier + 1} {BASE_BET}/{RUSH_BET}")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + 10, stop_event)
                bet_placed = False
                continue

            targets = [sorted(random.sample(range(10), NUMBERS_PER_POS)) for _ in range(NUM_POSITIONS)]
            amounts = [RUSH_BET if pos_steps[i] == 2 else BASE_BET for i in range(NUM_POSITIONS)]

            step_info = " | ".join(
                f"球{i+1}:{'二阶赢冲' if pos_steps[i]==2 else '一阶底注'}({amounts[i]})" for i in range(NUM_POSITIONS)
            )
            prefix = f"档{tier + 1} " if conditional else ""
            phase = "模拟投注" if virtual_active else "实投下注"
            log(f"[{account}] {prefix}{phase}计划 | 选号={targets}")
            log(f"[{account}] 注码计划 | {step_info}")

            delay = random.uniform(2.0, 6.0)
            log(f"[{account}] 距封盘{cd}秒，延时{delay:.1f}秒后处理...")
            time.sleep(delay)

            if stop_event.is_set():
                break

            if _get_countdown(page) > CLOSE_BUFFER:
                anchor = read_stable_draw(page, _get_last_draw_with_issue)
                if not anchor:
                    log(f"[{account}] 警告：无法稳定读取投注锚点期号，跳过本期，避免错期结算")
                    bet_placed = True
                    remain = _get_countdown(page)
                    _sleep_interruptible((remain if remain > 0 else 30) + DRAW_DELAY, stop_event)
                    bet_placed = False
                    continue
                last_issue = anchor[0]
                if _get_countdown(page) <= CLOSE_BUFFER:
                    log(f"[{account}] 警告：已确认期号锚点，但封盘太近，取消本期")
                    continue

                if virtual_active:
                    bet_placed = True
                    pending_settlement = True
                    pending_virtual = True
                    pending_issue = anchor[0]
                    last_targets = targets
                    last_amounts = amounts
                    remain = _get_countdown(page)
                    log(f"[{account}] 本期选号 | 模拟投注 | 投注锚点={pending_issue} | {_format_rush_plan(targets, amounts, pos_steps)}")
                    log(f"[{account}] 模拟投注已记录 | 投注锚点={pending_issue} | 本期不真实下注 | 预计等待{remain + DRAW_DELAY}秒开奖结算")
                    _sleep_interruptible(remain + DRAW_DELAY, stop_event)
                    bet_placed = False
                    continue

                ok = _place_bet(page, targets, amounts, log, account)
                if ok:
                    bet_placed = True
                    pending_settlement = True
                    pending_virtual = False
                    pending_issue = anchor[0]
                    last_targets = targets
                    last_amounts = amounts
                    remain = _get_countdown(page)
                    log(f"[{account}] 本期选号 | 实投下注 | 投注锚点={pending_issue} | {_format_rush_plan(targets, amounts, pos_steps)}")
                    log(f"[{account}] 下注成功 | 投注锚点={pending_issue} | 只接受更大期号开奖结算 | 预计等待{remain + DRAW_DELAY}秒")
                    _sleep_interruptible(remain + DRAW_DELAY, stop_event)
                    bet_placed = False
                    log(f"[{account}] 新一局准备中")
            else:
                log(f"[{account}] 封盘太近，取消本期")
        elif bet_placed:
            time.sleep(3)
        else:
            now = time.time()
            if now - last_heartbeat >= 30:
                extra = f" | 虚拟累计盈亏={virtual_profit:+.2f}/-{virtual_trigger}" if virtual_active else ""
                log(f"[{account}] 等待下注窗口 | 倒计时={cd}秒 | 余额={bal:.0f}{extra}")
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

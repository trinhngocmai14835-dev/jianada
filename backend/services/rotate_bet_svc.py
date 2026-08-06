"""
轮换追损下注服务 — 3路球，每路两组号码交替轮换，追损最多N次，每路独立计算
"""
import asyncio
import sys
import re
import time
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

NUM_POSITIONS = 3


# ─── 定时启动工具 ─────────────────────────────────────────────

def _parse_hhmm(raw):
    if not isinstance(raw, str):
        return None
    m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$", raw)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def _next_alarm(hhmm, now=None):
    now = now or datetime.now()
    h, mi = hhmm
    target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _fmt_span(seconds):
    total_min = max(0, int(seconds // 60))
    h, m = divmod(total_min, 60)
    if h:
        return f"{h}小时{m:02d}分"
    return f"{m}分" if m else "不到1分"


def _wait_until_start(cfg, account, stop_event, log):
    """随开随跑直接返回；定时模式等到指定时刻再开始投注。"""
    if cfg.get("start_mode", "now") != "scheduled":
        return
    hhmm = _parse_hhmm(cfg.get("start_time"))
    if not hhmm:
        log(f"[{account}] ⚠️ 闹钟时刻无效({cfg.get('start_time')!r})，按随开随跑处理")
        return
    target = _next_alarm(hhmm)
    remain = (target - datetime.now()).total_seconds()
    log(f"[{account}] ⏰ 闹钟已设: {target:%Y-%m-%d %H:%M} 开始下注"
        f"（距现在 {_fmt_span(remain)}）| 登录已完成，待机中...")
    last_beat = time.time()
    while not stop_event.is_set():
        remain = (target - datetime.now()).total_seconds()
        if remain <= 0:
            break
        _sleep_interruptible(min(60.0, remain), stop_event)
        now_t = time.time()
        if now_t - last_beat >= 600:
            log(f"[{account}] ⏳ 距开跑还有 {_fmt_span((target - datetime.now()).total_seconds())}")
            last_beat = now_t


# ─── 每路状态 ─────────────────────────────────────────────────

class _PathState:
    """单路球的轮换 + 追损状态，每路完全独立。"""

    def __init__(self, base: int, multiplier: float, max_losses: int):
        self.base = base
        self.multiplier = multiplier
        self.max_losses = max_losses
        self.set_idx = 0          # 当前使用哪组号码（0=A组，1=B组），下注成功后翻转
        self.loss_count = 0       # 连续未中次数（满 max_losses 后自动归零）
        self.loss_history = []    # 本轮各把的实际下注额（用于计算追损额）

    def get_bet(self) -> int:
        """计算本把应下注额。"""
        if self.loss_count == 0:
            return self.base
        return max(1, round(sum(self.loss_history) * self.multiplier))

    def on_win(self):
        """命中：重置追损计数，下一把回归一阶底注。"""
        self.loss_count = 0
        self.loss_history = []

    def on_lose(self, bet: int) -> bool:
        """未中：记录本把注额，返回 True 表示已满最大次数并自动重置。"""
        self.loss_count += 1
        self.loss_history.append(bet)
        if self.loss_count >= self.max_losses:
            self.loss_count = 0
            self.loss_history = []
            return True
        return False

    def rotate(self):
        """翻转号码组（下注成功后调用，确保每把轮换）。"""
        self.set_idx ^= 1


def _parse_number_sets(raw):
    """把前端传来的三路号码配置规整成 [(set_a, set_b), ...]。
    set_a/set_b 均为 0~9 的升序去重列表，任一路非法返回 None。"""
    if not raw or len(raw) < NUM_POSITIONS:
        return None
    out = []
    try:
        for i in range(NUM_POSITIONS):
            item = raw[i]

            def _clean(lst):
                nums = sorted({int(n) for n in (lst or []) if 0 <= int(n) <= 9})
                return nums if nums else None

            a = _clean(item.get("set_a", []))
            b = _clean(item.get("set_b", []))
            if not a or not b:
                return None
            out.append((a, b))
    except (TypeError, ValueError, AttributeError, KeyError):
        return None
    return out if len(out) == NUM_POSITIONS else None


# ─── 下注循环 ─────────────────────────────────────────────────

def _betting_loop(page, account, cfg, stop_event, log):
    STOP_LOSS   = cfg.get("daily_stop_loss", 29000)
    TAKE_PROFIT = cfg.get("take_profit", 25000)
    BASE_BET    = int(cfg.get("base_bet_amount", 100))
    MULTIPLIER  = float(cfg.get("loss_multiplier", 1.3))
    MAX_LOSSES  = int(cfg.get("max_losses", 5))
    WIN_MIN     = int(cfg.get("bet_window_min", 20))
    WIN_MAX     = int(cfg.get("bet_window_max", 90))
    CLOSE_BUF   = int(cfg.get("close_buffer", 10))
    DRAW_DELAY  = int(cfg.get("draw_delay", 73))

    number_sets = _parse_number_sets(cfg.get("number_sets")) or [
        ([0, 1, 3, 5, 8], [2, 4, 6, 7, 9]) for _ in range(NUM_POSITIONS)
    ]

    paths = [_PathState(BASE_BET, MULTIPLIER, MAX_LOSSES) for _ in range(NUM_POSITIONS)]

    start_balance = _get_balance(page) or 0
    initial_draw = read_stable_draw(page, _get_last_draw_with_issue)
    last_issue = initial_draw[0] if initial_draw else None
    pending_issue = None
    if last_issue:
        log(f"[{account}] 结算锚点初始化：当前已开奖期号 {last_issue}")
    else:
        log(f"[{account}] 警告：暂未稳定读取到初始开奖期号，首次下注前会再次确认")
    bet_placed = False
    pending_settlement = False
    last_targets = [None] * NUM_POSITIONS
    last_amounts = [BASE_BET] * NUM_POSITIONS
    last_heartbeat = 0.0

    sets_desc = "  ".join(
        f"球{i+1}[A:{number_sets[i][0]} / B:{number_sets[i][1]}]"
        for i in range(NUM_POSITIONS)
    )
    log(f"[{account}] 轮换追损启动 | 底注={BASE_BET} 追损倍率={MULTIPLIER} 最大追损={MAX_LOSSES} | {sets_desc} | 起始余额={start_balance}")
    log(f"[{account}] 时间参数 | 下注窗口={WIN_MIN}-{WIN_MAX}秒 封盘缓冲>{CLOSE_BUF}秒 开奖延迟+{DRAW_DELAY}秒")

    while not stop_event.is_set():
        bal = _get_balance(page)
        if bal is None:
            time.sleep(3)
            continue

        profit = bal - start_balance

        if profit >= TAKE_PROFIT:
            log(f"[{account}] 已触发止盈：利润={profit:.0f} >= {TAKE_PROFIT}")
            break
        if profit <= -STOP_LOSS:
            log(f"[{account}] 已触发止损：利润={profit:.0f}")
            break

        res = read_stable_draw(page, _get_last_draw_with_issue)
        if res:
            issue, draw = res

            if pending_settlement:
                if pending_issue is None:
                    log(f"[{account}] 错误：缺少投注期号锚点，为避免错期结算，停止当前账号")
                    break
                if last_targets[0] is not None and is_newer_issue(issue, pending_issue):
                    gap = issue_gap(issue, pending_issue)
                    if gap is not None and gap > 1:
                        log(f"[{account}] 警告：开奖期号从 {pending_issue} 跳到 {issue}，按最新稳定开奖结果结算，请核对记录")
                    log(f"[{account}] 开奖结算 | 期号={issue} 开奖={draw} | 投注锚点={pending_issue} | 当前利润={profit:+.0f}")

                    for i in range(NUM_POSITIONS):
                        amt = last_amounts[i]
                        nums = last_targets[i]
                        hit = draw[i] in nums
                        set_label = "A" if nums == list(number_sets[i][0]) else "B"

                        if hit:
                            log(f"[{account}]   球{i+1} 开{draw[i]}【中奖】| 投{set_label}组{nums} | 本球注码={amt} -> 下把回到底注")
                            paths[i].on_win()
                        else:
                            reset = paths[i].on_lose(amt)
                            if reset:
                                log(f"[{account}]   球{i+1} 开{draw[i]}【未中】| 投{set_label}组{nums} | 本球注码={amt} -> 已满{MAX_LOSSES}把，重置到底注")
                            else:
                                log(f"[{account}]   球{i+1} 开{draw[i]}【未中】| 投{set_label}组{nums} | 本球注码={amt} -> 第{paths[i].loss_count}次追损，下把注码={paths[i].get_bet()}")
                    pending_settlement = False
                    pending_issue = None
                    last_issue = issue
            elif issue != last_issue:
                log(f"[{account}] 观察到新开奖 | 期号={issue} 开奖={draw} | 当前利润={profit:+.0f}")
                last_issue = issue

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
            targets = [list(number_sets[i][paths[i].set_idx]) for i in range(NUM_POSITIONS)]
            amounts = [paths[i].get_bet() for i in range(NUM_POSITIONS)]

            step_info = "  ".join(
                f"球{i+1}{'A' if paths[i].set_idx==0 else 'B'}组{targets[i]}×{amounts[i]}元"
                + (f"(第{paths[i].loss_count+1}把追损)" if paths[i].loss_count > 0 else "(底注)")
                for i in range(NUM_POSITIONS)
            )
            log(f"[{account}] 下注计划 | {step_info}")

            delay = random.uniform(2.0, 6.0)
            log(f"[{account}] 距封盘{cd}秒，延时{delay:.1f}秒后下注...")
            time.sleep(delay)

            if stop_event.is_set():
                break

            if _get_countdown(page) > CLOSE_BUF:
                anchor = read_stable_draw(page, _get_last_draw_with_issue)
                if not anchor:
                    log(f"[{account}] 警告：无法稳定读取投注锚点期号，跳过本期，避免错期结算")
                    bet_placed = True
                    remain = _get_countdown(page)
                    _sleep_interruptible((remain if remain > 0 else 30) + DRAW_DELAY, stop_event)
                    bet_placed = False
                    continue
                last_issue = anchor[0]
                if _get_countdown(page) <= CLOSE_BUF:
                    log(f"[{account}] 警告：已确认期号锚点，但封盘太近，取消本期")
                    continue
                ok = _place_bet(page, targets, amounts, log, account)
                if ok:
                    bet_placed = True
                    pending_settlement = True
                    pending_issue = anchor[0]
                    last_targets = targets
                    last_amounts = amounts
                    for p in paths:
                        p.rotate()
                    remain = _get_countdown(page)
                    log(f"[{account}] 下注成功 | 投注锚点={pending_issue} | 只接受更大期号开奖结算 | 预计等待{remain + DRAW_DELAY}秒")
                    _sleep_interruptible(remain + DRAW_DELAY, stop_event)
                    bet_placed = False
            else:
                log(f"[{account}] 封盘太近，取消本期")
        else:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] 等待下注窗口 | 倒计时={cd}秒 | 余额={bal:.0f}")
                last_heartbeat = now
            time.sleep(5)

    log(f"[{account}] 轮换追损循环已停止")

def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg):
        log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})

    accounts = config.get("accounts", [])
    if not accounts:
        log("❌ 未配置账号，请先添加账号")
        return

    entry_url   = config.get("entry_url", "https://166.tt")
    safe_code   = config.get("safe_code", "")
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
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    account  = acc_info.get("account", "")
    password = acc_info.get("password", "")
    port     = acc_info.get("port", 9222)

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
                _wait_until_start(config, account, stop_event, log)
                if not stop_event.is_set():
                    _betting_loop(login_page, account, config, stop_event, log)
            except Exception as e:
                log(f"[{account}] ❌ 运行异常: {e}")
            finally:
                browser.close()
                log(f"[{account}] 浏览器已关闭")
    except Exception as e:
        log(f"[{account}] ❌ 启动失败: {e}")

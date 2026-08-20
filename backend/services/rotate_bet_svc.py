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
    """单路球的轮换、入场观察和追损状态，每路完全独立。"""

    def __init__(self, base: int, multiplier: float, max_losses: int, entry_miss_trigger: int = 1):
        self.base = base
        self.multiplier = multiplier
        self.max_losses = max_losses
        self.entry_miss_trigger = max(0, int(entry_miss_trigger))
        self.active = self.entry_miss_trigger == 0
        self.entry_loss_count = 0  # 入场前连续未中观察次数，达到 entry_miss_trigger 后开始实投
        self.set_idx = 0           # 当前使用哪组号码（0=A组，1=B组），每期分配后翻转
        self.loss_count = 0        # 实投后连续未中次数（满 max_losses 后自动归零）
        self.loss_history = []     # 实投本轮各把的实际下注额（用于计算追损额）

    def get_bet(self) -> int:
        """计算本把应下注额。"""
        if self.loss_count == 0:
            return self.base
        return max(1, round(sum(self.loss_history) * self.multiplier))

    def observe_entry(self, hit: bool) -> bool:
        """入场观察；返回 True 表示本路刚达到触发条件。"""
        if self.active:
            return False
        if hit:
            self.entry_loss_count = 0
            return False
        self.entry_loss_count += 1
        if self.entry_loss_count >= self.entry_miss_trigger:
            self.active = True
            self.entry_loss_count = 0
            self.loss_count = 0
            self.loss_history = []
            return True
        return False

    def on_win(self):
        """命中：清空追损；非直接开始模式下重新回到入场观察。"""
        self.loss_count = 0
        self.loss_history = []
        self.entry_loss_count = 0
        self.active = self.entry_miss_trigger == 0

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
        """翻转号码组，确保每期 A/B 轮换。"""
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


def _parse_entry_miss_trigger(raw):
    """入场触发次数：0=立即实投；1/2/3=连续观察未中后实投。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 1
    if value < 0:
        return 1
    return min(value, 3)


def _parse_enabled_positions(raw):
    if not isinstance(raw, (list, tuple)):
        return [True] * NUM_POSITIONS

    disabled_values = {"0", "false", "off", "no", "disabled", "停用", "关闭"}
    out = []
    for i in range(NUM_POSITIONS):
        if i >= len(raw):
            out.append(True)
            continue
        value = raw[i]
        if isinstance(value, bool):
            out.append(value)
        elif isinstance(value, (int, float)):
            out.append(value != 0)
        elif isinstance(value, str):
            out.append(value.strip().lower() not in disabled_values)
        else:
            out.append(value is not False)
    return out


def _observe_entry_draw(paths, number_sets, draw, account, log, targets=None, rotate_after=False, enabled_positions=None):
    """对尚未实投的球路做入场观察；每路独立累计连续未中。"""
    enabled_positions = enabled_positions or [True] * NUM_POSITIONS
    activated = []
    for i in range(NUM_POSITIONS):
        if not enabled_positions[i]:
            continue
        path = paths[i]
        if path.active:
            continue
        nums = list(targets[i]) if targets is not None else list(number_sets[i][path.set_idx])
        set_label = "A" if nums == list(number_sets[i][0]) else "B"
        hit = draw[i] in nums
        before = path.entry_loss_count
        ready = path.observe_entry(hit)

        if hit:
            suffix = "，观察计数清零" if before else f"，等待连续{path.entry_miss_trigger}次未中"
            log(f"[{account}]   球{i+1} 入场观察 | 开{draw[i]}【命中】| {set_label}组{nums}{suffix}")
        elif ready:
            activated.append(i)
            log(f"[{account}]   球{i+1} 入场观察 | 开{draw[i]}【未中】| {set_label}组{nums} -> 已达连续{path.entry_miss_trigger}次未中，下期开始实投")
        else:
            log(f"[{account}]   球{i+1} 入场观察 | 开{draw[i]}【未中】| {set_label}组{nums} -> 已累计{path.entry_loss_count}/{path.entry_miss_trigger}")

        if rotate_after:
            path.rotate()
    return activated

# ─── 下注循环 ─────────────────────────────────────────────────

def _betting_loop(page, account, cfg, stop_event, log):
    STOP_LOSS   = cfg.get("daily_stop_loss", 29000)
    TAKE_PROFIT = cfg.get("take_profit", 25000)
    BASE_BET    = int(cfg.get("base_bet_amount", 100))
    MULTIPLIER  = float(cfg.get("loss_multiplier", 1.3))
    MAX_LOSSES  = int(cfg.get("max_losses", 5))
    ENTRY_MISSES = _parse_entry_miss_trigger(cfg.get("entry_miss_trigger", 1))
    ENABLED_POSITIONS = _parse_enabled_positions(cfg.get("enabled_positions"))
    WIN_MIN     = int(cfg.get("bet_window_min", 20))
    WIN_MAX     = int(cfg.get("bet_window_max", 90))
    CLOSE_BUF   = int(cfg.get("close_buffer", 10))
    DRAW_DELAY  = int(cfg.get("draw_delay", 73))
    SETTLE_EARLY = max(0, int(cfg.get("settlement_wake_early", 8)))

    number_sets = _parse_number_sets(cfg.get("number_sets")) or [
        ([0, 1, 3, 5, 8], [2, 4, 6, 7, 9]) for _ in range(NUM_POSITIONS)
    ]

    paths = [_PathState(BASE_BET, MULTIPLIER, MAX_LOSSES, ENTRY_MISSES) for _ in range(NUM_POSITIONS)]
    if not any(ENABLED_POSITIONS):
        log(f"[{account}] 错误：至少需要启用一路球，当前三路都已关闭")
        return

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
    last_bet_active = [False] * NUM_POSITIONS
    last_heartbeat = 0.0

    sets_desc = "  ".join(
        f"球{i+1}[已关闭]" if not ENABLED_POSITIONS[i]
        else f"球{i+1}[A:{number_sets[i][0]} / B:{number_sets[i][1]}]"
        for i in range(NUM_POSITIONS)
    )
    trigger_desc = "立即实投" if ENTRY_MISSES == 0 else f"连续{ENTRY_MISSES}次未中后实投"
    log(f"[{account}] 轮换追损启动 | 底注={BASE_BET} 追损倍率={MULTIPLIER} 最大追损={MAX_LOSSES} 入场={trigger_desc} | {sets_desc} | 起始余额={start_balance}")
    log(f"[{account}] 时间参数 | 下注窗口<= {WIN_MAX}秒 无最低安全线/无封盘缓冲 开奖延迟+{DRAW_DELAY}秒 提前轮询结算={SETTLE_EARLY}秒")

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

                    won_positions = [False] * NUM_POSITIONS
                    for i in range(NUM_POSITIONS):
                        if not ENABLED_POSITIONS[i]:
                            continue
                        if not last_bet_active[i]:
                            continue
                        amt = last_amounts[i]
                        nums = last_targets[i]
                        hit = draw[i] in nums
                        set_label = "A" if nums == list(number_sets[i][0]) else "B"

                        if hit:
                            paths[i].on_win()
                            won_positions[i] = not paths[i].active
                            next_desc = "下期继续底注实投" if paths[i].active else f"回到入场观察，重新等待连续{paths[i].entry_miss_trigger}次未中"
                            log(f"[{account}]   球{i+1} 开{draw[i]}【中奖】| 投{set_label}组{nums} | 本球注码={amt} -> {next_desc}")
                        else:
                            reset = paths[i].on_lose(amt)
                            if reset:
                                log(f"[{account}]   球{i+1} 开{draw[i]}【未中】| 投{set_label}组{nums} | 本球注码={amt} -> 已满{MAX_LOSSES}把，重置到底注")
                            else:
                                log(f"[{account}]   球{i+1} 开{draw[i]}【未中】| 投{set_label}组{nums} | 本球注码={amt} -> 第{paths[i].loss_count}次追损，下把注码={paths[i].get_bet()}")
                    observe_enabled = [ENABLED_POSITIONS[i] and not won_positions[i] for i in range(NUM_POSITIONS)]
                    _observe_entry_draw(paths, number_sets, draw, account, log, targets=last_targets, rotate_after=False, enabled_positions=observe_enabled)
                    pending_settlement = False
                    pending_issue = None
                    last_issue = issue
            elif issue != last_issue:
                log(f"[{account}] 观察到新开奖 | 期号={issue} 开奖={draw} | 当前利润={profit:+.0f}")
                _observe_entry_draw(paths, number_sets, draw, account, log, rotate_after=True, enabled_positions=ENABLED_POSITIONS)
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
            time.sleep(0.5 if cd <= WIN_MAX else 1.0)
            continue

        if 0 <= cd <= WIN_MAX and not bet_placed:
            targets = [list(number_sets[i][paths[i].set_idx]) for i in range(NUM_POSITIONS)]
            active_mask = [ENABLED_POSITIONS[i] and paths[i].active for i in range(NUM_POSITIONS)]
            amounts = [paths[i].get_bet() if active_mask[i] else 0 for i in range(NUM_POSITIONS)]

            if not any(active_mask):
                observe_info = "  ".join(
                    f"球{i+1}{'A' if paths[i].set_idx==0 else 'B'}组{targets[i]}(观察{paths[i].entry_loss_count}/{paths[i].entry_miss_trigger})"
                    for i in range(NUM_POSITIONS)
                    if ENABLED_POSITIONS[i]
                )
                log(f"[{account}] 入场观察中 | {observe_info} | 本期不下注")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + DRAW_DELAY, stop_event)
                bet_placed = False
                continue

            step_parts = []
            for i in range(NUM_POSITIONS):
                if not ENABLED_POSITIONS[i]:
                    step_parts.append(f"球{i+1}已关闭")
                    continue
                label = "A" if paths[i].set_idx == 0 else "B"
                if active_mask[i]:
                    step_parts.append(
                        f"球{i+1}{label}组{targets[i]}×{amounts[i]}元"
                        + (f"(第{paths[i].loss_count+1}把追损)" if paths[i].loss_count > 0 else "(底注)")
                    )
                else:
                    step_parts.append(f"球{i+1}{label}组{targets[i]}(观察{paths[i].entry_loss_count}/{paths[i].entry_miss_trigger})")
            step_info = "  ".join(step_parts)
            log(f"[{account}] 下注计划 | {step_info}")

            log(f"[{account}] 距封盘{cd}秒，立即下注...")

            if stop_event.is_set():
                break

            anchor = read_stable_draw(page, _get_last_draw_with_issue)
            if not anchor:
                log(f"[{account}] 警告：无法稳定读取投注锚点期号，跳过本期，避免错期结算")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + DRAW_DELAY, stop_event)
                bet_placed = False
                continue
            last_issue = anchor[0]
            bet_targets = [targets[i] if active_mask[i] else [] for i in range(NUM_POSITIONS)]
            ok = _place_bet(page, bet_targets, amounts, log, account)
            if ok:
                bet_placed = True
                pending_settlement = True
                pending_issue = anchor[0]
                last_targets = targets
                last_amounts = amounts
                last_bet_active = active_mask
                for i, p in enumerate(paths):
                    if ENABLED_POSITIONS[i]:
                        p.rotate()
                remain = _get_countdown(page)
                wait_seconds = max(0, remain) + max(0, DRAW_DELAY - SETTLE_EARLY)
                log(f"[{account}] 下注成功 | 投注锚点={pending_issue} | 已实投球路={[i+1 for i, active in enumerate(active_mask) if active]} | 只接受更大期号开奖结算 | 预计{wait_seconds}秒后开始轮询结算")
                _sleep_interruptible(wait_seconds, stop_event)
                bet_placed = False
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

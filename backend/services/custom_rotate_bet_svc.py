"""Independent custom-amount rotate-loss betting service."""
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
    _free_port,
    _get_balance,
    _get_chrome,
    _get_countdown,
    _get_last_draw_with_issue,
    _login,
    _place_bet,
    _sleep_interruptible,
)
from services.rotate_bet_svc import (
    NUM_POSITIONS,
    _next_alarm,
    _parse_enabled_positions,
    _parse_entry_miss_trigger,
    _parse_hhmm,
    _wait_until_start,
)
from services.settlement_guard import issue_gap, is_newer_issue, read_stable_draw


DEFAULT_NUMBER_SETS = [
    ([0, 1, 3, 5, 8], [2, 4, 6, 7, 9]) for _ in range(NUM_POSITIONS)
]
DEFAULT_AMOUNT_STEPS = [100, 130, 299, 389, 506]

_STATUS_LOCK = threading.Lock()
_ACCOUNT_STATUS: dict[str, dict] = {}
_ACCOUNT_STOPS: dict[str, threading.Event] = {}
_MANUALLY_STOPPED: set[str] = set()
_BLOCKED_REASONS: dict[str, str] = {}


class _CombinedStop:
    def __init__(self, parent: threading.Event, child: threading.Event):
        self.parent = parent
        self.child = child

    def is_set(self):
        return self.parent.is_set() or self.child.is_set()

    def set(self):
        self.child.set()


class _CustomAmountPathState:
    """State for one ball path in fixed custom amount mode."""

    def __init__(self, amount_steps: list[int], entry_miss_trigger: int = 1):
        self.amount_steps = [max(1, int(v)) for v in amount_steps[:20]] or DEFAULT_AMOUNT_STEPS[:]
        self.entry_miss_trigger = max(0, int(entry_miss_trigger))
        self.active = self.entry_miss_trigger == 0
        self.entry_loss_count = 0
        self.set_idx = 0
        self.tier_index = 0

    def get_bet(self) -> int:
        return self.amount_steps[min(self.tier_index, len(self.amount_steps) - 1)]

    def observe_entry(self, hit: bool) -> bool:
        if self.active:
            return False
        if hit:
            self.entry_loss_count = 0
            return False
        self.entry_loss_count += 1
        if self.entry_loss_count >= self.entry_miss_trigger:
            self.active = True
            self.entry_loss_count = 0
            self.tier_index = 0
            return True
        return False

    def on_win(self):
        self.tier_index = 0
        self.entry_loss_count = 0
        self.active = self.entry_miss_trigger == 0

    def on_lose(self) -> bool:
        if self.tier_index >= len(self.amount_steps) - 1:
            self.tier_index = 0
            return True
        self.tier_index += 1
        return False

    def rotate(self):
        self.set_idx ^= 1


def _get_settled_balance(page, samples=3, interval=0.35) -> float | None:
    """Read the balance after settlement has propagated to the page."""
    values = []
    for index in range(max(1, int(samples))):
        value = _get_balance(page)
        if value is not None:
            values.append(value)
        if index + 1 < samples:
            time.sleep(max(0, float(interval)))
    if not values:
        return None
    values.sort()
    return values[len(values) // 2]


def _account_key(acc_info: dict) -> str:
    account = str(acc_info.get("account", "")).strip()
    port = str(acc_info.get("port", "")).strip()
    if account and port:
        return f"{account}@{port}"
    return account or port


def _account_resources(acc_info: dict) -> dict[str, set[str]]:
    account = str(acc_info.get("account", "")).strip().lower()
    port = str(acc_info.get("port", "")).strip()
    return {
        "accounts": {account} if account else set(),
        "ports": {port} if port else set(),
    }


def _set_account_status(key: str, **updates):
    with _STATUS_LOCK:
        current = _ACCOUNT_STATUS.get(key, {"key": key})
        current.update(updates)
        current["key"] = key
        _ACCOUNT_STATUS[key] = current


def get_account_statuses():
    with _STATUS_LOCK:
        return list(_ACCOUNT_STATUS.values())


def _resolve_status_key(raw: str):
    raw = str(raw or "").strip()
    with _STATUS_LOCK:
        if raw in _ACCOUNT_STATUS:
            return raw
        for key, status in _ACCOUNT_STATUS.items():
            account = str(status.get("account", "")).strip()
            port = str(status.get("port", "")).strip()
            if raw and raw in {account, port}:
                return key
    return raw


def stop_account(key: str):
    key = _resolve_status_key(key)
    with _STATUS_LOCK:
        stop = _ACCOUNT_STOPS.get(key)
        if not stop:
            return False, "账号任务不存在或已停止"
        stop.set()
        _MANUALLY_STOPPED.add(key)
        status = _ACCOUNT_STATUS.get(key, {"key": key})
        status["status"] = "stopping"
        status["message"] = "正在单独停止..."
        _ACCOUNT_STATUS[key] = status
    return True, "正在单独停止账号..."


def _parse_amount_steps(raw, fallback_base=100):
    if isinstance(raw, str):
        raw = re.split(r"[\s,，]+", raw.strip())
    if not isinstance(raw, (list, tuple)):
        raw = [fallback_base]
    out = []
    for item in raw[:20]:
        try:
            value = int(float(item))
        except (TypeError, ValueError):
            continue
        if value > 0:
            out.append(value)
    return out or [max(1, int(fallback_base or 100))]


def _clean_nums(raw):
    if isinstance(raw, str):
        raw = re.split(r"[\s,，]+", raw.strip())
    out = []
    for item in raw or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 9 and value not in out:
            out.append(value)
    return out


def _parse_custom_number_sets(raw):
    if not raw or len(raw) < NUM_POSITIONS:
        return None
    out = []
    try:
        for i in range(NUM_POSITIONS):
            item = raw[i]
            a = _clean_nums(item.get("set_a", []))
            b = _clean_nums(item.get("set_b", []))
            if len(a) not in (4, 5) or len(b) not in (4, 5):
                return None
            out.append((a, b))
    except (TypeError, AttributeError):
        return None
    return out if len(out) == NUM_POSITIONS else None


def _observe_entry_draw(paths, number_sets, draw, account, log, targets=None, rotate_after=False, enabled_positions=None):
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
            log(f"[{account}]   球{i+1} 入场观察 | 开{draw[i]}【命中】 {set_label}组{nums}{suffix}")
        elif ready:
            activated.append(i)
            log(f"[{account}]   球{i+1} 入场观察 | 开{draw[i]}【未中】 {set_label}组{nums} -> 已达连续{path.entry_miss_trigger}次未中，下期开始实投")
        else:
            log(f"[{account}]   球{i+1} 入场观察 | 开{draw[i]}【未中】 {set_label}组{nums} -> 已累计{path.entry_loss_count}/{path.entry_miss_trigger}")

        if rotate_after:
            path.rotate()
    return activated


def _wait_for_next_cycle(page, account, stop_event, log):
    anchor = read_stable_draw(page, _get_last_draw_with_issue)
    if not anchor:
        log(f"[{account}] 新增账号未读到当前期号，先进入下一轮循环保护")
        _sleep_interruptible(5, stop_event)
        return

    anchor_issue = anchor[0]
    log(f"[{account}] 新增账号已登录，等待当前期 {anchor_issue} 结束，下一完整周期再加入")
    last_beat = time.time()
    while not stop_event.is_set():
        res = read_stable_draw(page, _get_last_draw_with_issue)
        if res and is_newer_issue(res[0], anchor_issue):
            log(f"[{account}] 已进入新周期 {res[0]}，开始执行自定义金额轮换追损")
            return
        now = time.time()
        if now - last_beat >= 30:
            cd = _get_countdown(page)
            log(f"[{account}] 等待下一完整周期 | 当前期={anchor_issue} | 倒计时={cd}秒")
            last_beat = now
        _sleep_interruptible(2, stop_event)


def _betting_loop(page, account, cfg, stop_event, log):
    stop_loss = cfg.get("daily_stop_loss", 29000)
    take_profit = cfg.get("take_profit", 25000)
    base_bet = int(cfg.get("base_bet_amount", 100))
    amount_steps = _parse_amount_steps(cfg.get("amount_steps"), base_bet)
    entry_misses = _parse_entry_miss_trigger(cfg.get("entry_miss_trigger", 1))
    enabled_positions = _parse_enabled_positions(cfg.get("enabled_positions"))
    win_max = int(cfg.get("bet_window_max", 90))
    draw_delay = int(cfg.get("draw_delay", 73))
    settle_early = max(0, int(cfg.get("settlement_wake_early", 8)))

    number_sets = _parse_custom_number_sets(cfg.get("number_sets")) or DEFAULT_NUMBER_SETS
    paths = [_CustomAmountPathState(amount_steps, entry_misses) for _ in range(NUM_POSITIONS)]
    if not any(enabled_positions):
        log(f"[{account}] 错误：至少需要启用一路球，当前三路都已关闭")
        return

    start_balance = _get_settled_balance(page) or _get_balance(page) or 0
    settled_balance = start_balance
    initial_draw = read_stable_draw(page, _get_last_draw_with_issue)
    last_issue = initial_draw[0] if initial_draw else None
    pending_issue = None
    pending_settlement = False
    bet_placed = False
    last_targets = [None] * NUM_POSITIONS
    last_amounts = [0] * NUM_POSITIONS
    last_bet_active = [False] * NUM_POSITIONS
    last_heartbeat = 0.0

    sets_desc = "  ".join(
        f"球{i+1}[已关闭]" if not enabled_positions[i]
        else f"球{i+1}[A:{number_sets[i][0]} / B:{number_sets[i][1]}]"
        for i in range(NUM_POSITIONS)
    )
    trigger_desc = "立即实投" if entry_misses == 0 else f"连续{entry_misses}次未中后实投"
    log(f"[{account}] 自定义金额轮换追损启动 | 阶梯金额={amount_steps} 入场={trigger_desc} | {sets_desc} | 起始余额={start_balance}")
    if last_issue:
        log(f"[{account}] 结算锚点初始化：当前已开奖期号 {last_issue}")
    else:
        log(f"[{account}] 警告：暂未稳定读到初始开奖期号，首次下注前会再次确认")

    while not stop_event.is_set():
        bal = _get_balance(page)
        if bal is None:
            time.sleep(3)
            continue

        if not pending_settlement:
            stable_balance = _get_settled_balance(page, samples=2, interval=0.2)
            if stable_balance is not None:
                settled_balance = stable_balance
        profit = settled_balance - start_balance
        if profit >= take_profit:
            log(f"[{account}] 已触发止盈：利润={profit:.0f} >= {take_profit}")
            break
        if profit <= -stop_loss:
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
                        if not enabled_positions[i] or not last_bet_active[i]:
                            continue
                        amount = last_amounts[i]
                        nums = last_targets[i]
                        hit = draw[i] in nums
                        set_label = "A" if nums == list(number_sets[i][0]) else "B"

                        if hit:
                            paths[i].on_win()
                            won_positions[i] = not paths[i].active
                            next_desc = "下一期继续第一阶实投" if paths[i].active else f"回到入场观察，重新等待连续{paths[i].entry_miss_trigger}次未中"
                            log(f"[{account}]   球{i+1} 开{draw[i]}【中奖】 投{set_label}组{nums} | 本球注码={amount} -> {next_desc}")
                        else:
                            reset = paths[i].on_lose()
                            if reset:
                                log(f"[{account}]   球{i+1} 开{draw[i]}【未中】 投{set_label}组{nums} | 本球注码={amount} -> 最后一阶未中，回到第一阶，下一把注码={paths[i].get_bet()}")
                            else:
                                log(f"[{account}]   球{i+1} 开{draw[i]}【未中】 投{set_label}组{nums} | 本球注码={amount} -> 进入第{paths[i].tier_index + 1}阶，下一把注码={paths[i].get_bet()}")

                    observe_enabled = [enabled_positions[i] and not won_positions[i] for i in range(NUM_POSITIONS)]
                    _observe_entry_draw(paths, number_sets, draw, account, log, targets=last_targets, enabled_positions=observe_enabled)
                    pending_settlement = False
                    pending_issue = None
                    last_issue = issue
            elif issue != last_issue:
                log(f"[{account}] 观察到新开奖 | 期号={issue} 开奖={draw} | 当前利润={profit:+.0f}")
                _observe_entry_draw(paths, number_sets, draw, account, log, rotate_after=True, enabled_positions=enabled_positions)
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
            time.sleep(0.5 if cd <= win_max else 1.0)
            continue

        if 0 <= cd <= win_max and not bet_placed:
            targets = [list(number_sets[i][paths[i].set_idx]) for i in range(NUM_POSITIONS)]
            active_mask = [enabled_positions[i] and paths[i].active for i in range(NUM_POSITIONS)]
            amounts = [paths[i].get_bet() if active_mask[i] else 0 for i in range(NUM_POSITIONS)]

            if not any(active_mask):
                observe_info = "  ".join(
                    f"球{i+1}{'A' if paths[i].set_idx == 0 else 'B'}组{targets[i]}(观察{paths[i].entry_loss_count}/{paths[i].entry_miss_trigger})"
                    for i in range(NUM_POSITIONS)
                    if enabled_positions[i]
                )
                log(f"[{account}] 入场观察中 | {observe_info} | 本期不下注")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + draw_delay, stop_event)
                bet_placed = False
                continue

            parts = []
            for i in range(NUM_POSITIONS):
                if not enabled_positions[i]:
                    parts.append(f"球{i+1}已关闭")
                    continue
                label = "A" if paths[i].set_idx == 0 else "B"
                if active_mask[i]:
                    parts.append(f"球{i+1}{label}组{targets[i]}x{amounts[i]}元 第{paths[i].tier_index + 1}阶")
                else:
                    parts.append(f"球{i+1}{label}组{targets[i]}(观察{paths[i].entry_loss_count}/{paths[i].entry_miss_trigger})")
            log(f"[{account}] 下注计划 | {'  '.join(parts)}")
            log(f"[{account}] 距封盘{cd}秒，立即下注...")

            anchor = read_stable_draw(page, _get_last_draw_with_issue)
            if not anchor:
                log(f"[{account}] 警告：无法稳定读取投注锚点期号，跳过本期，避免错期结算")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + draw_delay, stop_event)
                bet_placed = False
                continue

            bet_targets = [targets[i] if active_mask[i] else [] for i in range(NUM_POSITIONS)]
            ok = _place_bet(page, bet_targets, amounts, log, account)
            if ok:
                bet_placed = True
                pending_settlement = True
                pending_issue = anchor[0]
                last_issue = anchor[0]
                last_targets = targets
                last_amounts = amounts
                last_bet_active = active_mask
                for i, path in enumerate(paths):
                    if enabled_positions[i]:
                        path.rotate()
                remain = _get_countdown(page)
                wait_seconds = max(0, remain) + max(0, draw_delay - settle_early)
                log(f"[{account}] 下注成功 | 投注锚点={pending_issue} | 已实投球路={[i+1 for i, active in enumerate(active_mask) if active]} | 预计{wait_seconds}秒后开始轮询结算")
                _sleep_interruptible(wait_seconds, stop_event)
                bet_placed = False
        else:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] 等待下注窗口 | 倒计时={cd}秒 | 余额={bal:.0f}")
                last_heartbeat = now
            time.sleep(5)

    log(f"[{account}] 自定义金额轮换追损循环已停止")


def _scheduled_target(config):
    if config.get("start_mode") != "scheduled":
        return None
    hhmm = _parse_hhmm(config.get("start_time"))
    return _next_alarm(hhmm) if hhmm else None


def _external_conflict(acc_info: dict):
    try:
        from core.task_manager import TaskManager

        requested = _account_resources(acc_info)
        occupied = TaskManager.get().active_resources(exclude_task_id="custom_rotatebet")
    except Exception:
        return None

    for port in requested["ports"]:
        owner = occupied.get("ports", {}).get(port)
        if owner:
            return f"端口 {port} 已被 {owner} 使用"
    for account in requested["accounts"]:
        owner = occupied.get("accounts", {}).get(account)
        if owner:
            return f"账号 {account} 已被 {owner} 使用"
    return None


def _run_account(acc_info, config, entry_url, safe_code, chrome_path, parent_stop, child_stop, log, wait_for_next_cycle=False):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    key = _account_key(acc_info)
    account = str(acc_info.get("account", "")).strip()
    password = acc_info.get("password", "")
    port = acc_info.get("port", 9222)
    stop_event = _CombinedStop(parent_stop, child_stop)
    _set_account_status(
        key,
        account=account,
        port=port,
        status="starting",
        message="正在启动",
        started_at=datetime.now().isoformat(),
    )

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
                _set_account_status(key, status="waiting", message="已登录，等待开跑")
                _wait_until_start(config, account, stop_event, log)
                if wait_for_next_cycle and not stop_event.is_set():
                    _set_account_status(key, status="waiting", message="等待下一完整周期")
                    _wait_for_next_cycle(login_page, account, stop_event, log)
                if not stop_event.is_set():
                    _set_account_status(key, status="running", message="运行中")
                    _betting_loop(login_page, account, config, stop_event, log)
            except Exception as e:
                _set_account_status(key, status="error", message=str(e))
                log(f"[{account}] 运行异常: {e}")
            finally:
                browser.close()
                log(f"[{account}] 浏览器已关闭")
    except Exception as e:
        _set_account_status(key, status="error", message=str(e))
        log(f"[{account}] 启动失败: {e}")
    finally:
        with _STATUS_LOCK:
            _ACCOUNT_STOPS.pop(key, None)
            current = _ACCOUNT_STATUS.get(key, {"key": key})
            if current.get("status") != "error":
                current["status"] = "stopped"
                current["message"] = "已停止"
                _ACCOUNT_STATUS[key] = current


def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg):
        log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})

    with _STATUS_LOCK:
        _ACCOUNT_STATUS.clear()
        _ACCOUNT_STOPS.clear()
        _MANUALLY_STOPPED.clear()
        _BLOCKED_REASONS.clear()

    initial_keys = {
        _account_key(acc_info)
        for acc_info in (config.get("accounts") or [])
        if isinstance(acc_info, dict) and _account_key(acc_info)
    }
    schedule_target = _scheduled_target(config)
    chrome_path = _get_chrome()
    threads: dict[str, threading.Thread] = {}

    while not stop_event.is_set():
        latest = get_config("custom_rotatebet_config", config)
        if not isinstance(latest, dict):
            latest = config
        latest = {**config, **latest}

        try:
            from core.task_manager import TaskManager

            TaskManager.get().update_config("custom_rotatebet", latest)
        except Exception:
            pass

        accounts = latest.get("accounts", []) if isinstance(latest, dict) else []
        if not accounts:
            log("未配置账号，请先添加账号")
            _sleep_interruptible(5, stop_event)
            continue

        running_accounts = set()
        running_ports = set()
        for key, thread in list(threads.items()):
            if thread.is_alive():
                status = _ACCOUNT_STATUS.get(key, {})
                account = str(status.get("account", "")).strip().lower()
                port = str(status.get("port", "")).strip()
                if account:
                    running_accounts.add(account)
                if port:
                    running_ports.add(port)
            else:
                thread.join(timeout=0.1)

        for acc_info in accounts:
            if not isinstance(acc_info, dict):
                continue
            key = _account_key(acc_info)
            if not key or key in _MANUALLY_STOPPED:
                continue
            thread = threads.get(key)
            if thread and thread.is_alive():
                continue

            resources = _account_resources(acc_info)
            account = next(iter(resources["accounts"]), "")
            port = next(iter(resources["ports"]), "")
            if account and account in running_accounts:
                reason = f"账号 {account} 已在自定义模式中运行"
            elif port and port in running_ports:
                reason = f"端口 {port} 已在自定义模式中运行"
            else:
                reason = _external_conflict(acc_info)
            if reason:
                if _BLOCKED_REASONS.get(key) != reason:
                    log(f"[{acc_info.get('account', '')}] 跳过启动：{reason}")
                    _BLOCKED_REASONS[key] = reason
                _set_account_status(
                    key,
                    account=acc_info.get("account", ""),
                    port=acc_info.get("port", ""),
                    status="blocked",
                    message=reason,
                )
                continue
            _BLOCKED_REASONS.pop(key, None)

            child_stop = threading.Event()
            with _STATUS_LOCK:
                _ACCOUNT_STOPS[key] = child_stop

            account_config = dict(latest)
            wait_schedule = schedule_target is not None and datetime.now() < schedule_target
            if not wait_schedule:
                account_config["start_mode"] = "now"
            wait_next_cycle = key not in initial_keys
            t = threading.Thread(
                target=_run_account,
                args=(
                    dict(acc_info),
                    account_config,
                    account_config.get("entry_url", "https://166.tt"),
                    account_config.get("safe_code", ""),
                    chrome_path,
                    stop_event,
                    child_stop,
                    log,
                    wait_next_cycle,
                ),
                daemon=True,
                name=f"custom-rotatebet-{key}",
            )
            threads[key] = t
            t.start()
            if wait_next_cycle:
                log(f"[{acc_info.get('account', '')}] 新账号会话已加入，登录后等待下一完整周期再下注")
            time.sleep(5)

        _sleep_interruptible(5, stop_event)

    with _STATUS_LOCK:
        for child_stop in _ACCOUNT_STOPS.values():
            child_stop.set()
    for thread in list(threads.values()):
        thread.join()
    log("自定义金额轮换追损所有账号线程已结束")

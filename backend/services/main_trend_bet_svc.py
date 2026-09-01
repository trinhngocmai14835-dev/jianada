"""Main-trend big/small odd/even rotate-loss betting service."""
import asyncio
import json
import queue
import re
import sys
import threading
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

from core.db import get_config
from services.auto_bet_svc import (
    _get_balance,
    _get_chrome,
    _get_countdown,
    _get_last_draw_with_issue,
    _login,
    _sleep_interruptible,
)
from services.custom_rotate_bet_svc import (
    _CombinedStop,
    _CustomAmountPathState,
    _account_key,
    _account_resources,
    _friendly_account_error,
    _get_settled_balance,
    _parse_amount_steps,
    _prepare_launch_port,
    _scheduled_target,
)
from services.rotate_bet_svc import _parse_entry_miss_trigger, _wait_until_start
from services.settlement_guard import issue_gap, is_newer_issue, read_stable_draw


TASK_ID = "main_trend_bet"
CONFIG_KEY = "main_trend_bet_config"
PATH_COUNT = 2
PATH_LABELS = ["大小路", "单双路"]
PATH_TARGETS = [("大", "小"), ("单", "双")]
DEFAULT_AMOUNT_STEPS = [100, 130, 299, 389, 506]
TARGET_INPUT_IDS = {"大": "odds_DX1", "小": "odds_DX2", "单": "odds_DS3", "双": "odds_DS4"}
TARGET_ODDS_IDS = {"大": "oddsValue_DX1", "小": "oddsValue_DX2", "单": "oddsValue_DS3", "双": "oddsValue_DS4"}

_STATUS_LOCK = threading.Lock()
_ACCOUNT_STATUS: dict[str, dict] = {}
_ACCOUNT_STOPS: dict[str, threading.Event] = {}
_MANUALLY_STOPPED: set[str] = set()
_BLOCKED_REASONS: dict[str, str] = {}


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


def _parse_enabled_paths(raw):
    if not isinstance(raw, (list, tuple)):
        return [True, True]
    out = []
    for index in range(PATH_COUNT):
        out.append(raw[index] is not False if index < len(raw) else True)
    return out


def _draw_total(draw) -> int:
    values = []
    for item in draw or []:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            return -1
    if len(values) < 3:
        return -1
    return sum(values[:3])


def _main_trend_result(draw) -> dict:
    total = _draw_total(draw)
    if total < 0:
        return {"total": total, "dx": None, "ds": None, "labels": [], "special": False}
    dx = "小" if total <= 13 else "大"
    ds = "单" if total % 2 else "双"
    return {
        "total": total,
        "dx": dx,
        "ds": ds,
        "labels": [dx, ds],
        "special": total in (13, 14),
    }


def _target_hit(draw, target: str) -> bool:
    return target in _main_trend_result(draw)["labels"]


def _settlement_odds(draw, target: str, fallback=None):
    result = _main_trend_result(draw)
    if target in result["labels"] and result["total"] in (13, 14):
        return 1.6
    return fallback


def _result_desc(draw) -> str:
    result = _main_trend_result(draw)
    total = result["total"]
    labels = "/".join(result["labels"]) if result["labels"] else "未知"
    suffix = " 特殊赔率1.6" if result["special"] else ""
    return f"和值={total} {labels}{suffix}"


def _target_for_path(path_index: int, set_idx: int) -> str:
    return PATH_TARGETS[path_index][set_idx % 2]


def _parse_odds_text(text):
    match = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _format_odds(value):
    if value is None:
        return "未开放"
    if abs(value - round(value)) < 0.0001:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _read_main_trend_odds(page) -> dict[str, float | None]:
    frame = page.frame(name="frame") or page
    id_map = json.dumps(TARGET_ODDS_IDS, ensure_ascii=False)
    raw = frame.evaluate(
        f"""(function(){{
            var ids = {id_map};
            var out = {{}};
            Object.keys(ids).forEach(function(key){{
                var el = document.querySelector('#' + ids[key]);
                out[key] = el ? (el.innerText || el.textContent || '').trim() : '';
            }});
            return JSON.stringify(out);
        }})()"""
    )
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return {key: _parse_odds_text(data.get(key)) for key in TARGET_ODDS_IDS}


def _place_main_trend_bet(page, target_amounts: dict[str, int], log, account):
    frame = page.frame(name="frame") or page
    cleaned = {}
    for target, amount in (target_amounts or {}).items():
        if target not in TARGET_INPUT_IDS:
            continue
        try:
            value = int(amount)
        except (TypeError, ValueError):
            continue
        if value > 0:
            cleaned[target] = value
    if not cleaned:
        return False, {}

    odds_snapshot = _read_main_trend_odds(page)
    missing = [target for target in cleaned if odds_snapshot.get(target) is None]
    if missing:
        log(f"[{account}] 主势盘赔率未开放或已封盘：{missing}，跳过本期")
        return False, odds_snapshot

    clear_ids = list(TARGET_INPUT_IDS.values())
    fill_map = {TARGET_INPUT_IDS[target]: amount for target, amount in cleaned.items()}
    frame.evaluate(
        """(payload) => {
            const setValue = (el, value) => {
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(el, value);
                else el.value = value;
                ['input', 'change', 'keyup'].forEach((type) => el.dispatchEvent(new Event(type, { bubbles: true })));
            };
            payload.clearIds.forEach((id) => setValue(document.querySelector('#' + id), ''));
            Object.keys(payload.fillMap).forEach((id) => setValue(document.querySelector('#' + id), String(payload.fillMap[id])));
        }""",
        {"clearIds": clear_ids, "fillMap": fill_map},
    )

    confirm_js = (
        "(function(){"
        "var bs=document.querySelectorAll('input[type=\"button\"],button');"
        "for(var i=0;i<bs.length;i++){"
        "if((bs[i].value==='确定'||(bs[i].innerText&&bs[i].innerText.includes('确定')))&&bs[i].id!=='btnOk')"
        "{bs[i].click();break;}}"
        "})()"
    )

    try:
        frame.evaluate("(function(){var b=document.querySelector('#btnOk');if(b)b.click();})()")
        time.sleep(1.5)
        frame.evaluate(confirm_js)
        time.sleep(0.5)
        page.evaluate(confirm_js)
        time.sleep(0.5)
        try:
            page.locator('.ui-button-text:text("继续投注")').click(timeout=2000)
        except Exception:
            pass
        return True, odds_snapshot
    except Exception as exc:
        log(f"[{account}] 主势盘下注确认异常：{exc}")
        return False, odds_snapshot


def _goto_main_trend_page(page, account, log):
    frame = page.frame(name="frame") or page
    current_url = getattr(frame, "url", "") or ""
    if "page=zsp" in current_url:
        log(f"[{account}] 已在主势盘页面")
        return page

    try:
        page.locator('a[href*="page=zsp"], a[url*="page=zsp"]').first.click(timeout=5000)
        time.sleep(1.5)
    except Exception:
        pass

    frame = page.frame(name="frame") or page
    current_url = getattr(frame, "url", "") or ""
    if "page=zsp" in current_url:
        log(f"[{account}] 已切换到主势盘页面")
        return page

    target_url = ""
    if current_url:
        if "page=" in current_url:
            target_url = re.sub(r"page=[^&]+", "page=zsp", current_url)
        elif "?" in current_url:
            target_url = current_url + "&page=zsp"
    if target_url:
        frame.goto(target_url, wait_until="domcontentloaded", timeout=15000)
        log(f"[{account}] 已打开主势盘页面")
    else:
        log(f"[{account}] 警告：未识别当前盘口地址，无法自动切换主势盘")
    return page


def _observe_entry_draw(paths, draw, account, log, targets=None, rotate_after=False, enabled_paths=None):
    enabled_paths = enabled_paths or [True] * PATH_COUNT
    result = _main_trend_result(draw)
    activated = []
    for i in range(PATH_COUNT):
        if not enabled_paths[i]:
            continue
        path = paths[i]
        if path.active:
            continue
        target = targets[i] if targets is not None else _target_for_path(i, path.set_idx)
        hit = target in result["labels"]
        before = path.entry_loss_count
        ready = path.observe_entry(hit)
        path_name = PATH_LABELS[i]

        if hit:
            suffix = "，观察计数清零" if before else f"，等待连续{path.entry_miss_trigger}次未中"
            log(f"[{account}]   {path_name} 入场观察 | {_result_desc(draw)}【{target}命中】{suffix}")
        elif ready:
            activated.append(i)
            log(f"[{account}]   {path_name} 入场观察 | {_result_desc(draw)}【{target}未中】 -> 已达连续{path.entry_miss_trigger}次未中，下期开始实投")
        else:
            log(f"[{account}]   {path_name} 入场观察 | {_result_desc(draw)}【{target}未中】 -> 已累计{path.entry_loss_count}/{path.entry_miss_trigger}")

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
            log(f"[{account}] 已进入新周期 {res[0]}，开始执行主势大小单双追损")
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
    enabled_paths = _parse_enabled_paths(cfg.get("enabled_paths", cfg.get("enabled_positions")))
    win_max = int(cfg.get("bet_window_max", 90))
    draw_delay = int(cfg.get("draw_delay", 73))
    settle_early = max(0, int(cfg.get("settlement_wake_early", 8)))

    paths = [_CustomAmountPathState(amount_steps, entry_misses) for _ in range(PATH_COUNT)]
    if not any(enabled_paths):
        log(f"[{account}] 错误：至少需要启用一路，当前大小路和单双路都已关闭")
        return

    start_balance = _get_settled_balance(page) or _get_balance(page) or 0
    settled_balance = start_balance
    initial_draw = read_stable_draw(page, _get_last_draw_with_issue)
    last_issue = initial_draw[0] if initial_draw else None
    pending_issue = None
    pending_settlement = False
    bet_placed = False
    last_targets = [None] * PATH_COUNT
    last_amounts = [0] * PATH_COUNT
    last_bet_active = [False] * PATH_COUNT
    last_odds = [None] * PATH_COUNT
    last_heartbeat = 0.0

    path_desc = "  ".join(
        f"{PATH_LABELS[i]}[已关闭]" if not enabled_paths[i]
        else f"{PATH_LABELS[i]}[{PATH_TARGETS[i][0]}/{PATH_TARGETS[i][1]}轮换]"
        for i in range(PATH_COUNT)
    )
    trigger_desc = "立即实投" if entry_misses == 0 else f"连续{entry_misses}次未中后实投"
    log(f"[{account}] 主势大小单双追损启动 | 阶梯金额={amount_steps} 入场={trigger_desc} | {path_desc} | 起始余额={start_balance}")
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
                    log(f"[{account}] 开奖结算 | 期号={issue} {_result_desc(draw)} | 投注锚点={pending_issue} | 当前利润={profit:+.0f}")

                    won_paths = [False] * PATH_COUNT
                    for i in range(PATH_COUNT):
                        if not enabled_paths[i] or not last_bet_active[i]:
                            continue
                        amount = last_amounts[i]
                        target = last_targets[i]
                        hit = _target_hit(draw, target)
                        odds = _settlement_odds(draw, target, last_odds[i])
                        odds_desc = f" | 结算赔率={_format_odds(odds)}" if hit else ""

                        if hit:
                            paths[i].on_win()
                            won_paths[i] = not paths[i].active
                            next_desc = "下一期继续第一阶实投" if paths[i].active else f"回到入场观察，重新等待连续{paths[i].entry_miss_trigger}次未中"
                            log(f"[{account}]   {PATH_LABELS[i]}【中奖】 投{target}x{amount}元{odds_desc} -> {next_desc}")
                        else:
                            reset = paths[i].on_lose()
                            if reset:
                                log(f"[{account}]   {PATH_LABELS[i]}【未中】 投{target}x{amount}元 -> 最后一阶未中，回到第一阶，下一把注码={paths[i].get_bet()}")
                            else:
                                log(f"[{account}]   {PATH_LABELS[i]}【未中】 投{target}x{amount}元 -> 进入第{paths[i].tier_index + 1}阶，下一把注码={paths[i].get_bet()}")

                    observe_enabled = [enabled_paths[i] and not won_paths[i] for i in range(PATH_COUNT)]
                    _observe_entry_draw(paths, draw, account, log, targets=last_targets, enabled_paths=observe_enabled)
                    pending_settlement = False
                    pending_issue = None
                    last_issue = issue
            elif issue != last_issue:
                log(f"[{account}] 观察到新开奖 | 期号={issue} {_result_desc(draw)} | 当前利润={profit:+.0f}")
                _observe_entry_draw(paths, draw, account, log, rotate_after=True, enabled_paths=enabled_paths)
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
            targets = [_target_for_path(i, paths[i].set_idx) for i in range(PATH_COUNT)]
            active_mask = [enabled_paths[i] and paths[i].active for i in range(PATH_COUNT)]
            amounts = [paths[i].get_bet() if active_mask[i] else 0 for i in range(PATH_COUNT)]

            if not any(active_mask):
                observe_info = "  ".join(
                    f"{PATH_LABELS[i]}{targets[i]}(观察{paths[i].entry_loss_count}/{paths[i].entry_miss_trigger})"
                    for i in range(PATH_COUNT)
                    if enabled_paths[i]
                )
                log(f"[{account}] 入场观察中 | {observe_info} | 本期不下注")
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + draw_delay, stop_event)
                bet_placed = False
                continue

            parts = []
            for i in range(PATH_COUNT):
                if not enabled_paths[i]:
                    parts.append(f"{PATH_LABELS[i]}已关闭")
                    continue
                if active_mask[i]:
                    parts.append(f"{PATH_LABELS[i]}投{targets[i]}x{amounts[i]}元 第{paths[i].tier_index + 1}阶")
                else:
                    parts.append(f"{PATH_LABELS[i]}{targets[i]}(观察{paths[i].entry_loss_count}/{paths[i].entry_miss_trigger})")
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

            target_amounts = {targets[i]: amounts[i] for i in range(PATH_COUNT) if active_mask[i]}
            ok, odds_snapshot = _place_main_trend_bet(page, target_amounts, log, account)
            if ok:
                bet_placed = True
                pending_settlement = True
                pending_issue = anchor[0]
                last_issue = anchor[0]
                last_targets = targets
                last_amounts = amounts
                last_bet_active = active_mask
                last_odds = [odds_snapshot.get(targets[i]) for i in range(PATH_COUNT)]
                for i, path in enumerate(paths):
                    if enabled_paths[i]:
                        path.rotate()
                odds_desc = "  ".join(
                    f"{target}赔率{_format_odds(odds_snapshot.get(target))}"
                    for target in target_amounts
                )
                remain = _get_countdown(page)
                wait_seconds = max(0, remain) + max(0, draw_delay - settle_early)
                log(f"[{account}] 下注成功 | 投注锚点={pending_issue} | {odds_desc} | 预计{wait_seconds}秒后开始轮询结算")
                _sleep_interruptible(wait_seconds, stop_event)
                bet_placed = False
            else:
                bet_placed = True
                remain = _get_countdown(page)
                _sleep_interruptible((remain if remain > 0 else 30) + draw_delay, stop_event)
                bet_placed = False
        else:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] 等待下注窗口 | 倒计时={cd}秒 | 余额={bal:.0f}")
                last_heartbeat = now
            time.sleep(5)

    log(f"[{account}] 主势大小单双追损循环已停止")


def _external_conflict(acc_info: dict):
    try:
        from core.task_manager import TaskManager

        requested = _account_resources(acc_info)
        occupied = TaskManager.get().active_resources(exclude_task_id=TASK_ID)
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

    _prepare_launch_port(port, wait_for_next_cycle, log)
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
                _goto_main_trend_page(login_page, account, log)
                _set_account_status(key, status="waiting", message="已登录，等待开跑")
                _wait_until_start(config, account, stop_event, log)
                if wait_for_next_cycle and not stop_event.is_set():
                    _set_account_status(key, status="waiting", message="等待下一完整周期")
                    _wait_for_next_cycle(login_page, account, stop_event, log)
                if not stop_event.is_set():
                    _set_account_status(key, status="running", message="运行中")
                    _betting_loop(login_page, account, config, stop_event, log)
            except Exception as e:
                if stop_event.is_set():
                    _set_account_status(key, status="stopped", message="已停止")
                    log(f"[{account}] 收到停止信号，账号线程退出")
                else:
                    message = _friendly_account_error(e)
                    _set_account_status(key, status="error", message=message)
                    log(f"[{account}] 运行异常: {message}")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
                log(f"[{account}] 浏览器已关闭")
    except Exception as e:
        message = _friendly_account_error(e)
        _set_account_status(key, status="error", message=message)
        log(f"[{account}] 启动失败: {message}")
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
    locked_config = dict(config or {})
    schedule_target = _scheduled_target(locked_config)
    chrome_path = _get_chrome()
    threads: dict[str, threading.Thread] = {}

    while not stop_event.is_set():
        saved = get_config(CONFIG_KEY, locked_config)
        if not isinstance(saved, dict):
            saved = {}
        latest = dict(locked_config)
        if isinstance(saved.get("accounts"), list):
            latest["accounts"] = saved["accounts"]

        try:
            from core.task_manager import TaskManager

            update_result = TaskManager.get().update_config(TASK_ID, latest)
            update_ok = update_result[0] if isinstance(update_result, tuple) else bool(update_result)
            update_msg = update_result[1] if isinstance(update_result, tuple) and len(update_result) > 1 else ""
            if not update_ok and update_msg not in ("", "任务未运行"):
                reason = update_msg or "运行中配置更新被拒绝"
                if _BLOCKED_REASONS.get("__config__") != reason:
                    log(f"运行中配置更新被拒绝：{reason}")
                    _BLOCKED_REASONS["__config__"] = reason
                _sleep_interruptible(5, stop_event)
                continue
            _BLOCKED_REASONS.pop("__config__", None)
        except Exception:
            pass

        accounts = latest.get("accounts", []) if isinstance(latest, dict) else []
        configured_keys = {
            _account_key(acc_info)
            for acc_info in accounts
            if isinstance(acc_info, dict) and _account_key(acc_info)
        }
        with _STATUS_LOCK:
            _MANUALLY_STOPPED.intersection_update(configured_keys)
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

        running_count = len(running_accounts) + len([p for p in running_ports if p])
        if running_count == 0 and _MANUALLY_STOPPED:
            if _BLOCKED_REASONS.get("__idle__") != "idle":
                log("主势大小单双追损待机中，0个运行账号，可继续添加账号；点击全部停止才会结束任务")
                _BLOCKED_REASONS["__idle__"] = "idle"
        else:
            _BLOCKED_REASONS.pop("__idle__", None)

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
                reason = f"账号 {account} 已在主势大小单双追损中运行"
            elif port and port in running_ports:
                reason = f"端口 {port} 已在主势大小单双追损中运行"
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
                name=f"main-trend-bet-{key}",
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
    log("主势大小单双追损所有账号线程已结束")

"""
自动下注服务 — 三球9粒模式
整合 auto_login.py + auto_bet_mode3.py 逻辑，支持多账号并发
"""

import asyncio
import sys
import time
import re
import random
import threading
import queue
import shutil
import os
import subprocess
from datetime import datetime
from playwright.sync_api import sync_playwright


# ─── 工具函数 ────────────────────────────────────────────────

def _free_port(port, log=None):
    """Kill any process occupying *port* so Chrome can bind to it cleanly."""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], stderr=subprocess.DEVNULL, text=True
        )
        killed = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}"):
                try:
                    pid = int(parts[-1])
                    if pid > 0 and pid not in killed:
                        subprocess.call(
                            ["taskkill", "/PID", str(pid), "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        killed.add(pid)
                        if log:
                            log(f"[端口清理] 已终止占用端口{port}的进程(PID={pid})")
                except ValueError:
                    pass
    except Exception:
        pass

_ocr_instance = None
_ocr_lock = threading.Lock()


def _get_ocr():
    """惰性创建并缓存单个 DdddOcr 实例（onnxruntime 推理线程安全，多账号可共用）。
    避免每次识别都重载 ONNX 模型。返回 None 表示 ddddocr 不可用。"""
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                import ddddocr  # 未安装会抛 ImportError，由调用方兜底
                _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ocr_instance


def _solve_captcha(image_bytes: bytes) -> str:
    # 捕获所有异常（不只 ImportError）：坏图/解码失败时不应搞挂整个登录
    try:
        return _get_ocr().classification(image_bytes)
    except Exception:
        pass
    try:
        import muggle_ocr
        sdk = muggle_ocr.SDK(model_type=muggle_ocr.ModelType.Captcha)
        return sdk.predict(image_bytes)
    except Exception:
        pass
    return ""


def _sleep_interruptible(seconds: float, stop_event) -> None:
    """分段睡眠，期间轮询 stop_event，使「停止」能在 ~1 秒内生效。"""
    end = time.time() + seconds
    while True:
        remain = end - time.time()
        if remain <= 0 or stop_event.is_set():
            return
        time.sleep(min(1.0, remain))


def _is_cf(page) -> bool:
    try:
        c = page.content().lower()
        return any(s in c for s in ["cf-challenge", "just a moment", "checking your browser", "turnstile"])
    except Exception:
        return False


def _get_chrome() -> str | None:
    for candidate in [
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


# ─── 登录流程 ────────────────────────────────────────────────

def _login(page, context, account: str, password: str, entry_url: str, safe_code: str, log):
    log(f"[{account}] 打开入口 {entry_url}...")
    page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(2)
    page.locator('input[placeholder="请输入您的关键字"]').fill(safe_code)
    page.locator('a:has-text("搜索一下")').click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    time.sleep(2)

    # 选择会员线路
    page.wait_for_selector("table", timeout=30000)
    time.sleep(1)
    links = page.locator('td:has-text("会员线路") + td a')
    count = links.count()
    if count == 0:
        raise Exception("找不到会员线路")

    login_page = None
    for i in range(count):
        try:
            with context.expect_page(timeout=15000) as npi:
                links.nth(i).click()
            np = npi.value
            np.wait_for_load_state("domcontentloaded", timeout=15000)
            time.sleep(3)
            if _is_cf(np):
                np.close()
                continue
            login_page = np
            login_page.on("dialog", lambda d: d.accept())
            break
        except Exception:
            continue

    if not login_page:
        raise Exception("所有会员线路均被CF拦截")

    log(f"[{account}] 进入登录页: {login_page.url[:60]}")
    login_base = login_page.url.split("?")[0]

    for attempt in range(1, 9):
        cur = login_page.url
        if "/Home/Index" in cur or "/Member/Agreement" in cur:
            break
        try:
            login_page.locator('input[name="account"]').wait_for(state="visible", timeout=5000)
        except Exception:
            login_page.goto(login_base, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            cur = login_page.url
            if "/Home/Index" in cur or "/Member/Agreement" in cur:
                break
            try:
                login_page.locator('input[name="account"]').wait_for(state="visible", timeout=8000)
            except Exception:
                continue

        login_page.locator('input[name="account"]').fill(account)
        login_page.locator('input[name="password"]').fill(password)

        captcha_img = login_page.locator('.code img, dt img, img[alt="none"]').first
        captcha_img.wait_for(state="visible", timeout=5000)
        time.sleep(0.5)
        captcha_bytes = captcha_img.screenshot()
        captcha_text = re.sub(r'[^a-zA-Z0-9]', '', _solve_captcha(captcha_bytes))

        if len(captcha_text) < 3:
            log(f"[{account}] 验证码识别失败，重试 ({attempt}/8)")
            captcha_img.click()
            time.sleep(1)
            continue

        login_page.locator('input[name="code"]').fill(captcha_text)
        time.sleep(0.3)
        try:
            with login_page.expect_navigation(timeout=10000, wait_until="domcontentloaded"):
                login_page.locator('input.submit_btn').click()
        except Exception:
            pass
        time.sleep(2)
        cur = login_page.url
        if "/Home/Index" in cur or "/Member/Agreement" in cur:
            break
        log(f"[{account}] 第{attempt}次登录未成功，重试...")

    # 同意协议
    if "/Member/Agreement" in login_page.url:
        login_page.locator('a:has-text("同意")').first.click()
        login_page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

    # 关闭公告
    for _ in range(10):
        try:
            btns = login_page.locator('.ui-dialog:visible button:has-text("确定")')
            if btns.count() == 0:
                break
            btns.last.click(timeout=3000)
            time.sleep(0.8)
        except Exception:
            break

    # 导航到单球1~3
    try:
        login_page.locator('a[href*="page=hm13"]').first.click()
        time.sleep(3)
    except Exception:
        pass

    log(f"[{account}] 登录完成，当前页: {login_page.url[:60]}")
    return login_page


# ─── 下注逻辑 ────────────────────────────────────────────────

def _get_balance(page) -> float | None:
    try:
        s = page.locator("#accountLimit_0").inner_text(timeout=5000).strip()
        return float(s.replace(',', ''))
    except Exception:
        return None


def _get_countdown(page) -> int:
    try:
        frame = page.frame(name="frame")
        if not frame:
            return -1
        s = frame.locator("#cdClose").inner_text(timeout=3000).strip()
        if ':' in s:
            parts = s.split(':')
            return int(parts[-2]) * 60 + int(parts[-1])
        return int(s) if s.isdigit() else 0
    except Exception:
        return -2


def _get_last_draw(page):
    try:
        # 开奖结果在主页面，不在 frame 内
        els = page.locator("b[class^='b']").all()
        nums = []
        for el in els[:3]:
            t = el.inner_text(timeout=2000).strip()
            if t.isdigit():
                nums.append(int(t))
        return nums if len(nums) == 3 else None
    except Exception:
        return None


def _place_bet(page, pos_numbers: list, amounts: list, log, label: str):
    frame = page.frame(name="frame")
    if not frame:
        log(f"[{label}] 找不到frame，跳过")
        return False

    # 用 IIFE 包裹，避免 Playwright evaluate() 把 var 语句错误地包进 return
    set_calls = ""
    for i, nums in enumerate(pos_numbers):
        prefix = f"odds_B{i+1}QH"
        for n in nums:
            set_calls += f"setVal('{prefix}{n}','{amounts[i]}');"

    js = (
        "(function(){"
        "var setVal=function(id,v){"
        "var el=document.querySelector('#'+id);"
        "if(el){"
        "var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
        "s.call(el,v);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));"
        "el.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'Enter',code:'Enter'}));"
        "}};"
        + set_calls +
        "})()"
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
        frame.evaluate(js)
        time.sleep(1)
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
        return True
    except Exception as e:
        log(f"[{label}] 下注异常: {e}")
        return False


def _betting_loop(page, account: str, cfg: dict, stop_event: threading.Event, log):
    BASE_BET = cfg.get("base_bet_amount", 188)
    N = cfg.get("numbers_per_pos", 9)
    START_H = cfg.get("run_start_hour", 9)
    END_H = cfg.get("run_end_hour", 21)
    STOP_LOSS = cfg.get("daily_stop_loss", 70000)
    TAKE_PROFIT = cfg.get("take_profit", 40000)
    LOCKED = cfg.get("locked_profit", 30000)
    TIER1 = cfg.get("profit_tier", 30000)
    ODDS = cfg.get("odds", 9.92)
    REBATE = cfg.get("rebate_rate", 0.0073)

    # ===== 封盘/开奖时间参数（可被前端配置覆盖）=====
    WIN_MIN = int(cfg.get("bet_window_min", 60))      # 距封盘倒计时落在 [min,max] 才下注
    WIN_MAX = int(cfg.get("bet_window_max", 120))
    CLOSE_BUFFER = int(cfg.get("close_buffer", 10))   # 延时后仍需 >该秒数才下注
    DRAW_DELAY = int(cfg.get("draw_delay", 73))       # 封盘到开奖间隔（实测加拿大2.0=73s）

    start_balance = _get_balance(page) or 0
    last_draw = None
    bet_placed = False        # 控制本局是否可以下注
    pending_settlement = False  # 上一局有待结算的注单
    targets = [None, None, None]
    amounts = [BASE_BET, BASE_BET, BASE_BET]
    max_profit = 0.0
    last_heartbeat = 0.0

    log(f"[{account}] 开始下注循环 | 起始余额: {start_balance}")

    while not stop_event.is_set():
        h = datetime.now().hour
        # 支持跨天窗口：START_H > END_H 时表示运行到次日 END_H
        if START_H <= END_H:
            in_window = START_H <= h < END_H
        else:
            in_window = h >= START_H or h < END_H
        if not in_window:
            log(f"[{account}] 宵禁时间 ({h}点，窗口 {START_H}-{END_H})，待机中...")
            time.sleep(60)
            continue

        bal = _get_balance(page)
        if bal is None:
            time.sleep(3)
            continue

        profit = bal - start_balance
        max_profit = max(max_profit, profit)

        if profit >= TAKE_PROFIT:
            log(f"[{account}] 🎉 止盈! 利润={profit:.0f} ≥ {TAKE_PROFIT}")
            break
        if max_profit >= TIER1 and profit <= LOCKED:
            log(f"[{account}] 🛡️ 锁利! 回撤触碰保底线 {LOCKED}")
            break
        if profit <= -STOP_LOSS:
            log(f"[{account}] 🩸 止损! 亏损={profit:.0f}")
            break

        draw = _get_last_draw(page)
        if draw and draw != last_draw:
            log(f"[{account}] 📊 开奖: {draw} | 利润: {profit:+.0f}")
            # 结算上一局（pending_settlement 在下注成功后置 True，bet_placed 重置不影响它）
            if pending_settlement and targets[0] is not None:
                total_win = 0.0
                for i in range(3):
                    hit = draw[i] in targets[i]
                    ball_cost = amounts[i] * len(targets[i])
                    rebate = ball_cost * REBATE
                    if hit:
                        win = amounts[i] * ODDS
                        ball_profit = win - ball_cost + rebate
                        total_win += ball_profit
                        log(f"[{account}]   球{i+1} 开{draw[i]} ✅中 | 赢{win:.2f}-投{ball_cost}+退{rebate:.2f}={ball_profit:+.2f}")
                    else:
                        ball_profit = -ball_cost + rebate
                        total_win += ball_profit
                        log(f"[{account}]   球{i+1} 开{draw[i]} ❌未中 | -投{ball_cost}+退{rebate:.2f}={ball_profit:+.2f}")
                log(f"[{account}]   本期自算: {total_win:+.2f} | 累计利润: {profit:+.0f}")
                pending_settlement = False
            last_draw = draw

        cd = _get_countdown(page)
        if cd < 0:
            time.sleep(2)
            continue

        # ⚠️ 必须等上一期开奖结算完才下注，避免开奖未确认就投/覆盖待结算注单
        if pending_settlement:
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] ⏳ 等待上期开奖结算后再下注 | 倒计时{cd}s")
                last_heartbeat = now
            time.sleep(2)
            continue

        if WIN_MIN <= cd <= WIN_MAX and not bet_placed:
            for i in range(3):
                targets[i] = sorted(random.sample(range(10), N))
            amounts = [BASE_BET, BASE_BET, BASE_BET]

            cost = BASE_BET * N * 3
            log(f"[{account}] 🎲 本期选号 | 每球{N}码×{BASE_BET}元=每球{BASE_BET*N} | 三球合计{cost}")
            for i in range(3):
                log(f"[{account}]   球{i+1}: {targets[i]}")

            delay = random.uniform(2.0, 6.0)
            log(f"[{account}] ⏳ 距封盘{cd}s，延时{delay:.1f}s后下注...")
            time.sleep(delay)

            if stop_event.is_set():
                break

            if _get_countdown(page) > CLOSE_BUFFER:
                ok = _place_bet(page, targets, amounts, log, account)
                if ok:
                    bet_placed = True
                    pending_settlement = True   # 标记本局有待结算注单
                    remain = _get_countdown(page)
                    log(f"[{account}] ✅ 下注成功，等待开奖 ({remain+DRAW_DELAY}s)...")
                    _sleep_interruptible(remain + DRAW_DELAY, stop_event)
                    # 睡眠结束，新一局可以下注；结算由 pending_settlement 驱动，不依赖 bet_placed
                    bet_placed = False
                    log(f"[{account}] 🔄 新一局开始，准备下注...")
            else:
                log(f"[{account}] ⚠️ 封盘太快，取消本期")
        elif bet_placed:
            # 已下注，等待开奖阶段
            time.sleep(3)
        else:
            # 等待进入下注窗口，每30秒打一次心跳
            now = time.time()
            if now - last_heartbeat >= 30:
                log(f"[{account}] ⏱ 等待下注窗口 | 当前倒计时 {cd}s | 余额 {bal:.0f}")
                last_heartbeat = now
            time.sleep(5)

    log(f"[{account}] 下注循环已停止")


# ─── 服务入口 ────────────────────────────────────────────────

def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg: str):
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
        time.sleep(5)  # 错开启动

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

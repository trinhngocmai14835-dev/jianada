"""
跟投服务 — 多账号跟投 + 自算帐结算
整合 follow_bet_combined.py 逻辑
"""

import time
import re
import json
import threading
import queue
import subprocess
import shutil
import os
import urllib.parse
from datetime import datetime, date
from playwright.sync_api import sync_playwright

POS_PREFIX = {"b1": "odds_B1QH", "b2": "odds_B2QH", "b3": "odds_B3QH"}


# ─── Chrome 启动工具 ──────────────────────────────────────────

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


def _launch_source_browser(port: str, entry_url: str, log) -> bool:
    chrome = _get_chrome()
    if not chrome:
        log(f"❌ 未找到Chrome可执行文件，请手动启动Chrome并添加参数: --remote-debugging-port={port}")
        return False

    log(f"[A] Chrome路径: {chrome}")

    # 用固定持久化 profile 目录（~/.betting_platform/chrome_{port}），而非每次新建的 TEMP 目录。
    # 持久化目录保留 cookie/session，CF 不会因全空白 profile 触发人机验证。
    profile_dir = os.path.join(os.path.expanduser("~"), ".betting_platform", f"chrome_{port}")
    os.makedirs(profile_dir, exist_ok=True)
    url = entry_url if entry_url else "about:blank"
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--exclude-switches=enable-automation",   # 去掉"受自动化控制"标识
        "--disable-infobars",
        "--disable-features=ChromeWhatsNewUI",
        url,
    ]
    try:
        proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        log(f"🌐 Chrome已启动(PID={proc.pid}, 端口{port}, 独立配置文件)，请登录后前往 报表中心 → 注单明细")
        return True
    except Exception as e:
        log(f"❌ 启动Chrome失败: {e}")
        return False


# ─── 反检测补丁 ───────────────────────────────────────────────

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
try { delete window.__playwright; } catch(e) {}
try { delete window.playwright; } catch(e) {}
try { delete window._pw_l; } catch(e) {}
"""


def _patch_stealth(browser):
    """连接 CDP 后给所有 context/page 打免检测补丁。"""
    try:
        for ctx in browser.contexts:
            try:
                ctx.add_init_script(_STEALTH_JS)
            except Exception:
                pass
            for pg in ctx.pages:
                try:
                    pg.evaluate(_STEALTH_JS)
                except Exception:
                    pass
    except Exception:
        pass


# ─── 报表页识别 ───────────────────────────────────────────────

def _is_report_page(pg) -> bool:
    """判断某个标签页是否是注单明细页（支持各种站点URL + 内容兜底识别）。"""
    url = (pg.url or "").lower()
    # 常见注单明细URL关键词（覆盖主流站点）
    if any(kw in url for kw in [
        "bettingdetail", "reportnew", "instant", "betrecord",
        "orderbet", "betdetail", "reportdetail", "betlist",
        "report", "order", "record",
    ]):
        return True
    # 内容兜底：页面有表格 + 7位以上期号 + "球"字（只有注单明细才同时满足）
    try:
        return bool(pg.evaluate("""() => {
            var t = document.body ? document.body.innerText : '';
            return document.querySelectorAll('table tr').length > 3
                && /[0-9]{7,}/.test(t)
                && t.indexOf('球') >= 0;
        }"""))
    except Exception:
        return False


# ─── 页面操作工具 ─────────────────────────────────────────────

def _get_real_page(browser):
    try:
        for ctx in browser.contexts:
            for pg in ctx.pages:
                url = pg.url or ""
                if any(s in url for s in ["devtools://", "chrome-extension://", "about:blank"]):
                    continue
                return pg
    except Exception:
        pass
    return None


def _find_cd_page(browser):
    """找到含投注窗口（倒计时）的页面：优先找有 PlaceBet iframe 或 name=frame 的页面。"""
    candidates = []
    try:
        for ctx in browser.contexts:
            for pg in ctx.pages:
                url = pg.url or ""
                if any(s in url for s in ["devtools://", "chrome-extension://", "about:blank"]):
                    continue
                for f in pg.frames:
                    fu = f.url or ""
                    fn = f.name or ""
                    if "PlaceBet" in fu or fn == "frame":
                        return pg
                candidates.append(pg)
    except Exception:
        pass
    return candidates[0] if candidates else None


def _get_iframe(page):
    try:
        for f in page.frames:
            if f.name == "frame":
                return f
        for f in page.frames:
            if f == page.main_frame:
                continue
            try:
                cnt = f.evaluate("document.querySelectorAll('[id^=odds_B]').length")
                if cnt and cnt > 0:
                    return f
            except Exception:
                continue
    except Exception:
        pass
    return None


def _get_countdown(page) -> int:
    """读取倒计时秒数，兼容多种站点的元素ID/class。-1 表示未找到。"""
    _SELECTORS = [
        "#cdClose", "#countdown", "#timer", "#cd", "#cdtime",
        ".countdown", ".cd-close", ".timer",
        "[id*='count']", "[id*='timer']", "[id*='close']", "[id*='cdtime']",
    ]
    targets = []
    try:
        f = page.frame(name="frame")
        if f:
            targets.append(f)
    except Exception:
        pass
    targets.append(page)

    for target in targets:
        for sel in _SELECTORS:
            try:
                s = target.locator(sel).first.inner_text(timeout=600).strip()
                if not s:
                    continue
                if ":" in s:
                    parts = s.split(":")
                    m_part, s_part = parts[-2].strip(), parts[-1].strip()
                    if m_part.isdigit() and s_part.isdigit():
                        return int(m_part) * 60 + int(s_part)
                if s.isdigit():
                    return int(s)
            except Exception:
                continue
    return -1


def _get_history(page):
    def _read(target):
        try:
            els = target.locator("b[class^='b']").all()
            nums = []
            for el in els[:3]:
                t = el.inner_text(timeout=2000).strip()
                if t.isdigit():
                    nums.append(int(t))
            return nums if len(nums) == 3 else None
        except Exception:
            return None

    result = _read(page)
    if result:
        return result
    try:
        frame = page.frame(name="frame")
        if frame:
            result = _read(frame)
            if result:
                return result
    except Exception:
        pass
    return None


def _get_current_period(page) -> str | None:
    try:
        frame = page.frame(name="frame") or page
        return frame.evaluate("""() => {
            var t = document.body.innerText;
            var m = t.match(/第\\s*([0-9]{7,})\\s*期/);
            if (m) return m[1];
            m = t.match(/期号[^0-9]*([0-9]{7,})/);
            if (m) return m[1];
            var all = t.match(/[0-9]{7,}/g);
            return all && all.length > 0 ? all[0] : null;
        }""")
    except Exception:
        return None


_FETCH_JS = """() => {
    var results = [];
    // 优先找 iframe 里的表格，其次找主文档
    var docs = [document];
    var frames = document.querySelectorAll('iframe, frame');
    for (var fi = 0; fi < frames.length; fi++) {
        try { if (frames[fi].contentDocument) docs.push(frames[fi].contentDocument); } catch(e) {}
    }
    for (var di = 0; di < docs.length; di++) {
        var trs = docs[di].querySelectorAll('table tr');
        if (trs.length < 2) continue;
        var hTds = trs[0].querySelectorAll('th, td');
        var colAccount = -1;
        for (var h = 0; h < hTds.length; h++) {
            var t = (hTds[h].innerText || '').trim();
            if (t.includes('帐号') || t.includes('账号') || t.includes('用户')) colAccount = h;
        }
        for (var i = 1; i < trs.length; i++) {
            var tds = trs[i].querySelectorAll('td');
            if (tds.length < 3) continue;
            var period = '', account = '', rowText = '';
            for (var k = 0; k < tds.length; k++) {
                var txt = (tds[k].innerText || '').trim();
                if (!period) {
                    var mp = txt.match(/([0-9]{7,})/);
                    if (mp && !txt.startsWith('N')) period = mp[1];
                }
                if (!account && (txt.includes('A盘') || txt.includes('B盘') || k === colAccount))
                    account = txt.replace(/\\s+/g, '_');
                // 拼接整行文字（包含span内容）用于后续多球解析
                var sp = tds[k].querySelector('span');
                rowText += ' ' + (sp ? sp.innerText.trim() : txt);
            }
            if (!period || rowText.indexOf('\\u7403') < 0) continue;
            results.push({account: account || 'unknown', period: period, content: rowText.trim()});
        }
        if (results.length > 0) break; // 找到数据就不再找其他 doc
    }
    return results;
}"""


def _fetch_bets(page_a: object) -> dict:
    try:
        items = page_a.evaluate(_FETCH_JS)
    except Exception:
        return {}
    if not items:
        return {}
    pos_map = {"一": "b1", "二": "b2", "三": "b3", "1": "b1", "2": "b2", "3": "b3"}
    groups: dict = {}
    for item in items:
        key = (item["account"], item["period"])
        if key not in groups:
            groups[key] = {"b1": [], "b2": [], "b3": []}
        c = item["content"]
        # 用 findall 拿到行内所有的"第X球【N】"组合
        pairs = re.findall(r"第\s*([一二三123])\s*球[^一二三1-3球]{0,20}?[〖【\[\(](\d)[〗】\]\)]", c)
        for pos_char, num_str in pairs:
            pos = pos_map.get(pos_char)
            num = int(num_str)
            if pos and num not in groups[key][pos]:
                groups[key][pos].append(num)
    return groups


def _navigate_today(page, log):
    """把报表页日期自动切换到今天，避免读到旧注单"""
    try:
        today = date.today().strftime("%Y-%m-%d")
        url = page.url
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if "querydata" not in params:
            return
        qd = json.loads(params["querydata"][0])
        if qd.get("startDate") == today and qd.get("endDate") == today:
            log(f"[A] 报表日期已是今日 ({today})")
            return
        old_date = qd.get("startDate", "?")
        qd["startDate"] = today
        qd["endDate"] = today
        params["querydata"] = [json.dumps(qd, ensure_ascii=False, separators=(",", ":"))]
        new_query = "&".join(
            f"{k}={urllib.parse.quote(v[0], safe='')}" for k, v in params.items()
        )
        new_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
        log(f"[A] 日期从 {old_date} 切换到 {today}，重新加载...")
        page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
    except Exception as e:
        log(f"[A] 自动更新日期失败（请手动改成今日）: {e}")


_SET_COUNT_JS = ("(function(){"
    "var s=document.getElementById('showCount');"
    "if(!s)return false;"
    "if(s.value==='999')return true;"
    "s.value='999';"
    "s.dispatchEvent(new Event('change',{bubbles:true}));"
    "return true;"
    "})()")


def _refresh_page(rp) -> dict | None:
    try:
        rp.reload(wait_until="load", timeout=25000)
    except Exception:
        pass
    # 设最大分页，触发 AJAX 重新加载全量数据
    try:
        changed = rp.evaluate(_SET_COUNT_JS)
        if changed:
            time.sleep(3)   # 等 AJAX 刷新完表格
        else:
            time.sleep(2)
    except Exception:
        time.sleep(3)
    try:
        return _fetch_bets(rp)
    except Exception:
        return None


def _place_bet(page_b, bets_by_pos: dict, amount: int, log) -> bool:
    frame = _get_iframe(page_b) or page_b
    targets = []
    for pos, nums in bets_by_pos.items():
        pfx = POS_PREFIX[pos]
        for n in nums:
            targets.append(f"{pfx}{n}")
    if not targets:
        return False
    ids_json = str(targets)
    amt_str = str(amount)
    js_fill = f"""(function(){{
        var setVal=function(id,amt){{
            var el=document.getElementById(id);
            if(!el)return;
            var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
            s.call(el,amt);
            el.dispatchEvent(new Event('input',{{bubbles:true}}));
            el.dispatchEvent(new Event('change',{{bubbles:true}}));
        }};
        var ids={ids_json};
        for(var i=0;i<ids.length;i++)setVal(ids[i],'{amt_str}');
    }})()"""
    try:
        frame.evaluate(js_fill)
    except Exception as e:
        log(f"  ❌ 填值异常: {e}")
        return False
    time.sleep(1)
    try:
        frame.evaluate("var b=document.querySelector('#btnOk');if(b)b.click();")
    except Exception as e:
        log(f"  ❌ btnOk异常: {e}")
        return False
    time.sleep(1.5)
    confirm_js = """var bs=document.querySelectorAll('input[type="button"],button');for(var i=0;i<bs.length;i++){if((bs[i].value==='确定'||(bs[i].innerText&&bs[i].innerText.includes('确定')))&&bs[i].id!=='btnOk'){bs[i].click();break;}}"""
    try:
        frame.evaluate(confirm_js)
    except Exception:
        pass
    time.sleep(0.5)
    try:
        page_b.evaluate(confirm_js)
    except Exception:
        pass
    time.sleep(0.5)
    try:
        page_b.locator('.ui-button-text:text("继续投注")').click(timeout=2000)
    except Exception:
        pass
    # 清空输入框
    js_clear = """(function(){var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;var ins=document.querySelectorAll('[id^=odds_B1QH],[id^=odds_B2QH],[id^=odds_B3QH]');for(var i=0;i<ins.length;i++){s.call(ins[i],'');ins[i].dispatchEvent(new Event('input',{bubbles:true}));}})()"""
    try:
        frame.evaluate(js_clear)
    except Exception:
        pass
    return True


# ─── 结算模块 ─────────────────────────────────────────────────

class _Tracker:
    def __init__(self, odds, rebate, log):
        self.odds = odds
        self.rebate = rebate
        self.log = log
        self.total = 0.0
        self.periods = 0
        self.pending: dict = {}
        self.settled: set = set()

    def record(self, period, port, bets, amount):
        if period not in self.pending:
            self.pending[period] = {}
        if port not in self.pending[period]:
            self.pending[period][port] = {"b1": set(), "b2": set(), "b3": set(), "amount": amount}
        for pos in ["b1", "b2", "b3"]:
            for n in bets.get(pos, []):
                self.pending[period][port][pos].add(n)

    def settle(self, period, draw_nums):
        if period in self.settled or period not in self.pending:
            return
        self.settled.add(period)
        self.periods += 1
        draw = {"b1": draw_nums[0], "b2": draw_nums[1], "b3": draw_nums[2]}
        period_profit = 0.0
        self.log(f"📒 结算期号: {period} | 开奖: {draw_nums}")
        for port, info in self.pending[period].items():
            amt = info["amount"]
            pp = 0.0
            for pos, label in [("b1", "一球"), ("b2", "二球"), ("b3", "三球")]:
                nums = info.get(pos, set())
                if not nums:
                    continue
                bc = len(nums)
                total_bet = bc * amt
                rebate_v = total_bet * self.rebate
                if draw[pos] in nums:
                    win = amt * self.odds
                    bp = win - total_bet + rebate_v
                    self.log(f"  [{port}] {label}: ✅中{draw[pos]} 赢{win:.2f}-投{total_bet}+退{rebate_v:.2f}={bp:+.2f}")
                else:
                    bp = -total_bet + rebate_v
                    self.log(f"  [{port}] {label}: ❌未中{draw[pos]} -{total_bet}+退{rebate_v:.2f}={bp:+.2f}")
                pp += bp
            period_profit += pp
            self.log(f"  [{port}] 本期盈亏: {pp:+.2f}")
        self.total += period_profit
        self.log(f"📊 本期总盈亏: {period_profit:+.2f} | 累计: {self.total:+.2f} (共{self.periods}期)")


# ─── 服务入口 ─────────────────────────────────────────────────

def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg: str):
        log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})

    source_port = str(config.get("source_port", "9222"))
    followers_cfg = config.get("followers", [{"port": "9223", "bet_amount": 100}])
    BET_START = config.get("bet_window_start", 120)
    BET_END = config.get("bet_window_end", 35)
    REFRESH = config.get("refresh_sec", 5)
    ODDS = config.get("odds", 9.92)
    REBATE = config.get("rebate", 0.0073)
    entry_url = config.get("entry_url", "")

    follower_str = ', '.join(f"{f['port']}({f['bet_amount']}元)" for f in followers_cfg)
    log(f"跟投服务启动 | 采集端口:{source_port} | 跟投:{follower_str}")

    with sync_playwright() as p:
        # ── 步骤1：自动启动采集端口Chrome，连接后等待登录 ──────────
        log(f"[A] 尝试连接端口{source_port}的Chrome...")
        browser_a = None
        launched = False
        last_remind = 0
        while not stop_event.is_set():
            try:
                browser_a = p.chromium.connect_over_cdp(f"http://127.0.0.1:{source_port}")
                _patch_stealth(browser_a)
                log(f"[A] 已连接端口{source_port}的Chrome")
                break
            except Exception:
                if not launched:
                    launched = _launch_source_browser(source_port, entry_url, log)
                now = time.time()
                if now - last_remind >= 15:
                    log(f"[A] 等待Chrome(端口{source_port})就绪，请在弹出的浏览器中登录...")
                    last_remind = now
                time.sleep(3)

        if browser_a is None:
            log("⏹️ 已取消等待")
            return

        # ── 步骤2：等待报表页出现 ───────────────────────────────
        log(f"⏳ 请在Chrome中登录，然后点击【即时注单】或【报表查询 → 注单明细】（等待中...）")
        report_pages = []
        last_remind = 0
        while not stop_event.is_set():
            report_pages = []
            try:
                for ctx in browser_a.contexts:
                    for pg in ctx.pages:
                        if _is_report_page(pg):
                            report_pages.append(pg)
            except Exception:
                pass
            if report_pages:
                log(f"[A] 找到{len(report_pages)}个注单明细标签页")
                break
            now = time.time()
            if now - last_remind >= 10:
                log(f"⏳ 未找到注单明细页，请点击【即时注单】或【报表查询 → 注单明细】...")
                last_remind = now
            time.sleep(3)

        if not report_pages:
            log("⏹️ 已取消等待")
            return
        page_a = report_pages[0]
        _navigate_today(page_a, log)
        log(f"[A] 开始监控注单明细")

        # 连接跟投账号
        followers = []
        for f_cfg in followers_cfg:
            port_b = str(f_cfg.get("port", "9223"))
            bet_b = int(f_cfg.get("bet_amount", 100))
            try:
                bb = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port_b}")
                pg = _get_real_page(bb) or bb.contexts[0].pages[0]
                followers.append((port_b, bet_b, bb, pg))
                log(f"[B:{port_b}] 已连接 每注{bet_b}元")
            except Exception as e:
                log(f"[B:{port_b}] ❌ 连接失败: {e}")

        if not followers:
            log("❌ 无可用的跟投账号")
            return

        # 初始化分页
        try:
            page_a.evaluate(_SET_COUNT_JS)
            time.sleep(2)
        except Exception:
            pass

        tracker = _Tracker(ODDS, REBATE, log)
        done_keys: dict = {}
        total_bets = 0
        bet_record: dict = {}
        last_draw = None
        last_settled = None
        bet_placed_this_period = False
        cd_browser = followers[0][2]
        cd_page = _find_cd_page(cd_browser) or followers[0][3]

        log(f"开始监控，等待投注窗口 (倒计时 {BET_END}~{BET_START}秒)...")

        _cd_miss = 0
        while not stop_event.is_set():
            real = _find_cd_page(cd_browser)
            if real:
                cd_page = real

            cd = _get_countdown(cd_page)
            if cd < 0:
                _cd_miss += 1
                if _cd_miss % 5 == 1:  # 每15秒提示一次
                    log(f"⚠️  未检测到倒计时（已等{_cd_miss*3}s），请确认跟投账号浏览器已打开投注页面（即时注单/快三等游戏页）| 当前页: {(cd_page.url or '')[:60]}")
                time.sleep(3)
                continue
            _cd_miss = 0

            # 检查开奖
            draw_nums = _get_history(cd_page)
            if draw_nums and draw_nums != last_draw:
                last_draw = draw_nums
                log(f"📊 最新开奖: {draw_nums}")
                if bet_placed_this_period and draw_nums != last_settled:
                    last_settled = draw_nums
                    bet_placed_this_period = False
                    for per in sorted(bet_record.keys(), reverse=True):
                        if per not in tracker.settled:
                            tracker.settle(per, draw_nums)
                            break

            # 判断窗口
            if cd > BET_START:
                wait = cd - BET_START
                log(f"⏳ 倒计时{cd}s，等待{wait}s...")
                time.sleep(min(wait, 10))
                continue

            if cd <= BET_END:
                if cd > 0:
                    log(f"🔒 封盘中(倒计时{cd}s)，等待开奖...")
                    time.sleep(cd + 10)
                else:
                    time.sleep(5)
                continue

            # 投注窗口：刷新报表
            log(f"📡 倒计时{cd}s，抓取注单...")
            report_pages = []
            for ctx in browser_a.contexts:
                for pg in ctx.pages:
                    if _is_report_page(pg):
                        report_pages.append(pg)
            if not report_pages:
                log("⚠️  未找到注单明细页，请确认采集账号浏览器已打开注单明细")
                time.sleep(REFRESH)
                continue

            all_groups: dict = {}
            for rp in report_pages:
                g = _refresh_page(rp)
                if g:
                    for k, v in g.items():
                        if k not in all_groups:
                            all_groups[k] = v
                        else:
                            for pos in ["b1", "b2", "b3"]:
                                for n in v[pos]:
                                    if n not in all_groups[k][pos]:
                                        all_groups[k][pos].append(n)

            if not all_groups:
                time.sleep(REFRESH)
                continue

            for (account, period), bets in all_groups.items():
                if stop_event.is_set():
                    break
                key = (account, period)
                curr = {pos: set(bets[pos]) for pos in ["b1", "b2", "b3"]}
                done = done_keys.get(key, {"b1": set(), "b2": set(), "b3": set()})
                need = {pos: list(curr[pos] - done[pos]) for pos in ["b1", "b2", "b3"]}
                need_total = sum(len(v) for v in need.values())
                if need_total == 0:
                    continue

                # 期号验证
                bet_period = _get_current_period(cd_page)
                if bet_period and bet_period != period:
                    log(f"  ⚠️  期号不匹配! 报表={period} 投注页={bet_period}，跳过")
                    continue

                log(f"  [{account}] 期:{period} 待补{need_total}注")

                if key not in done_keys:
                    done_keys[key] = {"b1": set(), "b2": set(), "b3": set()}
                for pos in ["b1", "b2", "b3"]:
                    done_keys[key][pos].update(need[pos])

                n_f = len(followers)
                all_items = [(pos, n) for pos in ["b1", "b2", "b3"] for n in need[pos]]
                buckets = [[] for _ in range(n_f)]
                for idx, item in enumerate(all_items):
                    buckets[idx % n_f].append(item)

                for fi, (f_port, f_bet, f_browser, f_page) in enumerate(followers):
                    if not buckets[fi]:
                        continue
                    my_bets: dict = {}
                    for pos, n in buckets[fi]:
                        my_bets.setdefault(pos, []).append(n)
                    rp = _find_cd_page(f_browser) or _get_real_page(f_browser) or f_page
                    ok = _place_bet(rp, my_bets, f_bet, log)
                    if ok:
                        total_bets += len(buckets[fi])
                        bet_placed_this_period = True
                        log(f"    ✅ [{f_port}] 成功 {len(buckets[fi])}注×{f_bet}元 | 累计{total_bets}注")
                        if period not in bet_record:
                            bet_record[period] = {}
                        if f_port not in bet_record[period]:
                            bet_record[period][f_port] = {"b1": set(), "b2": set(), "b3": set(), "amount": f_bet}
                        for pos, n in buckets[fi]:
                            bet_record[period][f_port][pos].add(n)
                        tracker.record(period, f_port, my_bets, f_bet)
                    else:
                        log(f"    ❌ [{f_port}] 失败，下次重试")
                        for pos, n in buckets[fi]:
                            done_keys[key][pos].discard(n)
                    time.sleep(1)

            time.sleep(REFRESH)

        log(f"⏹️ 跟投停止 | 共跟投{total_bets}注 | 自算累计: {tracker.total:+.2f}")

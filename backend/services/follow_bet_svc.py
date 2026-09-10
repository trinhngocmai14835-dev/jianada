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

from services.auto_bet_svc import _sleep_interruptible, _login

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


def _launch_source_browser(port: str, entry_url: str, log, usage_hint: str = "请登录后前往 报表中心 → 注单明细") -> bool:
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
        log(f"🌐 Chrome已启动(PID={proc.pid}, 端口{port}, 独立配置文件)，{usage_hint}")
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
    // 按表头定位列：投注内容/下注金额/帐号/下注编号/彩种，逐行抓出每注金额
    function pickCols(cells){
        var idx={account:-1,content:-1,amount:-1,betId:-1,lottery:-1};
        for(var h=0;h<cells.length;h++){
            var t=(cells[h].innerText||'').trim();
            if(idx.account<0 && (t.indexOf('帐号')>=0||t.indexOf('账号')>=0||t.indexOf('用户')>=0)) idx.account=h;
            if(idx.content<0 && t.indexOf('投注内容')>=0) idx.content=h;
            if(idx.amount<0 && (t.indexOf('下注金额')>=0||t.indexOf('投注金额')>=0)) idx.amount=h;
            if(idx.betId<0 && (t.indexOf('下注编号')>=0||t.indexOf('注单号')>=0)) idx.betId=h;
            if(idx.lottery<0 && t.indexOf('彩种')>=0) idx.lottery=h;
        }
        return idx;
    }
    var docs=[document];
    var frames=document.querySelectorAll('iframe, frame');
    for(var fi=0;fi<frames.length;fi++){try{if(frames[fi].contentDocument)docs.push(frames[fi].contentDocument);}catch(e){}}
    var results=[];
    for(var di=0;di<docs.length;di++){
        var tables=docs[di].querySelectorAll('table');
        for(var ti=0;ti<tables.length;ti++){
            var trs=tables[ti].querySelectorAll('tr');
            if(trs.length<2) continue;
            var idx=pickCols(trs[0].querySelectorAll('th, td'));
            if(idx.content<0 || idx.amount<0) continue;  // 非注单明细表，跳过
            for(var i=1;i<trs.length;i++){
                var tds=trs[i].querySelectorAll('td');
                if(tds.length<=idx.content || tds.length<=idx.amount) continue;
                var content=(tds[idx.content].innerText||'').replace(/\\s+/g,' ').trim();
                if(content.indexOf('球')<0) continue;
                var amount=(tds[idx.amount].innerText||'').replace(/[^0-9.]/g,'');
                var account=(idx.account>=0&&idx.account<tds.length)?(tds[idx.account].innerText||'').replace(/\\s+/g,'_').trim():'unknown';
                var betId=(idx.betId>=0&&idx.betId<tds.length)?(tds[idx.betId].innerText||'').trim():'';
                var period='';
                if(idx.lottery>=0&&idx.lottery<tds.length){var ml=(tds[idx.lottery].innerText||'').match(/([0-9]{7,})/);if(ml)period=ml[1];}
                if(!period){var rowAll='';for(var k=0;k<tds.length;k++)rowAll+=' '+(tds[k].innerText||'');var m2=rowAll.match(/([0-9]{7,})/);if(m2)period=m2[1];}
                results.push({betId:betId,account:account||'unknown',period:period,content:content,amount:amount});
            }
            if(results.length>0) break;
        }
        if(results.length>0) break;
    }
    return results;
}"""


def _fetch_bets(page_a: object) -> dict:
    """读注单明细，返回 {(账号,期号): [ {betId,pos,num,amount}, ... ]}。
    amount = 客户该注下注金额（整数元），用于按倍数跟投与自算结算。"""
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
        try:
            amount = int(float(item.get("amount") or 0))
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            continue
        bet_id = (item.get("betId") or "").strip()
        c = item["content"]
        # 用 findall 拿到行内所有的"第X球【N】"组合（通常一行一个）
        pairs = re.findall(r"第\s*([一二三123])\s*球[^一二三1-3球]{0,20}?[〖【\[\(](\d)[〗】\]\)]", c)
        for pos_char, num_str in pairs:
            pos = pos_map.get(pos_char)
            if not pos:
                continue
            num = int(num_str)
            # 无下注编号时退化用 球+号+金额 作去重键
            uid = bet_id or f"{pos}{num}@{amount}"
            groups.setdefault(key, []).append(
                {"betId": uid, "pos": pos, "num": num, "amount": amount})
    return groups


def _current_domain(browser, prefer_page=None):
    """找出采集账号当前登录的代理线路域名，如 https://11313740-luk.mm555.co。
    优先用登录后所在页(prefer_page)的域名，其次已登录后台页(/Home/Index、/ReportNew)，
    最后任意 luk 域名。取错域名会导致重拼的报表URL没有会话、抓不到数据。"""
    def dom_of(url):
        m = re.match(r'(https?://[^/]+)', url or '')
        return m.group(1) if (m and 'luk.' in m.group(1)) else None

    if prefer_page is not None:
        try:
            d = dom_of(prefer_page.url)
            if d:
                return d
        except Exception:
            pass
    candidates = []
    try:
        for ctx in browser.contexts:
            for pg in ctx.pages:
                d = dom_of(pg.url)
                if d:
                    candidates.append((pg.url, d))
    except Exception:
        pass
    for url, d in candidates:   # 优先已登录后台页，避免选到登录页
        if '/Home/Index' in url or '/ReportNew' in url:
            return d
    return candidates[0][1] if candidates else None


def _current_agent_ids(browser):
    """从已登录的代理后台页里读出当前会话的 uid 和 loginId（后台报表链接里都带）。
    代理账号可能换（df788→kan3772…），uid/loginId 随之变，必须用实时值而非粘贴的旧值。"""
    js = """() => {
        var as = document.querySelectorAll('a[href]');
        for (var i=0;i<as.length;i++){
            var h = as[i].getAttribute('href')||'';
            if (h.indexOf('uid=')>=0 && h.indexOf('loginId=')>=0){
                var q = h.split('?')[1]||'', p={};
                q.split('&').forEach(function(kv){var j=kv.indexOf('=');if(j>0)p[kv.slice(0,j)]=decodeURIComponent(kv.slice(j+1));});
                if(p.uid && p.loginId) return {uid:p.uid, loginId:p.loginId};
            }
        }
        return null;
    }"""
    try:
        for ctx in browser.contexts:
            for pg in ctx.pages:
                try:
                    res = pg.evaluate(js)
                    if res and res.get("uid") and res.get("loginId"):
                        return res["uid"], res["loginId"]
                except Exception:
                    continue
    except Exception:
        pass
    return None, None


def _rebuild_report_url(url, domain, today, uid=None, login_id=None):
    """把粘贴的未结明细URL换成【当前域名 + 今天日期 + 当前代理uid/loginId】。
    固定不变的只有 querydata 里的 userid/orgId(客户身份)；域名/日期/代理身份都实时替换。"""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if "querydata" in params:
        qd = json.loads(params["querydata"][0])
        qd["startDate"] = today
        qd["endDate"] = today
        params["querydata"] = [json.dumps(qd, ensure_ascii=False, separators=(",", ":"))]
    if uid:
        params["uid"] = [uid]
    if login_id:
        params["loginId"] = [login_id]
    new_query = "&".join(
        f"{k}={urllib.parse.quote(v[0], safe='')}" for k, v in params.items())
    base = domain or (parsed.scheme + "://" + parsed.netloc)
    return f"{base}{parsed.path}?{new_query}"


def _merge_groups(all_groups: dict, g: dict):
    """把一个报表页的解析结果 g={(账号,期号):[注单dict,...]} 合并进 all_groups。
    多报表页可能有重叠，按下注编号(betId)去重。"""
    for k, v in g.items():
        if k not in all_groups:
            all_groups[k] = list(v)
        else:
            seen = {b["betId"] for b in all_groups[k]}
            for b in v:
                if b["betId"] not in seen:
                    all_groups[k].append(b)
                    seen.add(b["betId"])


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
            time.sleep(1.5)   # 等 AJAX 刷新完表格（已压缩以贴身跟投，漏的下一轮补）
        else:
            time.sleep(1)
    except Exception:
        time.sleep(1.5)
    try:
        return _fetch_bets(rp)
    except Exception:
        return None


def _place_bet(page_b, bets_by_pos: dict, log) -> bool:
    """bets_by_pos: {pos: {num: amount}}，每个号填各自金额（按倍数跟投后的金额）。"""
    frame = _get_iframe(page_b) or page_b
    pairs = []  # [[inputId, amountStr], ...]
    for pos, num_amt in bets_by_pos.items():
        pfx = POS_PREFIX[pos]
        for n, amt in num_amt.items():
            pairs.append([f"{pfx}{n}", str(int(amt))])
    if not pairs:
        return False
    pairs_json = json.dumps(pairs)
    js_fill = f"""(function(){{
        var setVal=function(id,amt){{
            var el=document.getElementById(id);
            if(!el)return;
            var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
            s.call(el,amt);
            el.dispatchEvent(new Event('input',{{bubbles:true}}));
            el.dispatchEvent(new Event('change',{{bubbles:true}}));
        }};
        var ps={pairs_json};
        for(var i=0;i<ps.length;i++)setVal(ps[i][0],ps[i][1]);
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

    def record(self, period, port, bets, account=""):
        """bets: {pos: {num: amount}}，按号记录各自金额用于结算。account 用于流水显示账号名。"""
        slot = self.pending.setdefault(period, {}).setdefault(
            port, {"b1": {}, "b2": {}, "b3": {}, "acct": account or port})
        if account:
            slot["acct"] = account
        for pos in ["b1", "b2", "b3"]:
            for n, amt in bets.get(pos, {}).items():
                slot[pos][n] = amt

    def settle(self, period, draw_nums):
        if period in self.settled or period not in self.pending:
            return
        self.settled.add(period)
        self.periods += 1
        draw = {"b1": draw_nums[0], "b2": draw_nums[1], "b3": draw_nums[2]}
        period_profit = 0.0
        self.log(f"📒 结算期号: {period} | 开奖: {draw_nums}")
        for port, info in self.pending[period].items():
            acct = info.get("acct") or port
            pp = 0.0
            for pos, label in [("b1", "一球"), ("b2", "二球"), ("b3", "三球")]:
                num_amt = info.get(pos, {})
                if not num_amt:
                    continue
                total_bet = sum(num_amt.values())
                rebate_v = total_bet * self.rebate
                d = draw[pos]
                if d in num_amt:
                    win = num_amt[d] * self.odds
                    bp = win - total_bet + rebate_v
                    self.log(f"  [{acct}] {label}: ✅中{d} 赢{win:.2f}-投{total_bet}+退{rebate_v:.2f}={bp:+.2f}")
                else:
                    bp = -total_bet + rebate_v
                    self.log(f"  [{acct}] {label}: ❌未中{d} -{total_bet}+退{rebate_v:.2f}={bp:+.2f}")
                pp += bp
            period_profit += pp
            self.log(f"  [{acct}] 本期盈亏: {pp:+.2f}")
        self.total += period_profit
        self.log(f"📊 本期总盈亏: {period_profit:+.2f} | 累计: {self.total:+.2f} (共{self.periods}期)")


# ─── 服务入口 ─────────────────────────────────────────────────

def _cdp_login(browser, account, password, entry_url, safe_code, log, line_kw="会员线路"):
    """在 CDP 连接的浏览器上自动登录（复用自动下单的 _login）。
    line_kw：采集/代理账号用"代理线路"(管理员登录)，跟投会员账号用"会员线路"(用户登录)。
    只有填了账号密码才登录；成功返回登录后的 page，失败返回 None（可手动登录兜底）。"""
    if not (account and password):
        return None
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        return _login(pg, ctx, account, password, entry_url, safe_code, log, line_kw)
    except Exception as e:
        log(f"[{account}] 自动登录失败（可手动登录兜底）: {e}")
        return None


def run(config: dict, stop_event: threading.Event, log_queue: queue.Queue):
    def log(msg: str):
        log_queue.put({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})

    source_port = str(config.get("source_port", "9222"))
    followers_cfg = config.get("followers", [{"port": "9223", "multiplier": 1}])
    BET_START = config.get("bet_window_start", 120)
    BET_END = config.get("bet_window_end", 35)
    REFRESH = config.get("refresh_sec", 5)
    ODDS = config.get("odds", 9.92)
    REBATE = config.get("rebate", 0.0073)
    entry_url = config.get("entry_url", "")
    safe_code = config.get("safe_code", "")
    source_account = config.get("source_account", "")
    source_password = config.get("source_password", "")
    follow_targets = [t for t in (config.get("follow_targets") or [])
                      if isinstance(t, dict) and str(t.get("url", "")).strip()]

    follower_str = ', '.join(f"{f.get('port')}({f.get('multiplier', 1)}倍)" for f in followers_cfg)
    log(f"跟投服务启动 | 采集端口:{source_port} | 跟投:{follower_str} | 模式:镜像全跟·按客户金额倍数")

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

        # 采集账号自动登录（采集账号=代理/管理员，走"代理线路→管理员登录"）
        src_login_page = None
        if source_account and source_password:
            log(f"[A] 采集账号 {source_account} 自动登录中（代理线路·管理员登录）...")
            src_login_page = _cdp_login(browser_a, source_account, source_password, entry_url, safe_code, log, line_kw="代理线路")
            if src_login_page:
                log(f"[A] 采集账号已登录")

        # ── 步骤2：点开始后立即开好并登录所有跟投账号（不等采集注单明细）──
        followers = []
        for f_cfg in followers_cfg:
            port_b = str(f_cfg.get("port", "9223"))
            try:
                mult = float(f_cfg.get("multiplier", 1))
            except (TypeError, ValueError):
                mult = 1.0
            if mult <= 0:
                mult = 1.0
            f_acc = f_cfg.get("account", "")
            f_pwd = f_cfg.get("password", "")

            # 自动开浏览器：该端口连不上就启动一个（点开始后全自动，无需手动打开）
            try:
                bb = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port_b}")
            except Exception:
                log(f"[B:{port_b}] 自动启动浏览器...")
                _launch_source_browser(port_b, entry_url, log)
                bb = None
                for _ in range(20):
                    if stop_event.is_set():
                        break
                    try:
                        bb = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port_b}")
                        break
                    except Exception:
                        time.sleep(2)
            if bb is None:
                log(f"[B:{port_b}] ❌ 浏览器启动/连接失败，跳过")
                continue

            try:
                _patch_stealth(bb)
            except Exception:
                pass

            # 自动登录（填了账号密码才登录，否则用已登录的浏览器）
            if f_acc and f_pwd:
                log(f"[B:{port_b}] 自动登录 {f_acc}（会员线路·用户登录）...")
                _cdp_login(bb, f_acc, f_pwd, entry_url, safe_code, log)

            pg = _find_cd_page(bb) or _get_real_page(bb) or \
                (bb.contexts[0].pages[0] if bb.contexts and bb.contexts[0].pages else None)
            followers.append((port_b, mult, bb, pg, f_acc))
            log(f"[B:{port_b}] 已就绪 {mult}倍跟投")

        if not followers:
            log("❌ 无可用的跟投账号")
            return

        # 目标客户未结明细：用【当前登录域名+今天日期】重拼粘贴的URL并自动打开（免去手动找页面）
        if follow_targets:
            dom = _current_domain(browser_a, src_login_page)
            cur_uid, cur_login = _current_agent_ids(browser_a)
            today = date.today().strftime("%Y-%m-%d")
            log(f"[A] 目标客户URL重拼：域名 {dom or '(用原URL域名)'} | 代理 {cur_login or '(未取到,用原loginId)'} | 日期 {today}")
            opened = 0
            for t in follow_targets:
                try:
                    rebuilt = _rebuild_report_url(str(t["url"]).strip(), dom, today, cur_uid, cur_login)
                    tp = browser_a.contexts[0].new_page()
                    tp.goto(rebuilt, wait_until="domcontentloaded", timeout=30000)
                    opened += 1
                    log(f"[A] 已自动打开目标客户未结明细页: {t.get('label') or ''}")
                except Exception as e:
                    log(f"[A] 打开目标未结明细失败({t.get('label') or ''}): {e}")
            if opened:
                time.sleep(2)

        # ── 步骤3：等待采集账号的注单明细页 ──
        if follow_targets:
            log(f"⏳ 已按目标客户自动打开未结明细页，开始监控...")
        else:
            log(f"⏳ 跟投账号已就绪。请把采集账号页面点到【报表查询 → 注单明细】（可提前打开空的未结明细页，等待中...）")
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

        # 初始化分页
        try:
            page_a.evaluate(_SET_COUNT_JS)
            time.sleep(2)
        except Exception:
            pass

        tracker = _Tracker(ODDS, REBATE, log)
        done_set: set = set()   # 已成功跟投的 (跟投端口, 下注编号, 球, 号)，逐账号独立去重/重试
        total_bets = 0
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
                    for per in sorted(tracker.pending.keys(), reverse=True):
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
                    _sleep_interruptible(cd + 10, stop_event)
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
                    _merge_groups(all_groups, g)

            if not all_groups:
                time.sleep(REFRESH)
                continue

            for (account, period), bets in all_groups.items():
                if stop_event.is_set():
                    break

                # 期号验证：报表期号需与投注页当前期号一致，避免跟到已封盘的旧期
                bet_period = _get_current_period(cd_page)
                if bet_period and bet_period != period:
                    log(f"  ⚠️  期号不匹配! 报表={period} 投注页={bet_period}，跳过")
                    continue

                # 镜像全跟：每个跟投账号各自按倍数跟客户全部注单
                # 按 (跟投端口, 下注编号, 球, 号) 独立去重 —— 客户每注每账号只跟一次，失败下次自动补
                for f_port, f_mult, f_browser, f_page, f_acc in followers:
                    acc_tag = f_acc or f_port
                    pending = [b for b in bets
                               if (f_port, b["betId"], b["pos"], b["num"]) not in done_set]
                    if not pending:
                        continue
                    my_bets: dict = {}
                    for b in pending:
                        amt = max(1, int(round(b["amount"] * f_mult)))   # 客户金额×倍数
                        my_bets.setdefault(b["pos"], {})[b["num"]] = amt
                    rp = _find_cd_page(f_browser) or _get_real_page(f_browser) or f_page
                    ok = _place_bet(rp, my_bets, log)
                    if ok:
                        for b in pending:
                            done_set.add((f_port, b["betId"], b["pos"], b["num"]))
                        total_bets += len(pending)
                        bet_placed_this_period = True
                        tracker.record(period, f_port, my_bets, acc_tag)
                        amt_desc = ", ".join(
                            f"{pos}:{'/'.join(str(a) for a in na.values())}"
                            for pos, na in my_bets.items())
                        log(f"    ✅ [{acc_tag}] {f_mult}倍 跟客户{len(pending)}注 [{amt_desc}] | 累计{total_bets}注")
                    else:
                        log(f"    ❌ [{acc_tag}] 失败，下次重试")
                    time.sleep(1)

            time.sleep(REFRESH)

        log(f"⏹️ 跟投停止 | 共跟投{total_bets}注 | 自算累计: {tracker.total:+.2f}")

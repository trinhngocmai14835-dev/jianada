"""本轮新增功能回归测试（无需浏览器、不下真单、不碰真实数据库）。

覆盖：
  1. 目标客户 URL 重建 _rebuild_report_url：换域名 + 换今天日期、userid等固定参数保留
  2. 当前域名提取 _current_domain：从已登录页面识别代理线路域名
  3. 流水落盘过滤 _persist_flow：只存有用流水、正确提取[账号]、噪音日志跳过
  4. 流水库 add_flow/get_flow/flow_accounts/clear_flow（用临时DB，不污染真实库）
"""
import os
import sys
import json
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def check(cond, msg):
    if not cond:
        raise AssertionError("❌ " + msg)
    print(f"  ✅ {msg}")


# ── 1. 目标客户 URL 重建 ─────────────────────────────────────
def test_rebuild_report_url():
    print("[1] _rebuild_report_url：换域名+换日期、userid保留")
    from services.follow_bet_svc import _rebuild_report_url
    qd = {"userid": "7a599ABC", "startDate": "2026-07-01", "endDate": "2026-07-01",
          "settlementStatus": "0", "lotteryType": "JND282,JND28WEB"}
    url = ("https://73642149-luk.cc555.co/ReportNew/BettingDetail?querydata="
           + urllib.parse.quote(json.dumps(qd)) + "&uid=ORG&loginId=df788&level=12")
    out = _rebuild_report_url(url, "https://11313740-luk.mm555.co", "2026-07-02")

    check(out.startswith("https://11313740-luk.mm555.co/ReportNew/BettingDetail?"),
          "域名换成当前登录域名、路径保留")
    # 解析回来核对
    p = urllib.parse.urlparse(out)
    params = urllib.parse.parse_qs(p.query)
    nqd = json.loads(params["querydata"][0])
    check(nqd["startDate"] == "2026-07-02" and nqd["endDate"] == "2026-07-02", "日期换成今天")
    check(nqd["userid"] == "7a599ABC", "userid(客户)保留不变")
    check(nqd["settlementStatus"] == "0", "settlementStatus=0(未结)保留")
    check(params["loginId"][0] == "df788" and params["uid"][0] == "ORG", "loginId/uid(代理身份)保留")
    check("2026-07-01" not in out, "旧日期已被替换干净")

    # domain 传 None 时应回退用原URL域名
    out2 = _rebuild_report_url(url, None, "2026-07-02")
    check(out2.startswith("https://73642149-luk.cc555.co"), "domain=None 时回退原域名")


# ── 2. 当前域名提取 ─────────────────────────────────────────
class _FakePage:
    def __init__(self, url):
        self.url = url

class _FakeCtx:
    def __init__(self, pages):
        self.pages = pages

class _FakeBrowser:
    def __init__(self, ctxs):
        self.contexts = ctxs


def test_current_domain():
    print("[2] _current_domain：识别代理线路域名（优先登录页/后台页）")
    from services.follow_bet_svc import _current_domain
    b = _FakeBrowser([_FakeCtx([
        _FakePage("chrome://newtab/"),
        _FakePage("https://11313740-luk.mm555.co/Home/Index"),
    ])])
    check(_current_domain(b) == "https://11313740-luk.mm555.co", "从多标签里挑出 luk 域名")

    b2 = _FakeBrowser([_FakeCtx([_FakePage("https://www.166dh2.com/Site/Show")])])
    check(_current_domain(b2) is None, "只有门户页(无luk)时返回 None")

    # prefer_page 优先：登录后所在页的域名最可靠
    prefer = _FakePage("https://11313740-luk.mm555.co/Home/Index")
    b3 = _FakeBrowser([_FakeCtx([_FakePage("https://99999999-luk.cc555.co/Member/Login")])])
    check(_current_domain(b3, prefer) == "https://11313740-luk.mm555.co",
          "有 prefer_page 时优先用它的域名（而非残留登录页）")

    # 无 prefer 时，优先已登录后台页(/Home/Index)而非登录页(/Member/Login)
    b4 = _FakeBrowser([_FakeCtx([
        _FakePage("https://88888888-luk.cc555.co/Member/Login"),
        _FakePage("https://11313740-luk.mm555.co/Home/Index"),
    ])])
    check(_current_domain(b4) == "https://11313740-luk.mm555.co",
          "优先 /Home/Index 后台页，跳过残留登录页")


# ── 3. 流水落盘过滤 ─────────────────────────────────────────
def test_persist_flow_filter():
    print("[3] _persist_flow：只存有用流水、正确提取账号、噪音跳过")
    import api.ws as ws
    captured = []
    ws.add_flow = lambda mode, account, msg, ts=None: captured.append((mode, account, msg))

    # 有用流水（应入库）
    ws._persist_flow("followbet", {"msg": "    ✅ [sxwd02] 1.0倍 跟客户5注 [b1:700/700]", "time": "12:00:00"})
    ws._persist_flow("rushbet", {"msg": "[ab99] 📊 开奖: [4, 6, 3] | 利润: +1200", "time": "12:00:01"})
    ws._persist_flow("autobet", {"msg": "[df788] 登录完成，当前页: .../Home/Index", "time": "12:00:02"})
    # 噪音日志（应跳过）
    ws._persist_flow("followbet", {"msg": "⏳ 未找到注单明细页，请点击...", "time": "12:00:03"})
    ws._persist_flow("rushbet", {"msg": "[ab99] ⏱ 等待下注窗口 | 倒计时100s", "time": "12:00:04"})
    ws._persist_flow("followbet", {"msg": "📡 倒计时120s，抓取注单...", "time": "12:00:05"})

    check(len(captured) == 3, f"3条有用流水入库、3条噪音跳过，实得 {len(captured)}")
    accts = [c[1] for c in captured]
    check(accts == ["sxwd02", "ab99", "df788"], f"账号正确提取自[方括号]：{accts}")
    check(captured[0][0] == "followbet", "模式取自 task_id")


# ── 4. 流水库增删查（临时DB）────────────────────────────────
def test_flow_db():
    print("[4] 流水库 add/get/accounts/clear（临时DB）")
    import core.db as db
    tmp = os.path.join(tempfile.gettempdir(), f"flow_test_{os.getpid()}.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    db.DB_PATH = tmp
    db.init_db()

    db.add_flow("followbet", "sxwd02", "跟客户5注", "2026-07-02 12:00:00")
    db.add_flow("followbet", "sxwd02", "本期盈亏: +800", "2026-07-02 12:03:00")
    db.add_flow("rushbet", "ab99", "下注成功", "2026-07-02 12:01:00")

    all_recs = db.get_flow()
    check(len(all_recs) == 3, f"共3条，实得 {len(all_recs)}")
    check(all_recs[0]["id"] > all_recs[-1]["id"], "按 id 倒序（最新在前）")

    sx = db.get_flow(account="sxwd02")
    check(len(sx) == 2, f"按账号筛选 sxwd02=2条，实得 {len(sx)}")

    accts = db.flow_accounts()
    names = sorted(a["account"] for a in accts)
    check(names == ["ab99", "sxwd02"], f"账号清单 {names}")
    cnt = {a["account"]: a["count"] for a in accts}
    check(cnt["sxwd02"] == 2 and cnt["ab99"] == 1, "各账号条数正确")

    db.clear_flow(account="sxwd02")
    check(len(db.get_flow(account="sxwd02")) == 0, "清空该账号后为0")
    check(len(db.get_flow()) == 1, "其他账号不受影响，剩1条")

    db.clear_flow()  # 清空全部
    check(len(db.get_flow()) == 0, "清空全部后为0")

    os.remove(tmp)


def test_merge_groups():
    print("[5] _merge_groups：多报表页按注单列表+betId去重合并（曾报 list indices 错）")
    from services.follow_bet_svc import _merge_groups
    all_groups = {}
    key = ("ab1351", "P1")
    # 第一张报表页
    _merge_groups(all_groups, {key: [
        {"betId": "N1", "pos": "b1", "num": 2, "amount": 350},
        {"betId": "N2", "pos": "b1", "num": 4, "amount": 350},
    ]})
    check(len(all_groups[key]) == 2, "首页2注")
    # 第二张报表页：同key重叠(N2重复)+新增N3 —— 关键：v是列表，不能用 v['b1']
    _merge_groups(all_groups, {key: [
        {"betId": "N2", "pos": "b1", "num": 4, "amount": 350},
        {"betId": "N3", "pos": "b2", "num": 7, "amount": 100},
    ]})
    ids = sorted(b["betId"] for b in all_groups[key])
    check(ids == ["N1", "N2", "N3"], f"按betId去重合并，实得 {ids}")
    # 另一个客户key独立
    _merge_groups(all_groups, {("ab1352", "P1"): [{"betId": "M1", "pos": "b3", "num": 9, "amount": 500}]})
    check(len(all_groups) == 2 and len(all_groups[("ab1352", "P1")]) == 1, "不同客户各自独立")


def main():
    print("=" * 56)
    print("本轮新增功能 回归测试")
    print("=" * 56)
    for fn in [test_rebuild_report_url, test_current_domain,
               test_persist_flow_filter, test_flow_db, test_merge_groups]:
        fn()
    print("=" * 56)
    print("🎉 全部通过")


if __name__ == "__main__":
    main()

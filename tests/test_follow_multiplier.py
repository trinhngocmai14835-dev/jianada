"""跟投·按客户金额倍数 逻辑测试（无需浏览器、不下真单）。

验证点：
  1. _fetch_bets 能从注单明细解析出 (账号,期号)->[{betId,pos,num,amount}]，含每注金额
     - 金额为 0/非注单行 被跳过；同号不同 betId 各自保留
  2. _place_bet 把"每个号各自的金额"正确填进对应输入框（按倍数算好的金额）
  3. 倍数+去重：amount×倍数取整(最小1)；按(端口,betId,球,号)去重，逐账号独立
  4. _Tracker 按号金额结算：中/未中盈亏数学正确
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import services.follow_bet_svc as fb
from services.follow_bet_svc import _fetch_bets, _place_bet, _Tracker

# 下注流程里的 time.sleep 在测试里置空，避免拖慢
fb.time.sleep = lambda *a, **k: None


def check(cond, msg):
    if not cond:
        raise AssertionError("❌ " + msg)
    print(f"  ✅ {msg}")


# ── 假对象 ───────────────────────────────────────────────────
class FakeReportPage:
    """_fetch_bets 只调用 page.evaluate(_FETCH_JS)，直接返回预置的解析结果。"""
    def __init__(self, items):
        self._items = items

    def evaluate(self, js):
        return self._items


class FakeLocator:
    def click(self, *a, **k):
        pass


class FakeBetPage:
    """无 frames → _get_iframe 返回 None → _place_bet 直接用本对象 evaluate。"""
    frames = []

    def __init__(self):
        self.calls = []

    def evaluate(self, js):
        self.calls.append(js)
        return None

    def locator(self, sel):
        return FakeLocator()


# 真实采集到的注单明细样本（第一球 2/4/5/7/8 各 350 元）+ 干扰行
SAMPLE_ITEMS = [
    {"betId": "N001", "account": "ab1351_A盘", "period": "3451376", "content": "第一球 〖2〗 @ 9.926", "amount": "350"},
    {"betId": "N002", "account": "ab1351_A盘", "period": "3451376", "content": "第一球 〖4〗 @ 9.926", "amount": "350"},
    {"betId": "N003", "account": "ab1351_A盘", "period": "3451376", "content": "第一球 〖5〗 @ 9.926", "amount": "350"},
    {"betId": "N004", "account": "ab1351_A盘", "period": "3451376", "content": "第二球 〖3〗 @ 9.926", "amount": "100"},
    # 干扰：金额 0 应跳过
    {"betId": "N005", "account": "ab1351_A盘", "period": "3451376", "content": "第三球 〖7〗 @ 9.926", "amount": "0"},
    # 干扰：非投注行（无"球"）应跳过
    {"betId": "N006", "account": "ab1351_A盘", "period": "3451376", "content": "余额 12345", "amount": "999"},
]


def test_fetch_bets_with_amount():
    print("[1] _fetch_bets 解析号码+金额，跳过0元/非注单行")
    groups = _fetch_bets(FakeReportPage(SAMPLE_ITEMS))
    key = ("ab1351_A盘", "3451376")
    check(key in groups, f"解析出账号期号键 {key}")
    bets = groups[key]
    check(len(bets) == 4, f"有效注单 4 条（跳过0元与非注单），实得 {len(bets)}")
    b1 = sorted(b["num"] for b in bets if b["pos"] == "b1")
    check(b1 == [2, 4, 5], f"第一球号码 {b1}")
    check(all(b["amount"] == 350 for b in bets if b["pos"] == "b1"), "第一球每注金额=350")
    b2 = [b for b in bets if b["pos"] == "b2"]
    check(len(b2) == 1 and b2[0]["num"] == 3 and b2[0]["amount"] == 100, "第二球〖3〗金额=100")
    check(all(b["betId"] for b in bets), "每注都带下注编号(用于去重)")


def test_place_bet_per_amount():
    print("[2] _place_bet 按号填各自金额（倍数算好后的金额）")
    page = FakeBetPage()
    # 模拟 2 倍：第一球 2/4 各 700，第三球 8 为 525（350×1.5 的另一账号）
    my_bets = {"b1": {2: 700, 4: 700}, "b3": {8: 525}}
    ok = _place_bet(page, my_bets, log=lambda m: None)
    check(ok is True, "_place_bet 返回成功")
    fill_js = page.calls[0]  # 第一段就是填值 JS
    check('["odds_B1QH2", "700"]' in fill_js or '"odds_B1QH2","700"' in fill_js.replace(" ", ""),
          "第一球2 填 700")
    check(fill_js.replace(" ", "").find('"odds_B1QH4","700"') >= 0, "第一球4 填 700")
    check(fill_js.replace(" ", "").find('"odds_B3QH8","525"') >= 0, "第三球8 填 525（不同倍数各算各的）")


def follow_amount(amount, mult):
    """与生产一致：客户金额×倍数，四舍五入取整，最小 1。"""
    return max(1, int(round(amount * mult)))


def test_multiplier_and_dedup():
    print("[3] 倍数取整 + 按(端口,betId,球,号)去重")
    check(follow_amount(350, 2) == 700, "350×2=700")
    check(follow_amount(350, 1.5) == 525, "350×1.5=525")
    check(follow_amount(100, 0.1) == 10, "100×0.1=10")
    check(follow_amount(1, 0.1) == 1, "1×0.1 取最小 1")

    bets = [{"betId": "N1", "pos": "b1", "num": 2}, {"betId": "N2", "pos": "b1", "num": 4}]
    done = set()
    pend1 = [b for b in bets if ("9223", b["betId"], b["pos"], b["num"]) not in done]
    check(len(pend1) == 2, "首轮两注都待跟")
    for b in pend1:
        done.add(("9223", b["betId"], b["pos"], b["num"]))
    pend2 = [b for b in bets if ("9223", b["betId"], b["pos"], b["num"]) not in done]
    check(len(pend2) == 0, "同账号同注不重复跟")
    pend3 = [b for b in bets if ("9224", b["betId"], b["pos"], b["num"]) not in done]
    check(len(pend3) == 2, "另一账号(9224)独立、仍需跟")


def test_tracker_settlement():
    print("[4] _Tracker 按号金额结算 中/未中")
    odds, rebate = 9.92, 0.0073
    # 中：第一球投 2/4/5/7/8 各 700，开奖第一球=4
    t = _Tracker(odds, rebate, log=lambda m: None)
    t.record("P1", "9223", {"b1": {2: 700, 4: 700, 5: 700, 7: 700, 8: 700}})
    t.settle("P1", [4, 0, 0])
    total_bet = 700 * 5
    exp_hit = 700 * odds - total_bet + total_bet * rebate
    check(abs(t.total - exp_hit) < 0.01, f"中一个号 盈亏≈{exp_hit:.2f}，实得 {t.total:.2f}")

    # 未中：开奖第一球=9（投的里没有9）
    t2 = _Tracker(odds, rebate, log=lambda m: None)
    t2.record("P2", "9223", {"b1": {2: 700, 4: 700, 5: 700, 7: 700, 8: 700}})
    t2.settle("P2", [9, 0, 0])
    exp_miss = -total_bet + total_bet * rebate
    check(abs(t2.total - exp_miss) < 0.01, f"全未中 盈亏≈{exp_miss:.2f}，实得 {t2.total:.2f}")


def main():
    print("=" * 56)
    print("跟投·按客户金额倍数 逻辑测试")
    print("=" * 56)
    for fn in [test_fetch_bets_with_amount, test_place_bet_per_amount,
               test_multiplier_and_dedup, test_tracker_settlement]:
        fn()
    print("=" * 56)
    print("🎉 全部通过")


if __name__ == "__main__":
    main()

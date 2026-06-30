"""条件赢冲输缩 档位状态机测试（无需浏览器）。

验证点：
  1. 后端版 _next_tier 与独立版 next_tier 行为一致、档位表一致
  2. 升档阈值边界（严格大于）
  3. 档3 封顶不再升
  4. 回正立即归档1
  5. 完整回放用户描述的场景：档1 →亏2000→休眠3期→档2 →亏3000→休眠3期→档3 →回正→档1
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                       # 独立脚本 auto_bet_mode3
sys.path.insert(0, os.path.join(ROOT, "backend"))  # 后端 services 包

import auto_bet_mode3 as standalone
from services.rush_bet_svc import (
    _next_tier as backend_next_tier,
    TIERS as B_TIERS,
    LOSS_THRESHOLDS as B_THRESH,
    SLEEP_PERIODS as B_SLEEP,
)

TIERS = standalone.TIERS
LOSS_THRESHOLDS = standalone.LOSS_THRESHOLDS
SLEEP_PERIODS = standalone.SLEEP_PERIODS


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✅ {msg}")


def test_constants_match():
    print("[1] 两版常量一致")
    check(TIERS == B_TIERS == [(50, 70), (70, 98), (100, 140)], f"档位表 {TIERS}")
    check(LOSS_THRESHOLDS == B_THRESH == [2000, 3000], f"阈值 {LOSS_THRESHOLDS}")
    check(SLEEP_PERIODS == B_SLEEP == 3, f"休眠期数 {SLEEP_PERIODS}")


def test_two_versions_identical():
    print("[2] 后端版与独立版 next_tier 行为完全一致")
    profits = range(-6000, 1001, 137)
    for tier in range(len(TIERS)):
        for p in profits:
            check(standalone.next_tier(tier, p) == backend_next_tier(tier, p),
                  f"tier={tier} profit={p} 一致") if p == -6000 else None
            assert standalone.next_tier(tier, p) == backend_next_tier(tier, p)
    print("  ✅ 全网格(3档×约44个利润点)结果逐一相等")


def test_threshold_boundary():
    print("[3] 升档阈值为严格大于")
    check(standalone.next_tier(0, -2000) == (0, "hold"), "亏损=2000 不升档(边界)")
    check(standalone.next_tier(0, -2001) == (1, "upgrade"), "亏损=2001 升档2")
    check(standalone.next_tier(1, -3000) == (1, "hold"), "档2 亏损=3000 不升档(边界)")
    check(standalone.next_tier(1, -3001) == (2, "upgrade"), "档2 亏损=3001 升档3")


def test_tier3_capped():
    print("[4] 档3 封顶不再升")
    check(standalone.next_tier(2, -5000) == (2, "hold"), "档3 巨亏仍 hold")
    check(standalone.next_tier(2, -99999) == (2, "hold"), "档3 永不升档4")


def test_reset_on_recovery():
    print("[5] 回正(利润>=0)立即归档1")
    check(standalone.next_tier(2, 0) == (0, "reset"), "档3 利润=0 归档1")
    check(standalone.next_tier(1, 50) == (0, "reset"), "档2 利润>0 归档1")
    check(standalone.next_tier(0, 100) == (0, "hold"), "已在档1 不触发reset")


def simulate(profit_seq):
    """按真实循环顺序回放：每期先做档位评估，再决定下注/休眠。
    返回每期记录 (tier, action, did_bet, base) 列表。"""
    tier, sleep_remaining = 0, 0
    records = []
    for profit in profit_seq:
        new_tier, action = standalone.next_tier(tier, profit)
        if action == "reset":
            tier, sleep_remaining = new_tier, 0
        elif action == "upgrade":
            tier, sleep_remaining = new_tier, SLEEP_PERIODS
        base, rush = TIERS[tier]
        if sleep_remaining > 0:
            sleep_remaining -= 1
            records.append((tier, action, "SLEEP", base))
        else:
            records.append((tier, action, "BET", base))
    return records


def test_full_scenario():
    print("[6] 完整场景回放：50/70 →亏2000→休眠3→70/98 →亏3000→休眠3→100/140 →回正→50/70")
    # 每期开始时观察到的累计利润
    seq = [
        0,       # P0 档1 下注
        -2200,   # P1 升档2 + 休眠(3->2) 跳过
        -2200,   # P2 休眠(2->1) 跳过
        -2200,   # P3 休眠(1->0) 跳过
        -2200,   # P4 档2 下注
        -3100,   # P5 升档3 + 休眠(3->2) 跳过
        -3100,   # P6 休眠 跳过
        -3100,   # P7 休眠 跳过
        -3100,   # P8 档3 下注
        -5000,   # P9 档3 巨亏仍 hold，下注
        80,      # P10 回正 归档1 下注
    ]
    rec = simulate(seq)
    expected = [
        (0, "hold",    "BET",   50),
        (1, "upgrade", "SLEEP", 70),
        (1, "hold",    "SLEEP", 70),
        (1, "hold",    "SLEEP", 70),
        (1, "hold",    "BET",   70),
        (2, "upgrade", "SLEEP", 100),
        (2, "hold",    "SLEEP", 100),
        (2, "hold",    "SLEEP", 100),
        (2, "hold",    "BET",   100),
        (2, "hold",    "BET",   100),
        (0, "reset",   "BET",   50),
    ]
    for i, (got, exp) in enumerate(zip(rec, expected)):
        check(got == exp, f"P{i}: {got}")
    check(len(rec) == len(expected), "期数对齐")

    bets = [r for r in rec if r[2] == "BET"]
    sleeps = [r for r in rec if r[2] == "SLEEP"]
    check(len(sleeps) == 6, f"共休眠6期(两次升档各3期)，实际{len(sleeps)}")
    check(len(bets) == 5, f"共下注5期，实际{len(bets)}")


class AccountState:
    """复刻 _betting_loop 中与档位相关的 per-账号 局部状态，用于多账号隔离验证。"""
    def __init__(self):
        self.tier = 0
        self.sleep_remaining = 0
        self.base, self.rush = TIERS[0]

    def on_period(self, profit):
        """处理一期：先档位评估，再决定下注/休眠。返回 (tier, base, 是否下注)。"""
        new_tier, action = standalone.next_tier(self.tier, profit)
        if action == "reset":
            self.tier, self.sleep_remaining = new_tier, 0
            self.base, self.rush = TIERS[self.tier]
        elif action == "upgrade":
            self.tier, self.sleep_remaining = new_tier, SLEEP_PERIODS
            self.base, self.rush = TIERS[self.tier]
        if self.sleep_remaining > 0:
            self.sleep_remaining -= 1
            return self.tier, self.base, False  # 休眠跳过
        return self.tier, self.base, True


def test_multi_account_isolation():
    print("[7] 多账号隔离：两个号不同利润路径，各自独立升/降档互不影响")
    A, B = AccountState(), AccountState()
    # 账号A 一路亏到档3；账号B 全程盈利始终档1。两者交错处理。
    a_profits = [0, -2200, -2200, -2200, -2200, -3100, -3100, -3100, -3100]
    b_profits = [0,  +300,  +500,  +800, +1200, +1500, +1800, +2000, +2500]
    a_tiers, b_tiers = [], []
    for pa, pb in zip(a_profits, b_profits):   # 交错：每期先A后B
        ta, _, _ = A.on_period(pa)
        tb, _, _ = B.on_period(pb)
        a_tiers.append(ta)
        b_tiers.append(tb)
    check(a_tiers[-1] == 2, f"账号A 最终档3(下标2)，实际下标{a_tiers[-1]}")
    check(set(b_tiers) == {0}, f"账号B 始终档1(下标0)，实际{sorted(set(b_tiers))}")
    check(A.base == 100 and B.base == 50, f"A底注{A.base}/B底注{B.base} 各自独立")

    # 再验证：A 突然回正应只影响A，B 不受任何影响
    A.on_period(120)   # A 回正 → 归档1
    check(A.tier == 0, "账号A 回正后归档1")
    check(B.tier == 0, "账号B 不受A回正影响（本就档1）")


def test_configurable_tiers():
    print("[8] 前端可配置档位：自定义档位表/阈值/解析与回退")
    from services.rush_bet_svc import (
        _parse_tiers, _parse_thresholds, _next_tier as bnt,
    )
    # dict 形态（前端实际传参）与 list 形态都可解析
    check(_parse_tiers([{"base": 100, "rush": 140}, {"base": 200, "rush": 280}])
          == [(100, 140), (200, 280)], "dict 形态档位解析")
    check(_parse_tiers([[50, 70]]) == [(50, 70)], "list 形态档位解析")
    check(_parse_thresholds([5000]) == [5000], "阈值解析")
    # 自定义档位下的升/降/封顶/回正
    tiers = [(100, 140), (200, 280)]
    thr = [5000]
    check(bnt(0, -5000, tiers, thr) == (0, "hold"), "自定义阈值边界不升")
    check(bnt(0, -5001, tiers, thr) == (1, "upgrade"), "超自定义阈值升档")
    check(bnt(1, -99999, tiers, thr) == (1, "hold"), "自定义末档封顶")
    check(bnt(1, 0, tiers, thr) == (0, "reset"), "自定义档位回正归档1")
    # 非法/空输入回退（返回 None，让运行时用内置默认）
    check(_parse_tiers(None) is None, "空档位回退")
    check(_parse_tiers([{"base": 0, "rush": 70}]) is None, "非正数档位判非法")
    check(_parse_tiers("garbage") is None, "脏数据档位判非法")
    check(_parse_thresholds([]) is None and _parse_thresholds(["x"]) is None, "空/脏阈值回退")
    # 不传档位参数时仍等价于内置默认（保证旧 2 参调用兼容）
    check(bnt(0, -2001) == (1, "upgrade"), "默认参数调用不变")


if __name__ == "__main__":
    for fn in [test_constants_match, test_two_versions_identical,
               test_threshold_boundary, test_tier3_capped,
               test_reset_on_recovery, test_full_scenario,
               test_multi_account_isolation, test_configurable_tiers]:
        fn()
    print("\n🎉 全部通过")

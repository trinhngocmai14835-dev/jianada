import sys
import time
import random
import json
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

# ================= 策略配置：3路4球随机 · 每期必投 · 赢冲输缩 =================
BASE_BET_AMOUNT = 500      # 一阶底注
RUSH_BET_AMOUNT = 700      # 二阶赢冲注码
NUMBERS_PER_POS = 4        # 每位置随机选4个号码
NUM_POSITIONS = 3          # 3个独立位置（第一球、第二球、第三球）

# 风控配置（只保留止盈止损）
RUN_START_HOUR = 9         # 早上9点开机
RUN_END_HOUR = 21          # 晚上21点关机
DAILY_STOP_LOSS = 29000    # 止损线：亏满此数强制关机
ABSOLUTE_TAKE_PROFIT = 25000 # 止盈线：赚满此数立即收工
# =========================================================================

# ================= 自算帐配置 =================
ODDS = 9.92                # 单球号码赔率
REBATE_RATE = 0.0073       # 退水比例（0.73%）
AUDIT_SHARED_FILE = "self_audit.json"  # 共享文件，供监控脚本读取对比
# ==============================================

class BetAuditor:
    """自算帐模块 - 支持独立通道分别下注结算"""
    def __init__(self, label="", odds=ODDS, rebate=REBATE_RATE):
        self.label = label
        self.odds = odds
        self.rebate = rebate
        self.total_self_profit = 0.0
        self.total_bets = 0
        self.pending_bet = None
        script_dir = os.path.dirname(os.path.abspath(__file__))
        tag = f"_{label}" if label else ""
        self.log_path = os.path.join(script_dir, f"bet_audit{tag}.log")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"自算帐启动 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"赔率: {odds} | 退水: {rebate*100:.2f}%\n")
            f.write(f"{'='*80}\n")
    
    def record_bet(self, period_draw, target_numbers, bet_amounts):
        self.pending_bet = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "period_id": str(period_draw),
            "targets": target_numbers,
            "amounts": bet_amounts,
        }
    
    def settle(self, draw_numbers):
        if self.pending_bet is None:
            return None
        targets = self.pending_bet["targets"]
        amounts = self.pending_bet["amounts"]
        period_profit = 0.0
        period_rebate = 0.0
        details = []
        ball_names = ["第一球", "第二球", "第三球"]
        
        for i in range(NUM_POSITIONS):
            if not targets[i] or amounts[i] == 0:
                continue
                
            ball_bet_count = len(targets[i])
            ball_total_bet = amounts[i] * ball_bet_count
            ball_rebate = ball_total_bet * self.rebate
            period_rebate += ball_rebate
            
            if draw_numbers[i] in targets[i]:
                win_amount = amounts[i] * self.odds
                ball_profit = win_amount - ball_total_bet + ball_rebate
                details.append(f"  {ball_names[i]}: 开{draw_numbers[i]} ✅中! 赢{win_amount:.2f} - 投{ball_total_bet} + 退{ball_rebate:.2f} = {ball_profit:+.2f}")
            else:
                ball_profit = -ball_total_bet + ball_rebate
                details.append(f"  {ball_names[i]}: 开{draw_numbers[i]} ❌未中 亏{ball_total_bet} + 退{ball_rebate:.2f} = {ball_profit:+.2f}")
            period_profit += ball_profit
        
        self.total_self_profit += period_profit
        self.total_bets += 1
        tag = f"[{self.label}] " if self.label else ""
        print(f"{tag}📒 【自算帐】本期结算:")
        for d in details:
            print(f"{tag}{d}")
        print(f"{tag}  本期净输赢: {period_profit:+.2f} (含退水 {period_rebate:.2f})")
        print(f"{tag}  自算累计输赢: {self.total_self_profit:+.2f} (共出手{self.total_bets}期)")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- 第{self.total_bets}次协同拔枪 | {self.pending_bet['time']} ---\n")
            for d in details:
                f.write(f"{d}\n")
            f.write(f"本期: {period_profit:+.2f} | 累计: {self.total_self_profit:+.2f}\n")
        self.pending_bet = None
        self._update_shared_file()
        return period_profit
    
    def _update_shared_file(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        shared_path = os.path.join(script_dir, AUDIT_SHARED_FILE)
        data = {}
        try:
            if os.path.exists(shared_path):
                with open(shared_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except:
            data = {}
        key = self.label or "default"
        data[key] = {
            "total_profit": round(self.total_self_profit, 2),
            "total_bets": self.total_bets,
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            with open(shared_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except:
            pass

def get_current_balance(page):
    try:
        balance_str = page.locator("#accountLimit_0").inner_text(timeout=5000).strip()
        return float(balance_str.replace(',', ''))
    except:
        return None

def get_countdown(page):
    try:
        frame = page.frame(name="frame")
        if not frame: return -1
        time_str = frame.locator("#cdClose").inner_text(timeout=3000).strip()
        if ':' in time_str:
            m, s = time_str.split(':')
            return int(m) * 60 + int(s)
        if time_str.isdigit():
            return int(time_str)
        return 0
    except:
        return -2

def place_bet(page, pos_numbers, amounts):
    try:
        print(f"💰 正在独立通道填单...")
        total_cost = 0
        for i, nums in enumerate(pos_numbers):
            if len(nums) > 0 and amounts[i] > 0:
                cost = amounts[i] * len(nums)
                total_cost += cost
                print(f"    定位{i+1}选号({len(nums)}个): {nums} | 单注: {amounts[i]} | 成本: {cost}")
        print(f"    本期协同拔枪，总成本: {total_cost} 元")
        
        frame = page.frame(name="frame")
        if not frame: return
        js_code = """
            var setVal = function(id, amount) {
                var el = document.querySelector('#' + id);
                if(el) {
                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                    nativeInputValueSetter.call(el, amount);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter', code: 'Enter' }));
                }
            };
        """
        if amounts[0] > 0:
            for num in pos_numbers[0]: js_code += f"setVal('odds_B1QH{num}', '{amounts[0]}');\n"
        if amounts[1] > 0:
            for num in pos_numbers[1]: js_code += f"setVal('odds_B2QH{num}', '{amounts[1]}');\n"
        if amounts[2] > 0:
            for num in pos_numbers[2]: js_code += f"setVal('odds_B3QH{num}', '{amounts[2]}');\n"
            
        frame.evaluate(js_code)
        time.sleep(1)
        frame.evaluate("var btn = document.querySelector('#btnOk'); if(btn) { btn.click(); }")
        time.sleep(1.5)
        frame.evaluate("""
            var btns = document.querySelectorAll('input[type="button"], button');
            for(var i=0; i<btns.length; i++) {
                if(btns[i].value === '确定' || (btns[i].innerText && btns[i].innerText.includes('确定'))) {
                    if(btns[i].id !== 'btnOk') {
                        btns[i].click();
                        break;
                    }
                }
            }
        """)
        print("✅ 填单并确认完成！")
    except Exception as e:
        print(f"❌ 填写下注单异常: {e}")

def get_history(page):
    try:
        elements = page.locator("b[class^='b']").all()
        draw_nums = []
        for i in range(min(3, len(elements))):
            num_text = elements[i].inner_text(timeout=2000).strip()
            if num_text.isdigit():
                draw_nums.append(int(num_text))
        if len(draw_nums) == 3: return draw_nums
        return None
    except:
        return None

def get_history_deep(page, limit=100):
    try:
        page.evaluate("""() => { var link = document.querySelector('a[href*="ResultHistory"]'); if (link) link.click(); }""")
        time.sleep(3)
        history = page.evaluate("""(limit) => {
            var iframe = document.querySelector('iframe');
            if (!iframe || !iframe.contentDocument) return [];
            var doc = iframe.contentDocument;
            var rows = doc.querySelectorAll('#betList tr');
            var data = [];
            for (var r = 0; r < rows.length && data.length < limit; r++) {
                var spans = rows[r].querySelectorAll('span.ico, span[class*="ico b"]');
                var nums = [];
                for (var s = 0; s < spans.length; s++) {
                    var n = spans[s].innerText.trim();
                    if (n.match(/^\\d$/)) nums.push(parseInt(n));
                }
                if (nums.length >= 3) data.push(nums.slice(0, 3));
            }
            return data;
        }""", limit)
        page.evaluate("""() => { var link = document.querySelector('a[href*="page=hm13"]'); if (link) link.click(); }""")
        time.sleep(2)
        return history if history else []
    except:
        try:
            page.evaluate("""() => { var link = document.querySelector('a[href*="page=hm13"]'); if (link) link.click(); }""")
        except: pass
        return []

def random_pick(num_count):
    """每次完全随机选4个号码，不依赖历史数据。
    30天11371期回测验证：随机码30天合计+5697元，热号-24507元。
    热号命中率仅比随机高0.6%，但波动极大，不如随机稳定。"""
    return sorted(random.sample(range(10), num_count))

def run_betting(page, label="", base_amount=BASE_BET_AMOUNT, rush_amount=RUSH_BET_AMOUNT):
    tag = f"[{label}] " if label else ""
    page.on("dialog", lambda dialog: dialog.accept())

    start_balance = get_current_balance(page)
    if start_balance is None: start_balance = 0

    auditor = BetAuditor(label=label, odds=ODDS, rebate=REBATE_RATE)

    print(f"{tag}💰 初始本金: {start_balance}")
    print(f"{tag}🕵️ 运行规则: {RUN_START_HOUR}:00-{RUN_END_HOUR}:00 | 止损 -{DAILY_STOP_LOSS} | 止盈 +{ABSOLUTE_TAKE_PROFIT}")
    print(f"{tag}🎯 策略: 3路4球随机 · 每期必投 · 一阶{base_amount}元 · 赢冲二阶{rush_amount}元 · 输缩回一阶")

    # ===== 状态：只需记录各位置当前阶段和上期投注号码 =====
    pos_steps = [1, 1, 1]              # 1=底注阶, 2=赢冲阶
    current_targets = [None, None, None]  # 上期投注号码（结算用）
    last_recorded_draw = None
    bet_placed_this_period = False
    # ======================================================

    while True:
        # ---------------- 时间栅栏 ----------------
        current_hour = datetime.now().hour
        if current_hour < RUN_START_HOUR or current_hour >= RUN_END_HOUR:
            print(f"{tag}🌙 [宵禁] 当前 {current_hour} 点，非收割时段，待机中...")
            time.sleep(60)
            continue

        current_balance = get_current_balance(page)
        if current_balance is None:
            time.sleep(3)
            continue

        profit = current_balance - start_balance

        # ---------------- 止盈 / 止损 ----------------
        if profit >= ABSOLUTE_TAKE_PROFIT:
            print(f"\n{tag}🎉 【止盈】今日利润 {profit:.0f} 达到 +{ABSOLUTE_TAKE_PROFIT}！收工下班！")
            sys.exit(0)

        if profit <= -DAILY_STOP_LOSS:
            print(f"\n{tag}🩸 【止损】亏损 {profit:.0f}，触碰 -{DAILY_STOP_LOSS} 红线！强行关机！")
            sys.exit(0)

        # ---------------- 新期开奖 → 结算 → 赢冲输缩 ----------------
        last_draw = get_history(page)

        if last_draw and last_draw != last_recorded_draw:
            print(f"\n{tag}=======================================================")
            print(f"{tag}📊 最新开奖: {last_draw} | 当前利润: {profit:+.0f}")

            if bet_placed_this_period:
                auditor.settle(last_draw)
                bet_placed_this_period = False

                # 赢冲输缩：逐位置判断
                for i in range(NUM_POSITIONS):
                    if current_targets[i] is None:
                        continue
                    hit = last_draw[i] in current_targets[i]
                    if hit:
                        if pos_steps[i] == 1:
                            pos_steps[i] = 2
                            print(f"{tag}🔥 [位置{i+1}] 命中！一阶→二阶赢冲 ({base_amount}→{rush_amount})")
                        else:
                            print(f"{tag}💰 [位置{i+1}] 命中！继续保持二阶赢冲 ({rush_amount})")
                    else:
                        if pos_steps[i] == 2:
                            pos_steps[i] = 1
                            print(f"{tag}💀 [位置{i+1}] 未中，二阶→一阶输缩 ({rush_amount}→{base_amount})")
                        else:
                            print(f"{tag}💀 [位置{i+1}] 未中，保持一阶底注 ({base_amount})")

            last_recorded_draw = last_draw

        # ---------------- 开枪时机：每期必投 ----------------
        countdown = get_countdown(page)
        if countdown < 0:
            time.sleep(2)
            continue

        if 60 <= countdown <= 120 and not bet_placed_this_period:
            # 每期重新随机选号
            bet_targets = [random_pick(NUMBERS_PER_POS) for _ in range(NUM_POSITIONS)]
            bet_amounts = [rush_amount if pos_steps[i] == 2 else base_amount for i in range(NUM_POSITIONS)]

            step_info = " | ".join(f"位置{i+1}:{'二阶' if pos_steps[i]==2 else '一阶'}({bet_amounts[i]})" for i in range(NUM_POSITIONS))
            print(f"{tag}🎲 本期选号: {bet_targets}")
            print(f"{tag}⚡ {step_info}")

            random_delay = random.uniform(2.0, 6.0)
            print(f"{tag}⏳ 距封盘 {countdown}s，延时 {random_delay:.2f}s 后出手...")
            time.sleep(random_delay)

            current_countdown = get_countdown(page)
            if current_countdown > 10:
                place_bet(page, bet_targets, bet_amounts)
                bet_placed_this_period = True
                current_targets = bet_targets
                auditor.record_bet(last_recorded_draw, bet_targets, bet_amounts)

                remain = get_countdown(page)
                if remain > 0:
                    sleep_time = remain + 10
                    print(f"{tag}🚬 子弹已出膛，等待开奖 ({sleep_time}s)...")
                    time.sleep(sleep_time)
            else:
                print(f"{tag}⚠️ 封盘太快，取消本期下注。")
        else:
            time.sleep(5)


def input_amount(prompt, default):
    try:
        val = input(f"{prompt} [默认{default}]: ").strip()
        return int(val) if val else default
    except:
        return default

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else '9222'

    print("=" * 50)
    print("  3路4球随机 · 赢冲输缩 自动下注系统")
    print("=" * 50)
    base_amount = input_amount("请输入一阶底注金额", BASE_BET_AMOUNT)
    rush_amount = input_amount("请输入二阶赢冲金额", RUSH_BET_AMOUNT)
    print(f"✅ 注码设定：一阶 {base_amount} 元 / 二阶 {rush_amount} 元")
    print("=" * 50)

    with sync_playwright() as p:
        print(f"🔗 正在通过 {port} 端口连接...")
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            page = browser.contexts[0].pages[0]
            print(f"✅ 成功接管页面: {page.title()}")
        except Exception as e:
            print("❌ 连接失败！请检查端口。")
            sys.exit(1)

        run_betting(page, base_amount=base_amount, rush_amount=rush_amount)

if __name__ == "__main__":
    main()
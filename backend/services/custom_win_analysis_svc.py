from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.custom_win_bet_svc import (
    DEFAULT_AMOUNT_STEPS,
    DEFAULT_NUMBER_SETS,
    NUM_POSITIONS,
    _CustomWinPathState,
    _parse_amount_steps,
    _parse_custom_number_sets,
)
from services.draw_analysis_svc import DrawRecord, normalize_draw_records
from services.rotate_bet_svc import _parse_enabled_positions, _parse_entry_miss_trigger, _parse_risk_limit


BALL_LABELS = ["第一球", "第二球", "第三球"]
DEFAULT_NUMBER_ODDS = 9.92


@dataclass
class Metric:
    bets: int = 0
    wins: int = 0
    stake: float = 0.0
    profit: float = 0.0
    equity: float = 0.0
    peak: float = 0.0
    max_drawdown: float = 0.0
    miss_streak: int = 0
    max_miss_streak: int = 0
    win_streak: int = 0
    max_win_streak: int = 0

    def add(self, stake: float, profit: float, hit: bool) -> None:
        self.bets += 1
        if hit:
            self.wins += 1
            self.win_streak += 1
            self.miss_streak = 0
        else:
            self.miss_streak += 1
            self.win_streak = 0
        self.max_win_streak = max(self.max_win_streak, self.win_streak)
        self.max_miss_streak = max(self.max_miss_streak, self.miss_streak)
        self.stake += stake
        self.profit += profit
        self.equity += profit
        self.peak = max(self.peak, self.equity)
        self.max_drawdown = max(self.max_drawdown, self.peak - self.equity)


@dataclass
class PathMetric:
    metric: Metric = field(default_factory=Metric)
    sets: dict[str, Metric] = field(default_factory=lambda: {"A": Metric(), "B": Metric()})
    tiers: list[Metric] = field(default_factory=list)
    max_tier_reached: int = 1


def _round(value: float, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _metric_dict(metric: Metric) -> dict[str, Any]:
    return {
        "bets": metric.bets,
        "wins": metric.wins,
        "hit_rate": _rate(metric.wins, metric.bets),
        "stake": _round(metric.stake),
        "profit": _round(metric.profit),
        "roi": _round(metric.profit / metric.stake if metric.stake else 0.0, 4),
        "max_drawdown": _round(metric.max_drawdown),
        "max_miss_streak": metric.max_miss_streak,
        "max_win_streak": metric.max_win_streak,
    }


def _settle_number_group(amount: int, group_size: int, hit: bool, odds: float, rebate_rate: float) -> tuple[float, float]:
    stake = float(amount) * float(group_size)
    payout = float(amount) * float(odds) if hit else 0.0
    rebate = stake * max(0.0, float(rebate_rate or 0))
    return stake, payout - stake + rebate


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _set_label(path: _CustomWinPathState) -> str:
    return "A" if path.set_idx == 0 else "B"


def _observe_entry(paths, number_sets, draw: DrawRecord, enabled, targets=None, rotate_after=False) -> list[int]:
    activated = []
    for pos in range(NUM_POSITIONS):
        if not enabled[pos] or paths[pos].active:
            continue
        nums = list(targets[pos]) if targets is not None else list(number_sets[pos][paths[pos].set_idx])
        hit = draw.numbers[pos] in nums
        if paths[pos].observe_entry(hit):
            activated.append(pos)
        if rotate_after:
            paths[pos].rotate()
    return activated


def _make_plan(paths, number_sets, enabled):
    targets = [list(number_sets[pos][paths[pos].set_idx]) for pos in range(NUM_POSITIONS)]
    active = [enabled[pos] and paths[pos].active for pos in range(NUM_POSITIONS)]
    if not any(active):
        return None
    plan = {
        "targets": targets,
        "active": active,
        "amounts": [paths[pos].get_bet() if active[pos] else 0 for pos in range(NUM_POSITIONS)],
        "tiers": [paths[pos].tier_index for pos in range(NUM_POSITIONS)],
        "sets": [_set_label(paths[pos]) for pos in range(NUM_POSITIONS)],
    }
    for pos in range(NUM_POSITIONS):
        if enabled[pos]:
            paths[pos].rotate()
    return plan


def _overall_risk(summary: dict[str, Any], stop_loss: float) -> str:
    profit = float(summary.get("profit") or 0)
    drawdown = float(summary.get("max_drawdown") or 0)
    if summary.get("bets", 0) < 20:
        return "样本不足"
    if profit <= 0:
        return "高"
    if stop_loss > 0 and drawdown >= stop_loss * 0.6:
        return "高"
    if drawdown > max(profit * 2, 1):
        return "中"
    return "低"


def _position_advice(row: dict[str, Any]) -> str:
    if not row.get("enabled"):
        return "已关闭"
    if row.get("bets", 0) < 8:
        return "样本不足，先观察"
    if row.get("profit", 0) <= 0:
        return "回测亏损，建议先关闭或重配号码组"
    if row.get("roi", 0) < 0.01:
        return "盈利偏薄，建议继续观察"
    if row.get("max_drawdown", 0) > max(row.get("profit", 0) * 2.5, 1):
        return "回撤偏大，谨慎开启"
    return "表现正常，可保留"


def _build_suggestions(summary: dict[str, Any], positions: list[dict[str, Any]], amount_steps: list[int]) -> list[dict[str, str]]:
    suggestions = []
    if summary["bets"] < 20:
        suggestions.append({"level": "warning", "text": "有效回测下注不足 20 次，当前结论只能当观察参考。"})
    elif summary["profit"] <= 0:
        suggestions.append({"level": "error", "text": "当前配置整体回测为亏损，第一版不建议直接实盘放大。"})
    elif summary["roi"] < 0.01:
        suggestions.append({"level": "warning", "text": "当前配置整体利润为正，但 ROI 偏薄，建议先小额观察。"})
    else:
        suggestions.append({"level": "success", "text": "当前配置整体回测为正，可以继续看分球位风险。"})

    for row in positions:
        if row["enabled"] and row["bets"] >= 8 and row["profit"] <= 0:
            suggestions.append({"level": "error", "text": f"{row['name']} 回测亏损 {row['profit']}，建议先关闭该球路或调整 A/B 号码组。"})
        elif row["enabled"] and row["bets"] >= 8 and row["max_drawdown"] > max(row["profit"] * 2.5, 1):
            suggestions.append({"level": "warning", "text": f"{row['name']} 最大回撤 {row['max_drawdown']} 偏高，适合继续观察，不适合加大金额。"})

        if row["enabled"]:
            for label, set_row in row["sets"].items():
                if set_row["bets"] >= 5 and set_row["profit"] < 0:
                    suggestions.append({"level": "warning", "text": f"{row['name']} {label}组回测亏损 {set_row['profit']}，后续可优先优化这组号码。"})

    if len(amount_steps) >= 4:
        last_tier_hits = sum(1 for row in positions if row.get("max_tier_reached", 1) >= len(amount_steps))
        if last_tier_hits:
            suggestions.append({"level": "warning", "text": f"历史回测中有 {last_tier_hits} 路触达最后一阶，阶梯金额需要控制回撤。"})
    return suggestions


def analyze_custom_winbet_config(config: dict[str, Any], records: Any = None, text: str = "", limit: int = 1000) -> dict[str, Any]:
    cfg = dict(config or {})
    normalized = normalize_draw_records(records, text)
    limit = min(5000, max(20, int(limit or 1000)))
    normalized = normalized[-limit:]
    if len(normalized) < 12:
        return {
            "ok": False,
            "message": "自定义轮换赢冲分析至少需要 12 条开奖记录，请先到“开奖记录分析推荐”抓取真实数据。",
            "records": len(normalized),
        }

    base_bet = int(_parse_float(cfg.get("base_bet_amount"), 100))
    amount_steps = _parse_amount_steps(cfg.get("amount_steps"), base_bet) or DEFAULT_AMOUNT_STEPS[:]
    number_sets = _parse_custom_number_sets(cfg.get("number_sets")) or DEFAULT_NUMBER_SETS
    enabled = _parse_enabled_positions(cfg.get("enabled_positions"))
    entry_misses = _parse_entry_miss_trigger(cfg.get("entry_miss_trigger", 1))
    odds = _parse_float(cfg.get("odds"), DEFAULT_NUMBER_ODDS)
    rebate_rate = _parse_float(cfg.get("rebate_rate", cfg.get("rebate", 0)), 0.0)
    stop_loss = _parse_risk_limit(cfg.get("daily_stop_loss", 29000), 29000)

    if not any(enabled):
        return {"ok": False, "message": "至少需要启用一路球，才能分析自定义轮换赢冲。", "records": len(normalized)}

    paths = [_CustomWinPathState(amount_steps, entry_misses) for _ in range(NUM_POSITIONS)]
    overall = Metric()
    path_metrics = [PathMetric(tiers=[Metric() for _ in amount_steps]) for _ in range(NUM_POSITIONS)]
    pending = _make_plan(paths, number_sets, enabled)
    settled_issues = 0

    for record in normalized[1:]:
        if pending is not None:
            issue_profit = 0.0
            issue_stake = 0.0
            issue_hit = False
            issue_has_bet = False
            for pos in range(NUM_POSITIONS):
                if not pending["active"][pos]:
                    continue
                amount = int(pending["amounts"][pos])
                nums = list(pending["targets"][pos])
                tier_index = min(int(pending["tiers"][pos]), len(amount_steps) - 1)
                set_label = pending["sets"][pos]
                hit = record.numbers[pos] in nums
                stake, profit = _settle_number_group(amount, len(nums), hit, odds, rebate_rate)
                issue_stake += stake
                issue_profit += profit
                issue_hit = issue_hit or hit
                issue_has_bet = True

                metrics = path_metrics[pos]
                metrics.max_tier_reached = max(metrics.max_tier_reached, tier_index + 1)
                metrics.metric.add(stake, profit, hit)
                metrics.sets[set_label].add(stake, profit, hit)
                metrics.tiers[tier_index].add(stake, profit, hit)
                if hit:
                    paths[pos].on_win()
                else:
                    paths[pos].on_lose()

            if issue_has_bet:
                overall.add(issue_stake, issue_profit, issue_hit)
                settled_issues += 1
            _observe_entry(paths, number_sets, record, enabled, targets=pending["targets"])
            pending = None
        else:
            _observe_entry(paths, number_sets, record, enabled, rotate_after=True)

        pending = _make_plan(paths, number_sets, enabled)

    summary = _metric_dict(overall)
    path_bets = sum(item.metric.bets for item in path_metrics)
    path_wins = sum(item.metric.wins for item in path_metrics)
    summary.update(
        {
            "bets": path_bets,
            "wins": path_wins,
            "hit_rate": _rate(path_wins, path_bets),
            "records": len(normalized),
            "settled_issues": settled_issues,
            "amount_steps": amount_steps,
            "entry_miss_trigger": entry_misses,
            "odds": odds,
            "rebate_rate": rebate_rate,
            "latest_issue": normalized[-1].issue,
        }
    )
    summary["risk_level"] = _overall_risk(summary, stop_loss)

    positions = []
    for pos in range(NUM_POSITIONS):
        metrics = path_metrics[pos]
        row = _metric_dict(metrics.metric)
        row.update(
            {
                "position": pos + 1,
                "name": BALL_LABELS[pos],
                "enabled": enabled[pos],
                "set_a": list(number_sets[pos][0]),
                "set_b": list(number_sets[pos][1]),
                "sets": {label: _metric_dict(metric) for label, metric in metrics.sets.items()},
                "tiers": [
                    {"tier": index + 1, "amount": amount_steps[index], **_metric_dict(metric)}
                    for index, metric in enumerate(metrics.tiers)
                ],
                "max_tier_reached": metrics.max_tier_reached,
                "next_state": {
                    "active": paths[pos].active,
                    "set": _set_label(paths[pos]),
                    "tier": paths[pos].tier_index + 1,
                    "amount": paths[pos].get_bet(),
                    "entry_loss_count": paths[pos].entry_loss_count,
                },
            }
        )
        row["advice"] = _position_advice(row)
        positions.append(row)

    return {
        "ok": True,
        "summary": summary,
        "positions": positions,
        "suggestions": _build_suggestions(summary, positions, amount_steps),
        "recent_records": [
            {
                "issue": record.issue,
                "numbers": list(record.numbers),
                "total": record.total,
                "dx": record.dx,
                "ds": record.ds,
            }
            for record in reversed(normalized[-10:])
        ],
    }


def analyze_custom_winbet_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return analyze_custom_winbet_config(
        payload.get("config") or {},
        records=payload.get("records"),
        text=payload.get("text") or "",
        limit=payload.get("limit") or 1000,
    )

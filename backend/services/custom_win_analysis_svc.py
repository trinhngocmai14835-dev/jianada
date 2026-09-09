from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
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
DIGITS = tuple(range(10))


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


def _candidate_score(metric: dict[str, Any]) -> float:
    return _round(
        float(metric.get("profit") or 0)
        + float(metric.get("roi") or 0) * 120
        + float(metric.get("hit_rate") or 0) * 20
        + float(metric.get("max_win_streak") or 0) * 1.5
        - float(metric.get("max_drawdown") or 0) * 0.35
        - float(metric.get("max_miss_streak") or 0) * 1.2,
        4,
    )


def _group_heuristic(records: list[DrawRecord], position: int, group: tuple[int, ...]) -> float:
    recent = records[-min(80, len(records)):]
    group_set = set(group)
    total_rate = sum(1 for record in records if record.numbers[position] in group_set) / len(records)
    recent_rate = sum(1 for record in recent if record.numbers[position] in group_set) / len(recent) if recent else total_rate
    transitions = hits = 0
    for prev, curr in zip(records, records[1:]):
        if prev.numbers[position] in group_set:
            transitions += 1
            if curr.numbers[position] in group_set:
                hits += 1
    transition_rate = hits / transitions if transitions >= 5 else recent_rate
    current_miss = 0
    for record in reversed(records):
        if record.numbers[position] in group_set:
            break
        current_miss += 1
    return (recent_rate * 70) + (total_rate * 35) + (transition_rate * 35) + min(current_miss, 10) * 0.8


def _rank_groups(records: list[DrawRecord], position: int, size: int, top: int = 42) -> list[tuple[tuple[int, ...], float]]:
    ranked = [(tuple(combo), _group_heuristic(records, position, tuple(combo))) for combo in combinations(DIGITS, size)]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:top]


def _simulate_position_candidate(
    records: list[DrawRecord],
    position: int,
    set_a: list[int],
    set_b: list[int],
    amount_steps: list[int],
    entry_misses: int,
    odds: float,
    rebate_rate: float,
) -> dict[str, Any]:
    path = _CustomWinPathState(amount_steps, entry_misses)
    metric = Metric()
    set_metrics = {"A": Metric(), "B": Metric()}
    tier_metrics = [Metric() for _ in amount_steps]
    max_tier_reached = 1

    def current_target() -> list[int]:
        return list(set_a if path.set_idx == 0 else set_b)

    def observe(record: DrawRecord, target: list[int] | None = None, rotate_after: bool = False) -> None:
        if path.active:
            return
        nums = target if target is not None else current_target()
        path.observe_entry(record.numbers[position] in nums)
        if rotate_after:
            path.rotate()

    def make_plan():
        if not path.active:
            return None
        plan = {
            "target": current_target(),
            "amount": path.get_bet(),
            "tier": path.tier_index,
            "set": _set_label(path),
        }
        path.rotate()
        return plan

    pending = make_plan()
    settled = 0
    for record in records[1:]:
        if pending:
            target = list(pending["target"])
            amount = int(pending["amount"])
            tier_index = min(int(pending["tier"]), len(amount_steps) - 1)
            set_label = str(pending["set"])
            hit = record.numbers[position] in target
            stake, profit = _settle_number_group(amount, len(target), hit, odds, rebate_rate)
            metric.add(stake, profit, hit)
            set_metrics[set_label].add(stake, profit, hit)
            tier_metrics[tier_index].add(stake, profit, hit)
            max_tier_reached = max(max_tier_reached, tier_index + 1)
            if hit:
                path.on_win()
            else:
                path.on_lose()
            observe(record, target=target)
            pending = None
            settled += 1
        else:
            observe(record, rotate_after=True)
        pending = make_plan()

    out = _metric_dict(metric)
    out.update(
        {
            "set_a": list(set_a),
            "set_b": list(set_b),
            "group_size": [len(set_a), len(set_b)],
            "size_label": f"{len(set_a)}/{len(set_b)}",
            "sets": {label: _metric_dict(value) for label, value in set_metrics.items()},
            "tiers": [
                {"tier": index + 1, "amount": amount_steps[index], **_metric_dict(item)}
                for index, item in enumerate(tier_metrics)
            ],
            "max_tier_reached": max_tier_reached,
            "settled_issues": settled,
        }
    )
    out["score"] = _candidate_score(out)
    return out


def _candidate_pairs(
    records: list[DrawRecord],
    position: int,
    current_a: list[int],
    current_b: list[int],
    max_pairs: int = 360,
) -> list[tuple[list[int], list[int], str]]:
    groups_by_size = {
        4: _rank_groups(records, position, 4),
        5: _rank_groups(records, position, 5),
    }
    group_scores = {group: score for items in groups_by_size.values() for group, score in items}
    raw: list[tuple[float, tuple[int, ...], tuple[int, ...], str]] = []

    def add_pair(a, b, source: str, priority: float | None = None) -> None:
        a_tuple = tuple(sorted(int(v) for v in a))
        b_tuple = tuple(sorted(int(v) for v in b))
        if len(a_tuple) not in (4, 5) or len(b_tuple) not in (4, 5):
            return
        if len(set(a_tuple)) != len(a_tuple) or len(set(b_tuple)) != len(b_tuple):
            return
        if priority is None:
            priority = group_scores.get(a_tuple, 0.0) + group_scores.get(b_tuple, 0.0)
        raw.append((priority, a_tuple, b_tuple, source))

    add_pair(current_a, current_b, "current", 9999.0)
    for combo in combinations(DIGITS, 5):
        a = tuple(combo)
        b = tuple(d for d in DIGITS if d not in a)
        priority = _group_heuristic(records, position, a) + _group_heuristic(records, position, b) + 4
        add_pair(a, b, "5/5", priority)

    pool = groups_by_size[4] + groups_by_size[5]
    for a, a_score in pool:
        a_set = set(a)
        for b, b_score in pool:
            if a_set.intersection(b):
                continue
            add_pair(a, b, "ranked", a_score + b_score)

    raw.sort(key=lambda item: item[0], reverse=True)
    result = []
    seen = set()
    for _priority, a, b, source in raw:
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        result.append((list(a), list(b), source))
        if len(result) >= max_pairs:
            break
    return result


def _candidate_reasons(candidate: dict[str, Any], current: dict[str, Any] | None) -> list[str]:
    reasons = [
        f"{candidate['size_label']}个号轮换",
        f"回测利润 {candidate['profit']}",
        f"命中 {candidate['hit_rate'] * 100:.1f}%",
        f"最大回撤 {candidate['max_drawdown']}",
    ]
    if current:
        diff = float(candidate.get("profit") or 0) - float(current.get("profit") or 0)
        if diff > 0:
            reasons.append(f"比当前配置多 {diff:.2f}")
        elif diff < 0:
            reasons.append(f"比当前配置少 {abs(diff):.2f}")
    if candidate.get("max_tier_reached", 1) >= 4:
        reasons.append(f"最高触达 {candidate['max_tier_reached']} 阶")
    return reasons


def _recommend_position_groups(
    records: list[DrawRecord],
    position: int,
    current_a: list[int],
    current_b: list[int],
    amount_steps: list[int],
    entry_misses: int,
    odds: float,
    rebate_rate: float,
    current_position: dict[str, Any],
) -> dict[str, Any]:
    evaluated = []
    current_key = (tuple(sorted(current_a)), tuple(sorted(current_b)))
    current_candidate = None
    for set_a, set_b, source in _candidate_pairs(records, position, current_a, current_b):
        metric = _simulate_position_candidate(records, position, set_a, set_b, amount_steps, entry_misses, odds, rebate_rate)
        metric["source"] = source
        metric["is_current"] = (tuple(metric["set_a"]), tuple(metric["set_b"])) == current_key
        if metric["is_current"]:
            current_candidate = metric
        evaluated.append(metric)

    evaluated.sort(key=lambda item: item["score"], reverse=True)
    top = evaluated[0] if evaluated else None
    if current_candidate is None:
        current_candidate = _simulate_position_candidate(records, position, current_a, current_b, amount_steps, entry_misses, odds, rebate_rate)
        current_candidate["source"] = "current"
        current_candidate["is_current"] = True

    if not top:
        action = "暂无推荐"
    elif top.get("is_current"):
        action = "保持当前"
    elif top.get("profit", 0) <= 0:
        action = "不建议替换"
    else:
        improvement = float(top.get("profit") or 0) - float(current_candidate.get("profit") or 0)
        threshold = max(abs(float(current_candidate.get("profit") or 0)) * 0.08, 10.0)
        action = "建议替换" if improvement >= threshold else "差距不大"

    enabled_advice = "建议关闭"
    if top and top.get("profit", 0) > 0:
        enabled_advice = "谨慎开启" if top.get("max_drawdown", 0) > max(top.get("profit", 0) * 2.5, 1) else "建议开启/保留"

    top_candidates = [{**candidate, "reasons": _candidate_reasons(candidate, current_candidate)} for candidate in evaluated[:5]]
    recommended = {**top, "reasons": _candidate_reasons(top, current_candidate)} if top else None
    current_out = {
        "set_a": list(current_a),
        "set_b": list(current_b),
        "profit": current_position.get("profit", 0),
        "roi": current_position.get("roi", 0),
        "hit_rate": current_position.get("hit_rate", 0),
        "max_drawdown": current_position.get("max_drawdown", 0),
        "max_tier_reached": current_position.get("max_tier_reached", 1),
    }
    return {
        "position": position + 1,
        "name": BALL_LABELS[position],
        "action": action,
        "enabled_advice": enabled_advice,
        "current": current_out,
        "recommended": recommended,
        "candidates": top_candidates,
    }


def _build_number_recommendations(
    records: list[DrawRecord],
    number_sets,
    positions: list[dict[str, Any]],
    amount_steps: list[int],
    entry_misses: int,
    odds: float,
    rebate_rate: float,
) -> dict[str, Any]:
    by_position = []
    for pos in range(NUM_POSITIONS):
        by_position.append(
            _recommend_position_groups(
                records,
                pos,
                list(number_sets[pos][0]),
                list(number_sets[pos][1]),
                amount_steps,
                entry_misses,
                odds,
                rebate_rate,
                positions[pos],
            )
        )

    replace_count = sum(1 for row in by_position if row.get("action") == "建议替换")
    open_count = sum(1 for row in by_position if row.get("enabled_advice") == "建议开启/保留")
    return {
        "mode": "custom_winbet",
        "records": len(records),
        "method": "按赢冲状态机回测候选 A/B 号码组，优先看利润、ROI、最大回撤、最长不中和最高触达阶数。",
        "replace_count": replace_count,
        "open_count": open_count,
        "positions": by_position,
    }


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

    recommendations = _build_number_recommendations(normalized, number_sets, positions, amount_steps, entry_misses, odds, rebate_rate)

    return {
        "ok": True,
        "summary": summary,
        "positions": positions,
        "recommendations": recommendations,
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

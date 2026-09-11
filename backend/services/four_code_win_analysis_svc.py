from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import ceil
from typing import Any

from services.draw_analysis_svc import DrawRecord, normalize_draw_records
from services.rotate_bet_svc import _parse_enabled_positions, _parse_risk_limit

NUM_POSITIONS = 3
DIGITS = tuple(range(10))
BALL_LABELS = ["第一球", "第二球", "第三球"]
DEFAULT_AMOUNT_STEPS = [100, 130, 299, 389, 506]
DEFAULT_NUMBER_GROUPS = [[0, 1, 3, 8], [0, 1, 3, 8], [0, 1, 3, 8]]
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

    @property
    def current_drawdown(self) -> float:
        return max(0.0, self.peak - self.equity)


def _round(value: float, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_amount_steps(raw: Any, fallback_base: int = 100) -> list[int]:
    if isinstance(raw, str):
        raw = raw.replace("，", ",").split(",")
    if not isinstance(raw, (list, tuple)):
        raw = [fallback_base]
    out = []
    for item in list(raw)[:20]:
        try:
            value = int(float(item))
        except (TypeError, ValueError):
            continue
        if value > 0:
            out.append(value)
    return out or [max(1, int(fallback_base or 100))]


def _clean_four_numbers(raw: Any) -> list[int]:
    if isinstance(raw, str):
        raw = raw.replace("，", ",").split(",")
    out = []
    for item in raw or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 9 and value not in out:
            out.append(value)
    return sorted(out)


def parse_number_groups(raw: Any) -> list[list[int]] | None:
    if not raw or len(raw) < NUM_POSITIONS:
        return None
    out = []
    try:
        for index in range(NUM_POSITIONS):
            item = raw[index]
            if isinstance(item, dict):
                nums = _clean_four_numbers(item.get("numbers", []))
            else:
                nums = _clean_four_numbers(item)
            if len(nums) != 4:
                return None
            out.append(nums)
    except (TypeError, AttributeError):
        return None
    return out


class FourCodeWinState:
    def __init__(self, amount_steps: list[int]):
        self.amount_steps = [max(1, int(v)) for v in amount_steps[:20]] or DEFAULT_AMOUNT_STEPS[:]
        self.tier_index = 0

    def amount(self) -> int:
        return self.amount_steps[min(self.tier_index, len(self.amount_steps) - 1)]

    def on_win(self) -> bool:
        if self.tier_index >= len(self.amount_steps) - 1:
            self.tier_index = 0
            return True
        self.tier_index += 1
        return False

    def on_lose(self) -> bool:
        changed = self.tier_index != 0
        self.tier_index = 0
        return changed


def settle_four_code(amount: int, group_size: int, hit: bool, odds: float, rebate_rate: float) -> tuple[float, float]:
    stake = float(amount) * float(group_size)
    payout = float(amount) * float(odds) if hit else 0.0
    rebate = stake * max(0.0, float(rebate_rate or 0))
    return stake, payout - stake + rebate


def _metric_dict(metric: Metric) -> dict[str, Any]:
    return {
        "bets": metric.bets,
        "wins": metric.wins,
        "hit_rate": _rate(metric.wins, metric.bets),
        "stake": _round(metric.stake),
        "profit": _round(metric.profit),
        "roi": _round(metric.profit / metric.stake if metric.stake else 0.0, 4),
        "max_drawdown": _round(metric.max_drawdown),
        "current_drawdown": _round(metric.current_drawdown),
        "max_miss_streak": metric.max_miss_streak,
        "current_miss_streak": metric.miss_streak,
        "max_win_streak": metric.max_win_streak,
        "current_win_streak": metric.win_streak,
    }


def simulate_four_code_group(
    records: list[DrawRecord],
    position: int,
    numbers: list[int],
    amount_steps: list[int],
    odds: float,
    rebate_rate: float,
    budget: float = 0,
) -> dict[str, Any]:
    nums = _clean_four_numbers(numbers)
    if len(nums) != 4:
        raise ValueError("numbers must contain exactly four unique digits")
    state = FourCodeWinState(amount_steps)
    metric = Metric()
    tier_metrics = [Metric() for _ in state.amount_steps]
    max_tier_reached = 1

    for record in records:
        amount = state.amount()
        tier_index = min(state.tier_index, len(state.amount_steps) - 1)
        hit = record.numbers[position] in nums
        stake, profit = settle_four_code(amount, len(nums), hit, odds, rebate_rate)
        metric.add(stake, profit, hit)
        tier_metrics[tier_index].add(stake, profit, hit)
        max_tier_reached = max(max_tier_reached, tier_index + 1)
        if hit:
            state.on_win()
        else:
            state.on_lose()

    next_amount = state.amount()
    next_stake = next_amount * len(nums)
    budget_needed = ceil(max(metric.max_drawdown, metric.current_drawdown) + next_stake)
    budget_pass = budget <= 0 or budget_needed <= budget
    out = _metric_dict(metric)
    out.update(
        {
            "numbers": nums,
            "position": position + 1,
            "name": BALL_LABELS[position],
            "group_size": len(nums),
            "max_tier_reached": max_tier_reached,
            "next_state": {
                "tier": state.tier_index + 1,
                "amount": next_amount,
                "stake": next_stake,
            },
            "budget_needed": budget_needed,
            "budget_pass": budget_pass,
            "tiers": [
                {"tier": index + 1, "amount": state.amount_steps[index], **_metric_dict(item)}
                for index, item in enumerate(tier_metrics)
            ],
        }
    )
    out["score"] = _candidate_score(out, budget)
    out["advice"] = _candidate_advice(out, budget)
    out["reasons"] = _candidate_reasons(out, budget)
    return out


def _candidate_score(row: dict[str, Any], budget: float) -> float:
    over_budget = max(0.0, float(row.get("budget_needed") or 0) - max(0.0, budget)) if budget > 0 else 0.0
    return _round(
        float(row.get("current_drawdown") or 0) * 1.15
        + float(row.get("profit") or 0) * 0.12
        + float(row.get("hit_rate") or 0) * 140
        + float(row.get("roi") or 0) * 80
        + max(0, int(row.get("next_state", {}).get("tier") or 1) - 1) * 8
        - float(row.get("max_drawdown") or 0) * 0.32
        - float(row.get("max_miss_streak") or 0) * 2.5
        - over_budget * 1.8,
        4,
    )


def _candidate_advice(row: dict[str, Any], budget: float) -> str:
    if row.get("bets", 0) < 20:
        return "样本不足"
    if budget > 0 and not row.get("budget_pass"):
        return "准备金不足"
    if row.get("current_drawdown", 0) > 0 and row.get("profit", 0) > 0:
        return "可观察入场"
    if row.get("profit", 0) > 0:
        return "表现平稳"
    return "谨慎"


def _candidate_reasons(row: dict[str, Any], budget: float) -> list[str]:
    reasons = [
        f"当前回撤 {row.get('current_drawdown', 0)}",
        f"需备金额 {row.get('budget_needed', 0)}",
        f"下期第{row.get('next_state', {}).get('tier', 1)}阶 {row.get('next_state', {}).get('amount', 0)}元",
        f"命中 {float(row.get('hit_rate') or 0) * 100:.1f}%",
    ]
    if budget > 0:
        reasons.append("准备金内" if row.get("budget_pass") else f"超准备金 {row.get('budget_needed', 0) - budget:.0f}")
    if row.get("max_miss_streak", 0):
        reasons.append(f"最大连挂 {row.get('max_miss_streak')}")
    return reasons


PROFILE_DEFS = {
    "stable": {
        "key": "stable",
        "name": "稳健方案",
        "short_name": "稳健",
        "description": "优先挑准备金压力小、历史最大回撤低、最大连挂少的4粒码。",
    },
    "balanced": {
        "key": "balanced",
        "name": "均衡方案",
        "short_name": "均衡",
        "description": "兼顾当前回撤、历史利润、命中率和预算需求，作为默认选择。",
    },
    "drawdown": {
        "key": "drawdown",
        "name": "冲回撤方案",
        "short_name": "冲回撤",
        "description": "优先挑当前回测处在回撤里的4粒码，但仍要求预算能扛住。",
    },
}


def _profile_score(row: dict[str, Any], profile: str, budget: float) -> float:
    budget_needed = float(row.get("budget_needed") or 0)
    over_budget = max(0.0, budget_needed - max(0.0, budget)) if budget > 0 else 0.0
    current_drawdown = float(row.get("current_drawdown") or 0)
    max_drawdown = float(row.get("max_drawdown") or 0)
    profit = float(row.get("profit") or 0)
    hit_rate = float(row.get("hit_rate") or 0)
    roi = float(row.get("roi") or 0)
    max_miss = float(row.get("max_miss_streak") or 0)
    next_tier = int(row.get("next_state", {}).get("tier") or 1)

    if profile == "stable":
        score = (
            260
            + profit * 0.06
            + hit_rate * 120
            + roi * 60
            - budget_needed * 0.55
            - max_drawdown * 0.45
            - current_drawdown * 0.12
            - max_miss * 6
            - over_budget * 2.2
        )
    elif profile == "drawdown":
        score = (
            current_drawdown * 1.35
            + max(0, next_tier - 1) * 16
            + profit * 0.06
            + hit_rate * 90
            + roi * 50
            - max_drawdown * 0.28
            - max_miss * 4
            - over_budget * 2.4
        )
    else:
        score = _candidate_score(row, budget)
    if budget > 0 and row.get("budget_pass"):
        score += 80
    return _round(score, 4)


def _profile_reasons(row: dict[str, Any], budget: float, profile: str) -> list[str]:
    reasons = []
    if profile == "stable":
        reasons.append(f"需备金额 {row.get('budget_needed', 0)}")
        reasons.append(f"最大回撤 {row.get('max_drawdown', 0)}")
        reasons.append(f"最大连挂 {row.get('max_miss_streak', 0)}")
    elif profile == "drawdown":
        reasons.append(f"当前回撤 {row.get('current_drawdown', 0)}")
        reasons.append(f"下期第{row.get('next_state', {}).get('tier', 1)}阶")
        reasons.append(f"需备金额 {row.get('budget_needed', 0)}")
    else:
        reasons.append(f"当前回撤 {row.get('current_drawdown', 0)}")
        reasons.append(f"利润 {row.get('profit', 0)}")
        reasons.append(f"需备金额 {row.get('budget_needed', 0)}")
    reasons.append(f"命中 {float(row.get('hit_rate') or 0) * 100:.1f}%")
    if budget > 0:
        reasons.append("准备金内" if row.get("budget_pass") else f"超准备金 {row.get('budget_needed', 0) - budget:.0f}")
    return reasons


def _select_profile_candidate(evaluated: list[dict[str, Any]], profile: str, budget: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    ranked = sorted(
        evaluated,
        key=lambda item: (bool(item.get("budget_pass")), _profile_score(item, profile, budget)),
        reverse=True,
    )
    top_rows = []
    for item in ranked[:5]:
        row = dict(item)
        row["profile_score"] = _profile_score(row, profile, budget)
        row["profile_reasons"] = _profile_reasons(row, budget, profile)
        top_rows.append(row)
    selected = top_rows[0] if top_rows else None
    return selected, top_rows


def _rank_position_candidates(
    records: list[DrawRecord],
    position: int,
    amount_steps: list[int],
    odds: float,
    rebate_rate: float,
    budget: float,
    current_numbers: list[int],
) -> dict[str, Any]:
    current_key = tuple(_clean_four_numbers(current_numbers))
    evaluated = []
    current_candidate = None
    for combo in combinations(DIGITS, 4):
        candidate = simulate_four_code_group(records, position, list(combo), amount_steps, odds, rebate_rate, budget)
        candidate["is_current"] = tuple(candidate["numbers"]) == current_key
        if candidate["is_current"]:
            current_candidate = candidate
        evaluated.append(candidate)

    profile_map = {}
    for profile, meta in PROFILE_DEFS.items():
        selected, top_rows = _select_profile_candidate(evaluated, profile, budget)
        profile_map[profile] = {
            **meta,
            "recommended": selected,
            "candidates": top_rows,
        }

    evaluated.sort(key=lambda item: (bool(item.get("budget_pass")), float(item.get("score") or 0)), reverse=True)
    top = evaluated[0] if evaluated else None
    action = "暂无推荐"
    if top:
        if top.get("is_current"):
            action = "保持当前"
        elif budget > 0 and not top.get("budget_pass"):
            action = "准备金不足"
        else:
            improvement = float(top.get("score") or 0) - float((current_candidate or {}).get("score") or 0)
            action = "建议替换" if improvement >= 5 else "差距不大"

    return {
        "position": position + 1,
        "name": BALL_LABELS[position],
        "action": action,
        "current": current_candidate,
        "recommended": top,
        "profiles": profile_map,
        "candidates": evaluated[:8],
        "evaluated": len(evaluated),
        "budget_pass_count": sum(1 for item in evaluated if item.get("budget_pass")),
    }


def _plan_summary(profile: str, rows: list[dict[str, Any]], enabled: list[bool], budget: float) -> dict[str, Any]:
    active = [row for row in rows if row.get("enabled") and row.get("candidate")]
    bets = sum(int(row["candidate"].get("bets") or 0) for row in active)
    wins = sum(int(row["candidate"].get("wins") or 0) for row in active)
    stake = sum(float(row["candidate"].get("stake") or 0) for row in active)
    profit = sum(float(row["candidate"].get("profit") or 0) for row in active)
    max_drawdown = sum(float(row["candidate"].get("max_drawdown") or 0) for row in active)
    current_drawdown = sum(float(row["candidate"].get("current_drawdown") or 0) for row in active)
    budget_needed = ceil(sum(float(row["candidate"].get("budget_needed") or 0) for row in active))
    return {
        "profile": profile,
        "enabled_count": sum(1 for item in enabled if item),
        "bets": bets,
        "wins": wins,
        "hit_rate": _rate(wins, bets),
        "stake": _round(stake),
        "profit": _round(profit),
        "roi": _round(profit / stake if stake else 0.0, 4),
        "max_drawdown": _round(max_drawdown),
        "current_drawdown": _round(current_drawdown),
        "budget_needed": budget_needed,
        "suggested_budget": ceil(budget_needed * 1.2),
        "safe_budget": ceil(budget_needed * 1.5),
        "budget_pass": budget <= 0 or budget_needed <= budget,
    }


def _build_profile_plans(position_rows: list[dict[str, Any]], enabled: list[bool], budget: float) -> list[dict[str, Any]]:
    plans = []
    for profile, meta in PROFILE_DEFS.items():
        rows = []
        number_groups = []
        for index, row in enumerate(position_rows):
            candidate = row.get("profiles", {}).get(profile, {}).get("recommended")
            numbers = list(candidate.get("numbers") or []) if candidate else []
            if numbers:
                number_groups.append({"numbers": numbers})
            rows.append(
                {
                    "position": row.get("position"),
                    "name": row.get("name"),
                    "enabled": bool(enabled[index]),
                    "candidate": candidate,
                    "numbers": numbers,
                    "reasons": _profile_reasons(candidate, budget, profile) if candidate else [],
                }
            )
        summary = _plan_summary(profile, rows, enabled, budget)
        plans.append(
            {
                **meta,
                "summary": summary,
                "positions": rows,
                "recommended_config": {"number_groups": number_groups},
            }
        )
    return plans

def _build_summary(rows: list[dict[str, Any]], records_count: int, amount_steps: list[int], budget: float, odds: float, rebate_rate: float) -> dict[str, Any]:
    enabled = [row for row in rows if row.get("enabled")]
    bets = sum(int(row.get("bets") or 0) for row in enabled)
    wins = sum(int(row.get("wins") or 0) for row in enabled)
    stake = sum(float(row.get("stake") or 0) for row in enabled)
    profit = sum(float(row.get("profit") or 0) for row in enabled)
    max_drawdown = sum(float(row.get("max_drawdown") or 0) for row in enabled)
    current_drawdown = sum(float(row.get("current_drawdown") or 0) for row in enabled)
    budget_needed = sum(float(row.get("budget_needed") or 0) for row in enabled)
    return {
        "records": records_count,
        "bets": bets,
        "wins": wins,
        "hit_rate": _rate(wins, bets),
        "stake": _round(stake),
        "profit": _round(profit),
        "roi": _round(profit / stake if stake else 0.0, 4),
        "max_drawdown": _round(max_drawdown),
        "current_drawdown": _round(current_drawdown),
        "budget": _round(budget),
        "budget_needed": ceil(budget_needed),
        "budget_pass": budget <= 0 or budget_needed <= budget,
        "amount_steps": amount_steps,
        "odds": odds,
        "rebate_rate": rebate_rate,
    }


def _build_suggestions(summary: dict[str, Any], recommendations: dict[str, Any]) -> list[dict[str, str]]:
    suggestions = []
    if summary["records"] < 80:
        suggestions.append({"level": "warning", "text": "开奖记录少于 80 条，当前推荐只适合小额观察。"})
    if summary["budget"] > 0 and not summary["budget_pass"]:
        suggestions.append({"level": "warning", "text": f"当前配置需备金额约 {summary['budget_needed']}，已经超过手动准备金 {summary['budget']}。"})
    if summary["profit"] < 0:
        suggestions.append({"level": "warning", "text": "当前手动配置回测为亏损，建议优先看三套选号方案。"})
    replace_count = recommendations.get("replace_count", 0)
    if replace_count:
        suggestions.append({"level": "info", "text": f"按当前开奖记录回测，系统建议替换 {replace_count} 路 4 粒码。"})
    suggestions.append({"level": "info", "text": "回测用于估算风险和准备金，不代表下一期一定补回。"})
    return suggestions


def analyze_four_code_winbet_config(config: dict[str, Any], records: Any = None, text: str = "", limit: int = 1000) -> dict[str, Any]:
    cfg = dict(config or {})
    normalized = normalize_draw_records(records, text)
    limit = min(5000, max(20, int(limit or 1000)))
    normalized = normalized[-limit:]
    if len(normalized) < 12:
        return {
            "ok": False,
            "message": "4粒码赢冲输缩分析至少需要 12 条开奖记录，请先到“开奖记录分析推荐”抓取真实数据。",
            "records": len(normalized),
        }

    base_bet = int(_parse_float(cfg.get("base_bet_amount"), 100))
    amount_steps = _parse_amount_steps(cfg.get("amount_steps"), base_bet) or DEFAULT_AMOUNT_STEPS[:]
    groups = parse_number_groups(cfg.get("number_groups") or [{"numbers": item} for item in DEFAULT_NUMBER_GROUPS])
    if groups is None:
        return {"ok": False, "message": "每路球都必须配置 4 个不重复号码，才能分析4粒码赢冲输缩。", "records": len(normalized)}
    enabled = _parse_enabled_positions(cfg.get("enabled_positions"))
    budget = _parse_risk_limit(cfg.get("budget", cfg.get("analysis_budget", 0)), 0)
    odds = _parse_float(cfg.get("odds"), DEFAULT_NUMBER_ODDS)
    rebate_rate = _parse_float(cfg.get("rebate_rate", cfg.get("rebate", 0)), 0.0)

    if not any(enabled):
        return {"ok": False, "message": "至少需要启用一路球，才能分析4粒码赢冲输缩。", "records": len(normalized)}

    positions = []
    for pos in range(NUM_POSITIONS):
        row = simulate_four_code_group(normalized, pos, groups[pos], amount_steps, odds, rebate_rate, budget)
        row["enabled"] = enabled[pos]
        positions.append(row)

    recommendation_rows = [
        _rank_position_candidates(normalized, pos, amount_steps, odds, rebate_rate, budget, groups[pos])
        for pos in range(NUM_POSITIONS)
    ]
    profile_plans = _build_profile_plans(recommendation_rows, enabled, budget)
    recommendations = {
        "mode": "four_code_winbet",
        "records": len(normalized),
        "budget": _round(budget),
        "budget_mode": "auto" if budget <= 0 else "manual",
        "method": "逐球回测全部 210 组 4粒码，先生成稳健、均衡、冲回撤三套方案；准备金只作为风险参考，不再要求手动填写。",
        "positions": recommendation_rows,
        "profile_plans": profile_plans,
        "replace_count": sum(1 for row in recommendation_rows if row.get("action") == "建议替换"),
        "budget_pass_count": sum(int(row.get("budget_pass_count") or 0) for row in recommendation_rows),
    }
    summary = _build_summary(positions, len(normalized), amount_steps, budget, odds, rebate_rate)
    summary["latest_issue"] = normalized[-1].issue
    return {
        "ok": True,
        "summary": summary,
        "positions": positions,
        "recommendations": recommendations,
        "suggestions": _build_suggestions(summary, recommendations),
        "recent_records": [
            {"issue": record.issue, "numbers": list(record.numbers), "total": record.total}
            for record in reversed(normalized[-10:])
        ],
    }


def analyze_four_code_winbet_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return analyze_four_code_winbet_config(
        payload.get("config") or {},
        records=payload.get("records"),
        text=payload.get("text") or "",
        limit=payload.get("limit") or 1000,
    )

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

DIGITS = tuple(range(10))
POSITION_LABELS = ["第一球", "第二球", "第三球"]
MAIN_LABELS = {
    "dx": ("大", "小"),
    "ds": ("单", "双"),
}
MAIN_DIMENSION_LABELS = {
    "dx": "大小",
    "ds": "单双",
}
DEFAULT_MAIN_ODDS = 2.05
SPECIAL_MAIN_ODDS = 1.6
DEFAULT_NUMBER_ODDS = 9.92


@dataclass(frozen=True)
class DrawRecord:
    issue: str
    numbers: tuple[int, int, int]

    @property
    def total(self) -> int:
        return sum(self.numbers)

    @property
    def dx(self) -> str:
        return "小" if self.total <= 13 else "大"

    @property
    def ds(self) -> str:
        return "单" if self.total % 2 else "双"

    @property
    def special(self) -> bool:
        return self.total in (13, 14)


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_numbers(values: Any) -> tuple[int, int, int] | None:
    if not isinstance(values, (list, tuple)) or len(values) < 3:
        return None
    nums = []
    for item in values[:3]:
        value = _to_int(item)
        if value is None or value < 0 or value > 9:
            return None
        nums.append(value)
    return tuple(nums)  # type: ignore[return-value]


def _record_to_dict(record: DrawRecord) -> dict[str, Any]:
    return {
        "issue": record.issue,
        "numbers": list(record.numbers),
        "total": record.total,
        "dx": record.dx,
        "ds": record.ds,
        "special": record.special,
    }


def _coerce_record(item: Any) -> DrawRecord | None:
    if not isinstance(item, dict):
        return None
    issue = str(item.get("issue") or item.get("period") or item.get("期号") or "").strip()
    numbers = _valid_numbers(item.get("numbers") or item.get("draw") or item.get("开奖号码"))
    if numbers is None:
        keys = ("b1", "b2", "b3")
        if all(k in item for k in keys):
            numbers = _valid_numbers([item.get(k) for k in keys])
        else:
            keys = ("ball1", "ball2", "ball3")
            if all(k in item for k in keys):
                numbers = _valid_numbers([item.get(k) for k in keys])
    if numbers is None:
        return None
    return DrawRecord(issue=issue, numbers=numbers)


def _parse_json_text(text: str) -> list[DrawRecord]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("records") or data.get("data") or []
    if not isinstance(data, list):
        return []
    return [record for record in (_coerce_record(item) for item in data) if record]


def _parse_line(line: str) -> DrawRecord | None:
    raw = str(line or "").strip()
    if not raw:
        return None

    issue_match = re.search(r"(?:期号|期|issue|period)\D*(\d{4,})", raw, re.IGNORECASE)
    if issue_match:
        issue = issue_match.group(1)
        search_area = raw[issue_match.end():]
    else:
        long_match = re.search(r"\d{4,}", raw)
        issue = long_match.group(0) if long_match else ""
        search_area = raw[long_match.end():] if long_match else raw

    labeled = re.search(
        r"(?:开奖号码|开奖号码|开奖|三球|号码|draw|nums|numbers)\D*([0-9])\D+([0-9])\D+([0-9])",
        search_area,
        re.IGNORECASE,
    )
    if labeled:
        numbers = tuple(int(v) for v in labeled.groups())
        return DrawRecord(issue=issue, numbers=numbers)  # type: ignore[arg-type]

    tokens = list(re.finditer(r"\d+", search_area))
    nums = []
    for token in tokens:
        value = _to_int(token.group(0))
        if value is not None and 0 <= value <= 9:
            nums.append(value)
        if len(nums) >= 3:
            break
    if len(nums) < 3 and not issue:
        all_tokens = [_to_int(m.group(0)) for m in re.finditer(r"\d+", raw)]
        nums = [v for v in all_tokens if v is not None and 0 <= v <= 9][:3]
    if len(nums) < 3:
        return None
    return DrawRecord(issue=issue, numbers=tuple(nums[:3]))  # type: ignore[arg-type]


def parse_draw_text(text: str) -> list[dict[str, Any]]:
    records = _parse_json_text(text or "")
    if not records:
        records = [record for record in (_parse_line(line) for line in str(text or "").splitlines()) if record]
    return [_record_to_dict(record) for record in _dedupe_and_sort(records)]


def normalize_draw_records(records: Any = None, text: str = "") -> list[DrawRecord]:
    out: list[DrawRecord] = []
    if isinstance(records, list):
        out.extend(record for record in (_coerce_record(item) for item in records) if record)
    if text:
        parsed = _parse_json_text(text)
        if not parsed:
            parsed = [record for record in (_parse_line(line) for line in str(text).splitlines()) if record]
        out.extend(parsed)
    return _dedupe_and_sort(out)


def _dedupe_and_sort(records: list[DrawRecord]) -> list[DrawRecord]:
    indexed: list[tuple[int, DrawRecord]] = list(enumerate(records))
    by_issue: dict[str, tuple[int, DrawRecord]] = {}
    no_issue: list[tuple[int, DrawRecord]] = []
    for index, record in indexed:
        if record.issue:
            by_issue[record.issue] = (index, record)
        else:
            no_issue.append((index, record))
    deduped = list(by_issue.values()) + no_issue

    def key(item: tuple[int, DrawRecord]):
        index, record = item
        if record.issue.isdigit():
            return (0, int(record.issue), index)
        return (1, index, record.issue)

    return [record for _, record in sorted(deduped, key=key)]


def _rate(count: int, total: int) -> float:
    return _round(count / total, 4) if total else 0.0


def _value_summary(values: list[int], domain=()) -> list[dict[str, Any]]:
    counter = Counter(values)
    domain = tuple(domain) if domain else tuple(sorted(counter))
    total = len(values)
    return [
        {"value": value, "count": counter.get(value, 0), "rate": _rate(counter.get(value, 0), total)}
        for value in domain
    ]


def _position_omissions(records: list[DrawRecord], position: int) -> dict[int, int]:
    result: dict[int, int] = {}
    for digit in DIGITS:
        miss = 0
        for record in reversed(records):
            if record.numbers[position] == digit:
                break
            miss += 1
        result[digit] = miss
    return result


def _label_omission(records: list[DrawRecord], kind: str, label: str) -> int:
    miss = 0
    for record in reversed(records):
        if getattr(record, kind) == label:
            break
        miss += 1
    return miss


def _current_streak(records: list[DrawRecord], kind: str) -> dict[str, Any]:
    if not records:
        return {"label": "", "count": 0}
    label = getattr(records[-1], kind)
    count = 0
    for record in reversed(records):
        if getattr(record, kind) != label:
            break
        count += 1
    return {"label": label, "count": count}


def _transition_rate(records: list[DrawRecord], kind: str, previous_label: str, candidate: str) -> tuple[float, int]:
    total = 0
    hit = 0
    for prev, curr in zip(records, records[1:]):
        if getattr(prev, kind) == previous_label:
            total += 1
            if getattr(curr, kind) == candidate:
                hit += 1
    if total < 5:
        return 0.5, total
    return hit / total, total


def _digit_transition_rate(records: list[DrawRecord], position: int, previous_digit: int, candidate: int) -> tuple[float, int]:
    total = 0
    hit = 0
    for prev, curr in zip(records, records[1:]):
        if prev.numbers[position] == previous_digit:
            total += 1
            if curr.numbers[position] == candidate:
                hit += 1
    if total < 5:
        return 0.1, total
    return hit / total, total


def _recommend_main_raw(history: list[DrawRecord], kind: str, lookback: int) -> dict[str, Any]:
    labels = MAIN_LABELS[kind]
    window = history[-lookback:] if lookback > 0 else history[:]
    recent = history[-min(30, len(history)):] if history else []
    latest_label = getattr(history[-1], kind) if history else ""
    streak = _current_streak(history, kind)
    scored = []
    for label in labels:
        recent_count = sum(1 for record in recent if getattr(record, kind) == label)
        recent_rate = recent_count / len(recent) if recent else 0.5
        window_count = sum(1 for record in window if getattr(record, kind) == label)
        window_rate = window_count / len(window) if window else 0.5
        omission = _label_omission(window, kind, label)
        transition, transition_total = _transition_rate(window, kind, latest_label, label) if latest_label else (0.5, 0)
        streak_adjust = 0.0
        if streak["count"] >= 3 and label != streak["label"]:
            streak_adjust += min(10.0, streak["count"] * 2.0)
        if streak["count"] >= 4 and label == streak["label"]:
            streak_adjust -= min(8.0, streak["count"] * 1.4)
        score = ((recent_rate - 0.5) * 72) + ((window_rate - 0.5) * 35) + ((transition - 0.5) * 55) + min(omission, 10) * 1.6 + streak_adjust
        scored.append({
            "label": label,
            "score": _round(score, 2),
            "recent_rate": _round(recent_rate),
            "window_rate": _round(window_rate),
            "omission": omission,
            "transition_rate": _round(transition),
            "transition_samples": transition_total,
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    second = scored[1]
    gap = best["score"] - second["score"]
    reasons = [
        f"近{len(recent)}期{best['label']}占{best['recent_rate'] * 100:.1f}%",
        f"近{len(window)}期{best['label']}占{best['window_rate'] * 100:.1f}%",
        f"当前遗漏{best['omission']}期",
    ]
    if latest_label and best["transition_samples"] >= 5:
        reasons.append(f"上期{latest_label}后转{best['label']}为{best['transition_rate'] * 100:.1f}%")
    if streak["label"]:
        reasons.append(f"当前{streak['label']}连开{streak['count']}期")
    return {
        "kind": kind,
        "name": MAIN_DIMENSION_LABELS[kind],
        "target": best["label"],
        "score": best["score"],
        "gap": _round(gap, 2),
        "candidates": scored,
        "reasons": reasons,
    }


def _recommend_numbers_raw(history: list[DrawRecord], position: int, lookback: int, group_size: int) -> dict[str, Any]:
    group_size = min(9, max(1, int(group_size or 5)))
    window = history[-lookback:] if lookback > 0 else history[:]
    recent = history[-min(40, len(history)):] if history else []
    omissions = _position_omissions(window, position)
    latest_digit = history[-1].numbers[position] if history else 0
    scored = []
    for digit in DIGITS:
        recent_rate = sum(1 for record in recent if record.numbers[position] == digit) / len(recent) if recent else 0.1
        window_rate = sum(1 for record in window if record.numbers[position] == digit) / len(window) if window else 0.1
        transition, transition_samples = _digit_transition_rate(window, position, latest_digit, digit)
        score = ((recent_rate - 0.1) * 110) + ((window_rate - 0.1) * 60) + ((transition - 0.1) * 70) + min(omissions[digit], 18) * 0.65
        scored.append({
            "digit": digit,
            "score": _round(score, 2),
            "recent_rate": _round(recent_rate),
            "window_rate": _round(window_rate),
            "omission": omissions[digit],
            "transition_rate": _round(transition),
            "transition_samples": transition_samples,
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    group = sorted(item["digit"] for item in scored[:group_size])
    avg_top = sum(item["score"] for item in scored[:group_size]) / group_size
    avg_all = sum(item["score"] for item in scored) / len(scored)
    coverage = sum(1 for record in recent if record.numbers[position] in group) / len(recent) if recent else 0.0
    return {
        "position": position + 1,
        "name": POSITION_LABELS[position],
        "group": group,
        "score": _round(avg_top, 2),
        "strength": _round(avg_top - avg_all, 2),
        "coverage": _round(coverage),
        "candidates": scored,
        "reasons": [
            f"近{len(recent)}期候选组覆盖{coverage * 100:.1f}%",
            f"最大遗漏{max(omissions[d] for d in group)}期",
            f"上期本球开{latest_digit}",
        ],
    }


def _settle_main_bet(record: DrawRecord, kind: str, target: str, main_odds: float = DEFAULT_MAIN_ODDS) -> float:
    if getattr(record, kind) != target:
        return -1.0
    odds = SPECIAL_MAIN_ODDS if record.special else float(main_odds or DEFAULT_MAIN_ODDS)
    return _round(odds - 1.0, 4)


def _backtest_main(records: list[DrawRecord], kind: str, lookback: int, min_gap: float, main_odds: float) -> dict[str, Any]:
    if len(records) < 6:
        return _empty_backtest()
    min_history = min(max(5, lookback // 4), len(records) - 1)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    bets = wins = skips = 0
    miss_streak = max_miss_streak = 0
    stake = 0.0
    for index in range(min_history, len(records)):
        history = records[max(0, index - lookback):index]
        rec = _recommend_main_raw(history, kind, lookback)
        if rec["gap"] < min_gap:
            skips += 1
            continue
        bets += 1
        stake += 1.0
        profit = _settle_main_bet(records[index], kind, rec["target"], main_odds)
        if profit > 0:
            wins += 1
            miss_streak = 0
        else:
            miss_streak += 1
            max_miss_streak = max(max_miss_streak, miss_streak)
        equity += profit
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return _format_backtest(bets, wins, skips, stake, equity, max_drawdown, max_miss_streak)


def _settle_number_group(record: DrawRecord, position: int, group: list[int], number_odds: float = DEFAULT_NUMBER_ODDS) -> float:
    stake = len(group)
    if record.numbers[position] in set(group):
        return float(number_odds or DEFAULT_NUMBER_ODDS) - stake
    return -float(stake)


def _backtest_numbers(records: list[DrawRecord], position: int, lookback: int, group_size: int, min_strength: float, number_odds: float) -> dict[str, Any]:
    if len(records) < 6:
        return _empty_backtest()
    min_history = min(max(5, lookback // 4), len(records) - 1)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    bets = wins = skips = 0
    miss_streak = max_miss_streak = 0
    stake = 0.0
    for index in range(min_history, len(records)):
        history = records[max(0, index - lookback):index]
        rec = _recommend_numbers_raw(history, position, lookback, group_size)
        if rec["strength"] < min_strength:
            skips += 1
            continue
        bets += 1
        stake += len(rec["group"])
        profit = _settle_number_group(records[index], position, rec["group"], number_odds)
        if profit > 0:
            wins += 1
            miss_streak = 0
        else:
            miss_streak += 1
            max_miss_streak = max(max_miss_streak, miss_streak)
        equity += profit
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return _format_backtest(bets, wins, skips, stake, equity, max_drawdown, max_miss_streak)


def _empty_backtest() -> dict[str, Any]:
    return {
        "bets": 0,
        "wins": 0,
        "skips": 0,
        "hit_rate": 0.0,
        "profit_units": 0.0,
        "stake_units": 0.0,
        "roi": 0.0,
        "max_drawdown_units": 0.0,
        "max_miss_streak": 0,
    }


def _format_backtest(bets: int, wins: int, skips: int, stake: float, profit: float, max_drawdown: float, max_miss_streak: int) -> dict[str, Any]:
    return {
        "bets": bets,
        "wins": wins,
        "skips": skips,
        "hit_rate": _round(wins / bets if bets else 0.0),
        "profit_units": _round(profit, 2),
        "stake_units": _round(stake, 2),
        "roi": _round(profit / stake if stake else 0.0),
        "max_drawdown_units": _round(max_drawdown, 2),
        "max_miss_streak": max_miss_streak,
    }


def _confidence(score_gap: float, backtest: dict[str, Any]) -> str:
    if backtest.get("bets", 0) < 8 or backtest.get("profit_units", 0) <= 0:
        return "低"
    if score_gap >= 22 and backtest.get("roi", 0) >= 0.04:
        return "高"
    if score_gap >= 10 and backtest.get("roi", 0) >= 0.01:
        return "中"
    return "低"


def _main_stats(records: list[DrawRecord]) -> dict[str, Any]:
    totals = len(records)
    dx_counts = Counter(record.dx for record in records)
    ds_counts = Counter(record.ds for record in records)
    return {
        "dx": [{"label": label, "count": dx_counts.get(label, 0), "rate": _rate(dx_counts.get(label, 0), totals)} for label in MAIN_LABELS["dx"]],
        "ds": [{"label": label, "count": ds_counts.get(label, 0), "rate": _rate(ds_counts.get(label, 0), totals)} for label in MAIN_LABELS["ds"]],
        "special": {"count": sum(1 for record in records if record.special), "rate": _rate(sum(1 for record in records if record.special), totals)},
        "streaks": {"dx": _current_streak(records, "dx"), "ds": _current_streak(records, "ds")},
    }


def _position_stats(records: list[DrawRecord]) -> list[dict[str, Any]]:
    result = []
    for position, label in enumerate(POSITION_LABELS):
        values = [record.numbers[position] for record in records]
        summary = _value_summary(values, DIGITS)
        ranked_hot = sorted(summary, key=lambda item: (item["count"], item["value"]), reverse=True)[:3]
        ranked_cold = sorted(summary, key=lambda item: (item["count"], -item["value"]))[:3]
        omissions = _position_omissions(records, position)
        result.append({
            "position": position + 1,
            "name": label,
            "values": summary,
            "hot": ranked_hot,
            "cold": ranked_cold,
            "omissions": [{"digit": digit, "miss": omissions[digit]} for digit in DIGITS],
        })
    return result


def analyze_draws(records: Any = None, text: str = "", lookback: int = 80, backtest_window: int = 300, group_size: int = 5, main_odds: float = DEFAULT_MAIN_ODDS, number_odds: float = DEFAULT_NUMBER_ODDS) -> dict[str, Any]:
    normalized = normalize_draw_records(records, text)
    lookback = min(500, max(10, int(lookback or 80)))
    backtest_window = min(2000, max(20, int(backtest_window or 300)))
    group_size = min(9, max(1, int(group_size or 5)))
    if len(normalized) < 8:
        return {"ok": False, "message": "至少需要 8 条有效开奖记录", "records": [_record_to_dict(record) for record in normalized]}

    window = normalized[-lookback:]
    backtest_records = normalized[-backtest_window:]
    main_recommendations = []
    for kind in ("dx", "ds"):
        raw = _recommend_main_raw(window, kind, lookback)
        bt = _backtest_main(backtest_records, kind, lookback, min_gap=8.0, main_odds=main_odds)
        confidence = _confidence(raw["gap"], bt)
        action = raw["target"] if raw["gap"] >= 8.0 and bt["profit_units"] > 0 and bt["bets"] >= 8 else "跳过"
        main_recommendations.append({**raw, "action": action, "confidence": confidence, "backtest": bt})

    number_recommendations = []
    for position in range(3):
        raw = _recommend_numbers_raw(window, position, lookback, group_size)
        bt = _backtest_numbers(backtest_records, position, lookback, group_size, min_strength=1.0, number_odds=number_odds)
        confidence = _confidence(raw["strength"], bt)
        action = "投注候选组" if raw["strength"] >= 1.0 and bt["profit_units"] > 0 and bt["bets"] >= 8 else "跳过"
        number_recommendations.append({**raw, "action": action, "confidence": confidence, "backtest": bt})

    latest = normalized[-1]
    return {
        "ok": True,
        "summary": {
            "records": len(normalized),
            "used_window": len(window),
            "backtest_window": len(backtest_records),
            "lookback": lookback,
            "group_size": group_size,
            "latest": _record_to_dict(latest),
        },
        "main_trend": main_recommendations,
        "number_sets": number_recommendations,
        "stats": {
            "main": _main_stats(window),
            "positions": _position_stats(window),
            "sums": _value_summary([record.total for record in window], range(28)),
        },
        "records": [_record_to_dict(record) for record in reversed(normalized[-30:])],
    }


def analyze_draw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return analyze_draws(
        records=payload.get("records"),
        text=payload.get("text") or "",
        lookback=payload.get("lookback") or 80,
        backtest_window=payload.get("backtest_window") or payload.get("backtestWindow") or 300,
        group_size=payload.get("group_size") or payload.get("groupSize") or 5,
        main_odds=float(payload.get("main_odds") or DEFAULT_MAIN_ODDS),
        number_odds=float(payload.get("number_odds") or DEFAULT_NUMBER_ODDS),
    )


def sample_draw_records(limit: int = 160) -> list[dict[str, Any]]:
    limit = min(1000, max(20, int(limit or 160)))
    seed = 24681357
    records = []
    base_issue = 3500000
    for index in range(limit):
        nums = []
        for position in range(3):
            seed = (seed * 1103515245 + 12345 + position * 97 + index * 13) & 0x7FFFFFFF
            value = (seed >> 8) % 10
            if index % 17 in (3, 4, 5) and position == 0:
                value = (value + 6) % 10
            if index % 23 in (7, 8, 9, 10) and position == 1:
                value = (value + 3) % 10
            nums.append(value)
        records.append(_record_to_dict(DrawRecord(str(base_issue + index), tuple(nums))))  # type: ignore[arg-type]
    return records


def sample_draw_text(limit: int = 160) -> str:
    lines = []
    for record in sample_draw_records(limit):
        a, b, c = record["numbers"]
        lines.append(f"{record['issue']} {a} {b} {c}")
    return "\n".join(lines)
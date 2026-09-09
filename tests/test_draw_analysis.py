import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.draw_analysis_svc import (  # noqa: E402
    DrawRecord,
    _settle_main_bet,
    analyze_draws,
    normalize_draw_records,
    parse_draw_text,
    sample_draw_records,
)
from services.draw_capture_svc import parse_browser_rows  # noqa: E402
from core import db as core_db  # noqa: E402


def test_parse_draw_text_accepts_common_formats_and_dedupes():
    text = """
3476729 4 4 5
期号=3476730 开奖=[4,5,5]
issue 3476731 draw 1 2 3
3476731 9 9 9
"""
    records = parse_draw_text(text)
    assert len(records) == 3
    assert records[0]["issue"] == "3476729"
    assert records[0]["numbers"] == [4, 4, 5]
    assert records[0]["total"] == 13
    assert records[0]["dx"] == "小"
    assert records[0]["ds"] == "单"
    assert records[1]["numbers"] == [4, 5, 5]
    assert records[2]["numbers"] == [9, 9, 9]


def test_normalize_draw_records_sorts_numeric_issues():
    records = normalize_draw_records([
        {"issue": "1003", "numbers": [3, 3, 3]},
        {"issue": "1001", "numbers": [1, 1, 1]},
        {"issue": "1002", "draw": [2, 2, 2]},
    ])
    assert [record.issue for record in records] == ["1001", "1002", "1003"]


def test_main_trend_special_odds_are_used_for_analysis_settlement():
    r13 = DrawRecord("1", (4, 4, 5))
    r14 = DrawRecord("2", (4, 5, 5))
    normal = DrawRecord("3", (7, 7, 7))

    assert _settle_main_bet(r13, "dx", "小", 2.05) == 0.6
    assert _settle_main_bet(r13, "ds", "单", 2.05) == 0.6
    assert _settle_main_bet(r14, "dx", "大", 2.05) == 0.6
    assert _settle_main_bet(r14, "ds", "双", 2.05) == 0.6
    assert round(_settle_main_bet(normal, "dx", "大", 2.05), 2) == 1.05
    assert _settle_main_bet(normal, "dx", "小", 2.05) == -1.0


def test_analyze_draws_returns_recommendations_and_backtests():
    sample = sample_draw_records(160)
    result = analyze_draws(sample, lookback=40, backtest_window=120, group_size=5)

    assert result["ok"] is True
    assert result["summary"]["records"] == 160
    assert result["summary"]["used_window"] == 40
    assert len(result["main_trend"]) == 2
    assert len(result["number_sets"]) == 3
    assert len(result["records"]) == 30

    for item in result["main_trend"]:
        assert item["action"] in {"大", "小", "单", "双", "跳过"}
        assert item["confidence"] in {"低", "中", "高"}
        assert "backtest" in item
        assert "profit_units" in item["backtest"]

    for item in result["number_sets"]:
        assert len(item["group"]) == 5
        assert item["action"] in {"投注候选组", "跳过"}
        assert item["confidence"] in {"低", "中", "高"}
        assert "backtest" in item

def test_parse_browser_rows_extracts_real_result_table_shape():
    rows = [
        {"issue": "3479869", "draw_time": "09-09 16:33", "numbers": [0, "1", 0]},
        {"issue": "3479870", "draw_time": "09-09 16:36", "numbers": [4, 4, 5]},
        {"issue": "bad", "draw_time": "", "numbers": [1, 2]},
        {"issue": "3479871", "draw_time": "09-09 16:39", "numbers": [4, 5, 5]},
    ]

    records = parse_browser_rows(rows, limit=10)

    assert [r["issue"] for r in records] == ["3479869", "3479870", "3479871"]
    assert records[0]["numbers"] == [0, 1, 0]
    assert records[0]["total"] == 1
    assert records[0]["dx"] == "小"
    assert records[0]["ds"] == "单"
    assert records[1]["total"] == 13
    assert records[1]["special"] is True
    assert records[2]["total"] == 14
    assert records[2]["dx"] == "大"
    assert records[2]["ds"] == "双"


def test_draw_record_db_persists_and_overwrites(tmp_path):
    old_path = core_db.DB_PATH
    core_db.DB_PATH = str(tmp_path / "draws.db")
    try:
        core_db.init_db()
        saved = core_db.add_draw_records([
            {"issue": "1001", "draw_time": "09-09 10:00", "numbers": [1, 2, 3]},
            {"issue": "1002", "draw_time": "09-09 10:03", "numbers": [4, 4, 5]},
        ], source="test")
        assert saved == 2

        saved = core_db.add_draw_records([
            {"issue": "1001", "draw_time": "09-09 10:01", "numbers": [9, 0, 0]},
        ], source="browser")
        assert saved == 1

        records = core_db.get_draw_records(10)
        assert [r["issue"] for r in records] == ["1002", "1001"]
        updated = next(r for r in records if r["issue"] == "1001")
        assert updated["numbers"] == [9, 0, 0]
        assert updated["total"] == 9
        assert updated["dx"] == "小"
        assert updated["ds"] == "单"
        assert updated["source"] == "browser"

        core_db.clear_draw_records()
        assert core_db.get_draw_records(10) == []
    finally:
        core_db.DB_PATH = old_path

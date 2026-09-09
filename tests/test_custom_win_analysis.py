import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from core import db as core_db  # noqa: E402
from main import app  # noqa: E402
from services.custom_win_analysis_svc import analyze_custom_winbet_config  # noqa: E402
from services.draw_analysis_svc import sample_draw_records  # noqa: E402


CONFIG = {
    "amount_steps": [10, 20, 30],
    "base_bet_amount": 10,
    "entry_miss_trigger": 0,
    "enabled_positions": [True, True, True],
    "number_sets": [
        {"set_a": [0, 1, 3, 5, 8], "set_b": [2, 4, 6, 7, 9]},
        {"set_a": [0, 1, 3, 5, 8], "set_b": [2, 4, 6, 7, 9]},
        {"set_a": [0, 1, 3, 5, 8], "set_b": [2, 4, 6, 7, 9]},
    ],
    "odds": 10,
    "rebate_rate": 0,
    "daily_stop_loss": 999999,
}


def test_custom_win_analysis_replays_win_rush_state_machine():
    records = [
        {"issue": "1000", "numbers": [0, 0, 0]},
        {"issue": "1001", "numbers": [1, 1, 9]},
        {"issue": "1002", "numbers": [2, 2, 2]},
        {"issue": "1003", "numbers": [1, 1, 1]},
        {"issue": "1004", "numbers": [4, 4, 9]},
        {"issue": "1005", "numbers": [1, 2, 3]},
        {"issue": "1006", "numbers": [9, 1, 2]},
        {"issue": "1007", "numbers": [1, 9, 1]},
        {"issue": "1008", "numbers": [2, 2, 2]},
        {"issue": "1009", "numbers": [1, 1, 1]},
        {"issue": "1010", "numbers": [9, 9, 9]},
        {"issue": "1011", "numbers": [1, 1, 1]},
    ]

    result = analyze_custom_winbet_config(CONFIG, records=records)

    assert result["ok"] is True
    assert result["summary"]["records"] == 12
    assert result["summary"]["bets"] > 0
    assert result["summary"]["wins"] > 0
    assert len(result["positions"]) == 3
    assert result["positions"][0]["max_tier_reached"] >= 2
    assert result["positions"][0]["sets"]["A"]["bets"] > 0
    assert result["positions"][0]["sets"]["B"]["bets"] > 0
    assert result["positions"][2]["max_miss_streak"] >= 1


def test_custom_win_analysis_respects_disabled_positions():
    cfg = {**CONFIG, "enabled_positions": [True, False, False]}
    result = analyze_custom_winbet_config(cfg, records=sample_draw_records(80))

    assert result["ok"] is True
    assert result["positions"][0]["enabled"] is True
    assert result["positions"][0]["bets"] > 0
    assert result["positions"][1]["enabled"] is False
    assert result["positions"][1]["bets"] == 0
    assert result["positions"][2]["enabled"] is False
    assert result["positions"][2]["bets"] == 0


def test_custom_win_analysis_requires_enough_draw_records():
    records = [{"issue": str(2000 + i), "numbers": [i % 10, (i + 1) % 10, (i + 2) % 10]} for i in range(8)]
    result = analyze_custom_winbet_config(CONFIG, records=records)

    assert result["ok"] is False
    assert "开奖记录" in result["message"]


def test_custom_win_analysis_api_uses_saved_draw_records(tmp_path):
    old_path = core_db.DB_PATH
    core_db.DB_PATH = str(tmp_path / "draws.db")
    try:
        core_db.init_db()
        core_db.add_draw_records(sample_draw_records(80), "test")
        with TestClient(app) as client:
            res = client.post("/api/custom-winbet/analyze", json={"config": CONFIG, "limit": 80})
        data = res.json()

        assert res.status_code == 200
        assert data["ok"] is True
        assert data["summary"]["records"] == 80
        assert len(data["positions"]) == 3
        assert len(data["recommendations"]["same5_positions"]) == 3
    finally:
        core_db.DB_PATH = old_path


def test_custom_win_analysis_returns_number_recommendations():
    result = analyze_custom_winbet_config(CONFIG, records=sample_draw_records(120))
    recs = result["recommendations"]

    assert result["ok"] is True
    assert recs["mode"] == "custom_winbet"
    assert recs["records"] == 120
    assert recs["replace_count"] >= 0
    assert recs["open_count"] >= 0
    assert recs["same5_replace_count"] >= 0
    assert len(recs["positions"]) == 3
    assert len(recs["same5_positions"]) == 3

    for row in recs["same5_positions"]:
        assert row["position"] in (1, 2, 3)
        assert row["action"] in {"保持当前", "建议替换", "差距不大", "不建议替换", "暂无推荐"}
        assert row["enabled_advice"] in {"建议开启/保留", "谨慎开启", "建议关闭"}
        assert row["recommended"] is not None
        assert row["recommended"]["same_numbers"] is True
        assert len(row["recommended"]["set_a"]) == 5
        assert row["recommended"]["set_a"] == row["recommended"]["set_b"]
        assert row["recommended"]["reasons"]
        assert len(row["candidates"]) >= 1

    for row in recs["positions"]:
        assert row["position"] in (1, 2, 3)
        assert row["action"] in {"保持当前", "建议替换", "差距不大", "不建议替换", "暂无推荐"}
        assert row["enabled_advice"] in {"建议开启/保留", "谨慎开启", "建议关闭"}
        assert row["recommended"] is not None
        assert len(row["recommended"]["set_a"]) in (4, 5)
        assert len(row["recommended"]["set_b"]) in (4, 5)
        assert row["recommended"]["reasons"]
        assert len(row["candidates"]) >= 1
        scores = [item["score"] for item in row["candidates"]]
        assert scores == sorted(scores, reverse=True)

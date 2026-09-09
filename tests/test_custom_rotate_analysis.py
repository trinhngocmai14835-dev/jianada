import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from core import db as core_db  # noqa: E402
from main import app  # noqa: E402
from services.custom_rotate_analysis_svc import analyze_custom_rotatebet_config  # noqa: E402
from services.draw_analysis_svc import sample_draw_records  # noqa: E402


CONFIG = {
    "amount_steps": [10, 20, 30],
    "base_bet_amount": 10,
    "entry_miss_trigger": 1,
    "enabled_positions": [True, True, True],
    "number_sets": [
        {"set_a": [1, 3, 5, 7], "set_b": [0, 2, 4, 6]},
        {"set_a": [1, 3, 5, 7], "set_b": [0, 2, 4, 6]},
        {"set_a": [1, 3, 5, 7], "set_b": [0, 2, 4, 6]},
    ],
    "odds": 10,
    "rebate_rate": 0,
    "daily_stop_loss": 999999,
}


def test_custom_rotate_analysis_replays_loss_chase_state_machine():
    records = [
        {"issue": "1000", "numbers": [1, 1, 1]},
        {"issue": "1001", "numbers": [9, 9, 9]},
        {"issue": "1002", "numbers": [9, 9, 9]},
        {"issue": "1003", "numbers": [1, 1, 1]},
        {"issue": "1004", "numbers": [9, 9, 9]},
        {"issue": "1005", "numbers": [2, 2, 2]},
        {"issue": "1006", "numbers": [9, 9, 9]},
        {"issue": "1007", "numbers": [5, 5, 5]},
        {"issue": "1008", "numbers": [9, 9, 9]},
        {"issue": "1009", "numbers": [4, 4, 4]},
        {"issue": "1010", "numbers": [9, 9, 9]},
        {"issue": "1011", "numbers": [7, 7, 7]},
    ]

    result = analyze_custom_rotatebet_config(CONFIG, records=records)

    assert result["ok"] is True
    assert result["summary"]["records"] == 12
    assert result["summary"]["bets"] > 0
    assert result["summary"]["wins"] > 0
    assert len(result["positions"]) == 3
    assert result["positions"][0]["max_tier_reached"] >= 2
    assert result["positions"][0]["sets"]["A"]["bets"] > 0
    assert result["positions"][0]["sets"]["B"]["bets"] > 0


def test_custom_rotate_analysis_returns_chase_recommendations():
    result = analyze_custom_rotatebet_config(CONFIG, records=sample_draw_records(140))
    recs = result["recommendations"]

    assert result["ok"] is True
    assert recs["mode"] == "custom_rotatebet"
    assert recs["records"] == 140
    assert recs["recent_records"] == 80
    assert recs["replace_count"] >= 0
    assert len(recs["positions"]) == 3

    for row in recs["positions"]:
        assert row["position"] in (1, 2, 3)
        assert row["action"] in {"保持当前", "建议替换", "差距不大", "暂不推荐", "暂无推荐"}
        assert row["enabled_advice"] in {"建议开启/保留", "谨慎开启", "建议关闭"}
        assert row["recommended"] is not None
        assert len(row["recommended"]["set_a"]) in (4, 5)
        assert len(row["recommended"]["set_b"]) in (4, 5)
        assert row["recommended"]["recent"]["records"] == 80
        assert row["recommended"]["reasons"]
        scores = [item["score"] for item in row["candidates"]]
        assert scores == sorted(scores, reverse=True)


def test_custom_rotate_analysis_api_uses_saved_draw_records(tmp_path):
    old_path = core_db.DB_PATH
    core_db.DB_PATH = str(tmp_path / "draws.db")
    try:
        core_db.init_db()
        core_db.add_draw_records(sample_draw_records(90), "test")
        with TestClient(app) as client:
            res = client.post("/api/custom-rotatebet/analyze", json={"config": CONFIG, "limit": 90})
        data = res.json()

        assert res.status_code == 200
        assert data["ok"] is True
        assert data["summary"]["records"] == 90
        assert len(data["recommendations"]["positions"]) == 3
    finally:
        core_db.DB_PATH = old_path
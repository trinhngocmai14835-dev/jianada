import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from core import db as core_db  # noqa: E402
from main import app  # noqa: E402
from services.draw_analysis_svc import normalize_draw_records, sample_draw_records  # noqa: E402
from services.four_code_win_analysis_svc import (  # noqa: E402
    analyze_four_code_winbet_config,
    simulate_four_code_group,
)

CONFIG = {
    "amount_steps": [10, 20, 30],
    "base_bet_amount": 10,
    "enabled_positions": [True, True, True],
    "number_groups": [
        {"numbers": [0, 1, 2, 3]},
        {"numbers": [0, 1, 2, 3]},
        {"numbers": [0, 1, 2, 3]},
    ],
    "odds": 10,
    "rebate_rate": 0,
    "daily_stop_loss": 999999,
}


def test_four_code_win_state_machine_replays_hits_and_reset():
    records = [
        {"issue": "1000", "numbers": [0, 0, 0]},
        {"issue": "1001", "numbers": [1, 1, 1]},
        {"issue": "1002", "numbers": [2, 2, 2]},
        {"issue": "1003", "numbers": [9, 9, 9]},
    ]
    first = simulate_four_code_group(
        normalize_draw_records(records),
        0,
        [0, 1, 2, 3],
        [10, 20, 30],
        10,
        0,
        500,
    )

    assert first["wins"] == 3
    assert first["max_tier_reached"] == 3
    assert first["next_state"]["tier"] == 1
    assert first["current_miss_streak"] == 1
    assert first["profit"] == 320


def test_four_code_analysis_returns_budget_recommendations():
    result = analyze_four_code_winbet_config(CONFIG, records=sample_draw_records(120), limit=120)
    recs = result["recommendations"]

    assert result["ok"] is True
    assert result["summary"]["records"] == 120
    assert recs["mode"] == "four_code_winbet"
    assert recs["budget"] == 0
    assert recs["budget_mode"] == "auto"
    assert result["summary"]["budget"] == 0
    assert len(recs["positions"]) == 3
    assert recs["budget_pass_count"] >= 0
    assert [plan["key"] for plan in recs["profile_plans"]] == ["stable", "balanced", "drawdown"]
    for plan in recs["profile_plans"]:
        summary = plan["summary"]
        assert len(plan["positions"]) == 3
        assert summary["suggested_budget"] >= summary["budget_needed"]
        assert summary["safe_budget"] >= summary["suggested_budget"]
        assert len(plan["recommended_config"]["number_groups"]) == 3
        for group in plan["recommended_config"]["number_groups"]:
            assert len(group["numbers"]) == 4
    for row in recs["positions"]:
        assert row["evaluated"] == 210
        assert row["recommended"] is not None
        assert set(row["profiles"]) == {"stable", "balanced", "drawdown"}
        assert len(row["recommended"]["numbers"]) == 4
        assert isinstance(row["recommended"]["budget_pass"], bool)
        assert row["recommended"]["budget_needed"] >= row["recommended"]["next_state"]["stake"]
        ranking = [(item["budget_pass"], item["score"]) for item in row["candidates"]]
        assert ranking == sorted(ranking, reverse=True)


def test_four_code_analysis_rejects_invalid_group_size():
    bad = {**CONFIG, "number_groups": [{"numbers": [1, 2, 3, 4, 5]}] * 3}
    result = analyze_four_code_winbet_config(bad, records=sample_draw_records(80), limit=80)

    assert result["ok"] is False
    assert result["records"] == 80


def test_four_code_analysis_api_uses_saved_draw_snapshot(tmp_path):
    old_path = core_db.DB_PATH
    core_db.DB_PATH = str(tmp_path / "draws.db")
    try:
        core_db.init_db()
        snapshot = core_db.save_draw_record_snapshot(sample_draw_records(40), "browser")
        core_db.add_draw_records(sample_draw_records(90), "browser")
        with TestClient(app) as client:
            res = client.post("/api/four-code-winbet/analyze", json={"config": CONFIG, "limit": 90})
        data = res.json()

        assert res.status_code == 200
        assert data["ok"] is True
        assert snapshot["record_count"] == 40
        assert data["summary"]["records"] == 40
        assert data["record_source"]["type"] == "draw_snapshot"
        assert data["record_source"]["snapshot_id"] == snapshot["id"]
    finally:
        core_db.DB_PATH = old_path

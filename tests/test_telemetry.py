import json

from state import STORE
from telemetry import record_event, aggregate, debug_log_path
from config import reload_config


def test_telemetry_aggregate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    s = STORE.get("s1")
    s.main_model_calls_avoided = 2
    s.finishes = 2
    record_event(s, "round_finish", action="finish", reason="jev_judgment", outcome="success")
    agg = aggregate("s1")
    assert agg["finishes"] == 2
    assert agg["main_model_calls_avoided"] == 2
    log = debug_log_path()
    assert log.exists()
    last = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "round_finish"
    assert last["reason"] == "jev_judgment"


def test_record_event_auto_fields_and_human_summary(tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    s = STORE.get("s2")
    s.user_goal = "Short goal only"
    s.mutation_epoch = 7
    s.main_model_calls_avoided = 1
    with caplog.at_level(logging.INFO, logger="hermes.plugins.jev_router.telemetry"):
        record_event(
            s, "round_continue",
            action="continue", reason="thresholds_not_met",
            turn_id="tid-9", jev_ms=12.5,
        )
    last = json.loads(debug_log_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["seq"] == 1
    assert last["turn_id"] == "tid-9"
    assert last["goal_preview"] == "Short goal only"
    assert last["mutation_epoch"] == 7
    assert last["main_model_calls_avoided"] == 1
    assert last["jev_ms"] == 12.5
    joined = " ".join(r.message for r in caplog.records)
    assert "turn_id=tid-9" in joined
    assert "seq=1" in joined
    assert "jev_ms=12.5" in joined


def test_goal_preview_truncates():
    from telemetry import goal_preview
    long = "x" * 200
    assert len(goal_preview(long)) == 80
    assert goal_preview("a\nb\rc") == "a b c"

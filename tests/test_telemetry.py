from state import STORE
from telemetry import record_event, aggregate
from config import reload_config


def test_telemetry_aggregate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    s = STORE.get("s1")
    s.main_model_calls_avoided = 2
    s.finishes = 2
    record_event(s, "finish_jev", outcome="success")
    agg = aggregate("s1")
    assert agg["finishes"] == 2
    assert agg["main_model_calls_avoided"] == 2
    log = tmp_path / "plugins" / "jev-router" / "telemetry.jsonl"
    assert log.exists()

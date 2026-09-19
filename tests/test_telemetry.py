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

from config import reload_config
from plan import register_plan, gate_next_step, handle_jev_execution_plan, clear_plan
from jev import set_judge_override
from schemas import PlanGateJudgment
import json


def test_plan_disabled_by_default(monkeypatch):
    monkeypatch.setenv("JEV_AUTONOMOUS_PLAN_ENABLED", "false")
    reload_config()
    register_plan("s1", [{"tool_name": "read_file", "args": {"path": "/a"}}])
    assert gate_next_step("s1") is None
    out = json.loads(handle_jev_execution_plan({"steps": [{"tool_name": "x"}]}, "s1"))
    assert out["ok"] is False


def test_plan_gate_when_enabled(monkeypatch):
    monkeypatch.setenv("JEV_AUTONOMOUS_PLAN_ENABLED", "true")
    reload_config()
    set_judge_override(lambda m, t, s: PlanGateJudgment(
        allow_next_step=True, reason="ok", skip_remaining=False
    ))
    register_plan("s1", [
        {"tool_name": "read_file", "args": {"path": "/a"}},
        {"tool_name": "read_file", "args": {"path": "/b"}},
    ])
    step = gate_next_step("s1")
    assert step is not None
    assert step["tool_name"] == "read_file"
    clear_plan("s1")

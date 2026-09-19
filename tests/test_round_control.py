from policy import should_finish_round
from state import STORE
from config import reload_config
from jev import set_judge_override
from schemas import RoundControlJudgment


def _success_judgment(**over):
    base = dict(
        goal_satisfied=0.95,
        evidence_sufficient=0.9,
        contains_failure=0.05,
        another_tool_needed=0.1,
        requires_main_model=0.2,
        outcome="success",
        expects_explanation=False,
        evidence_bullets=["tests passed: 12 ok"],
        can_render_deterministically=True,
    )
    base.update(over)
    return RoundControlJudgment(**base)


def test_finish_when_thresholds_met():
    reload_config()
    set_judge_override(lambda m, t, s: _success_judgment())
    out = should_finish_round(
        session_id="s1",
        user_goal="run tests",
        tool_calls=[{"name": "terminal", "args": "pytest"}],
        tool_results=[{"name": "terminal", "content": "12 passed", "status": "ok"}],
        statuses=["ok"],
        mutated=False,
        api_call_count=1,
    )
    assert out is not None
    assert out["action"] == "finish"
    assert "passed" in out["message"].lower() or "Done" in out["message"]
    assert STORE.get("s1").main_model_calls_avoided == 1


def test_continue_when_needs_model():
    reload_config()
    set_judge_override(lambda m, t, s: _success_judgment(
        requires_main_model=0.9, can_render_deterministically=False, evidence_bullets=[]
    ))
    out = should_finish_round(
        session_id="s1",
        user_goal="explain the architecture",
        tool_calls=[{"name": "read_file", "args": "{}"}],
        tool_results=[{"name": "read_file", "content": "class Foo: ...", "status": "ok"}],
        statuses=["ok"],
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"


def test_continue_on_failure_signal():
    reload_config()
    set_judge_override(lambda m, t, s: _success_judgment(
        contains_failure=0.9, outcome="failure", can_render_deterministically=False
    ))
    out = should_finish_round(
        session_id="s1",
        user_goal="run tests",
        tool_calls=[{"name": "terminal", "args": "pytest"}],
        tool_results=[{"name": "terminal", "content": "FAILED traceback", "status": "error"}],
        statuses=["error"],
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"


def test_fast_path_without_jev():
    reload_config()
    set_judge_override(lambda *a, **k: None)  # no Jev
    session = STORE.get("s1")
    session.expects_explanation = False
    out = should_finish_round(
        session_id="s1",
        user_goal="run tests",
        tool_calls=[{"name": "terminal", "args": "pytest"}],
        tool_results=[{"name": "terminal", "content": "all tests passed", "status": "ok"}],
        statuses=["ok"],
        api_call_count=1,
    )
    assert out is not None
    assert out["action"] == "finish"


def test_fail_open_no_judge_no_fast_path():
    reload_config()
    set_judge_override(lambda *a, **k: None)
    session = STORE.get("s1")
    session.expects_explanation = True  # disable fast path
    out = should_finish_round(
        session_id="s1",
        user_goal="what does this mean?",
        tool_calls=[{"name": "read_file", "args": "{}"}],
        tool_results=[{"name": "read_file", "content": "obscure bytes", "status": "ok"}],
        statuses=["ok"],
        api_call_count=1,
    )
    assert out is None


def test_cannot_render_continues():
    reload_config()
    set_judge_override(lambda m, t, s: _success_judgment(
        can_render_deterministically=False, evidence_bullets=[]
    ))
    # Empty results → renderer returns None → continue
    out = should_finish_round(
        session_id="s1",
        user_goal="do thing",
        tool_calls=[{"name": "x", "args": "{}"}],
        tool_results=[],
        statuses=[],
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"

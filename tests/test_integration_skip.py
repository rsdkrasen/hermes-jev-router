"""Integration-style mock: prove finishing skips the next main-model API call.

Simulates the conversation loop contract without importing Hermes:
  api_calls = 0
  while True:
      api_calls += 1          # main-model call
      run tools
      decision = post_tool_round_control(...)
      if decision.action == finish: break
      # else continue → another API call
Without the hook (always continue): 2 API calls for a one-tool-round task.
With finish: 1 API call.
"""

from __future__ import annotations

from policy import should_finish_round
from state import STORE
from config import reload_config
from jev import set_judge_override
from schemas import RoundControlJudgment


def _loop(use_jev_finish: bool) -> int:
    """Return number of main-model API calls for a simple 'run tests' turn."""
    reload_config()
    STORE.clear()
    session_id = "integ"

    session = STORE.get(session_id)
    if use_jev_finish:
        session.expects_explanation = False
        set_judge_override(lambda m, t, s: RoundControlJudgment(
            goal_satisfied=0.95, evidence_sufficient=0.9, contains_failure=0.0,
            another_tool_needed=0.0, requires_main_model=0.1, outcome="success",
            expects_explanation=False, evidence_bullets=["12 passed"],
            can_render_deterministically=True,
        ))
        result_content = "12 passed in 0.3s"
    else:
        # Disable fast-path; force Jev to say continue → second main-model call
        session.expects_explanation = True
        set_judge_override(lambda m, t, s: RoundControlJudgment(
            goal_satisfied=0.2, evidence_sufficient=0.2, contains_failure=0.0,
            another_tool_needed=0.8, requires_main_model=0.9, outcome="partial",
            expects_explanation=True, evidence_bullets=[],
            can_render_deterministically=False,
        ))
        result_content = "collected 12 items; output inconclusive for narrative answer"

    api_calls = 0
    final = None
    # Cap iterations like a real loop
    for _ in range(5):
        api_calls += 1  # main-model provider request
        # Model emits a tool call on first iteration; on later ones would emit text.
        if api_calls == 1:
            tool_calls = [{"name": "terminal", "args": "pytest -q"}]
            tool_results = [{"name": "terminal", "content": result_content, "status": "ok"}]
            statuses = ["ok"]
            decision = should_finish_round(
                session_id=session_id,
                user_goal="run the unit tests",
                tool_calls=tool_calls,
                tool_results=tool_results,
                statuses=statuses,
                mutated=False,
                api_call_count=api_calls,
            )
            if decision and decision.get("action") == "finish":
                final = decision["message"]
                break
            # continue → next API call
            continue
        else:
            # Second API call: model produces final text
            final = "Tests look good (12 passed)."
            break
    assert final
    return api_calls


def test_without_finish_needs_two_api_calls():
    assert _loop(use_jev_finish=False) == 2


def test_with_finish_needs_one_api_call():
    assert _loop(use_jev_finish=True) == 1
    assert STORE.get("integ").main_model_calls_avoided >= 1

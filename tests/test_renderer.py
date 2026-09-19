from renderer import render_from_evidence, can_fast_path_success, render_fast_path
from schemas import RoundControlJudgment


def test_never_invents_without_evidence():
    assert render_from_evidence(user_goal="x", tool_calls=[], tool_results=[]) is None


def test_uses_judgment_bullets():
    j = RoundControlJudgment(
        goal_satisfied=1, evidence_sufficient=1, contains_failure=0,
        another_tool_needed=0, requires_main_model=0, outcome="success",
        expects_explanation=False, evidence_bullets=["file deleted"], can_render_deterministically=True,
    )
    msg = render_from_evidence(user_goal="del", tool_calls=[], tool_results=[], judgment=j)
    assert msg and "file deleted" in msg


def test_fast_path_gate():
    assert can_fast_path_success(
        tool_results=[{"content": "12 passed"}],
        statuses=["ok"],
        expects_explanation=False,
    )
    assert not can_fast_path_success(
        tool_results=[{"content": "12 passed"}],
        statuses=["ok"],
        expects_explanation=True,
    )
    assert not can_fast_path_success(
        tool_results=[{"content": "error failed"}],
        statuses=["error"],
        expects_explanation=False,
    )


def test_render_fast_path():
    assert "Completed" in render_fast_path(
        [{"name": "terminal", "content": "ok done"}],
        [{"name": "terminal"}],
    ) or "Done" in render_fast_path(
        [{"name": "terminal", "content": "ok done"}],
        [{"name": "terminal"}],
    )

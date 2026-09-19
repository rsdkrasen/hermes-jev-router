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
    mut = ("terminal", "write_file")
    obs = ("read_file",)
    assert can_fast_path_success(
        tool_results=[{"name": "terminal", "content": "12 passed"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "terminal"}],
        mutating_tools=mut,
        observational_tools=obs,
        mutated=True,
    )
    assert not can_fast_path_success(
        tool_results=[{"name": "terminal", "content": "12 passed"}],
        statuses=["ok"],
        expects_explanation=True,
        tool_calls=[{"name": "terminal"}],
        mutating_tools=mut,
        observational_tools=obs,
        mutated=True,
    )
    assert not can_fast_path_success(
        tool_results=[{"name": "terminal", "content": "error failed"}],
        statuses=["error"],
        expects_explanation=False,
        tool_calls=[{"name": "terminal"}],
        mutating_tools=mut,
        observational_tools=obs,
        mutated=True,
    )


def test_render_fast_path():
    out = render_fast_path(
        [{"name": "terminal", "content": "deleted ok"}],
        [{"name": "terminal"}],
    )
    assert "Completed" in out or "Done" in out

import jev_router as plugin
from state import STORE
from jev import set_judge_override
from schemas import GoalClass
from config import reload_config


def test_preflight_captures_goal_no_injection():
    reload_config()
    set_judge_override(lambda m, t, s: GoalClass(
        expects_explanation=False, goal_kind="verify", short_goal="run unit tests"
    ))
    result = plugin._on_pre_llm_call(
        session_id="s1", user_message="Please run the unit tests", is_first_turn=True
    )
    assert result is None  # no context injection
    s = STORE.get("s1")
    assert "test" in s.user_goal.lower()
    assert s.expects_explanation is False

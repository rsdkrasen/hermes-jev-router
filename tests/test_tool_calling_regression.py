"""Regressions: enabling jev-router must not incorrectly block or finish tool rounds."""

from __future__ import annotations

from config import get_config, reload_config
from policy import check_duplicate, should_finish_round
from renderer import can_fast_path_success
from state import STORE
from jev import set_judge_override
from schemas import DuplicateJudgment


OBS = ("read_file", "search_files", "grep", "glob", "list_dir", "web_search")
MUT = ("write_file", "terminal", "bash", "shell", "edit_file")


def test_token_and_looking_do_not_fast_path():
    """Substring 'ok' inside Token/looking must NOT finish the round."""
    assert not can_fast_path_success(
        tool_results=[{"name": "read_file", "content": "Token count: 12"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "read_file"}],
        observational_tools=OBS,
        mutating_tools=MUT,
    )
    assert not can_fast_path_success(
        tool_results=[{"name": "read_file", "content": "looking at config.yaml"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "read_file"}],
        observational_tools=OBS,
        mutating_tools=MUT,
    )
    assert not can_fast_path_success(
        tool_results=[{"name": "read_file", "content": "hooks registered successfully"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "read_file"}],
        observational_tools=OBS,
        mutating_tools=MUT,
    )


def test_observational_only_round_never_fast_paths():
    """A lone successful read must continue to the main model (multi-step tool calling)."""
    assert not can_fast_path_success(
        tool_results=[{"name": "read_file", "content": "ok done complete success"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "read_file", "args": {"path": "/a"}}],
        observational_tools=OBS,
        mutating_tools=MUT,
        mutated=False,
    )


def test_mutator_with_clear_success_can_fast_path():
    assert can_fast_path_success(
        tool_results=[{"name": "terminal", "content": "deleted /tmp/x successfully"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "terminal"}],
        observational_tools=OBS,
        mutating_tools=MUT,
        mutated=True,
    )


def test_should_finish_does_not_end_after_read_with_token_text():
    reload_config()
    set_judge_override(lambda *a, **k: None)  # no Jev → must fail open (continue)
    out = should_finish_round(
        session_id="reg1",
        user_goal="inspect the file and then edit it",
        tool_calls=[{"name": "read_file", "args": {"path": "/a"}}],
        tool_results=[{"name": "read_file", "content": "Token budget notes\nlooking good", "status": "ok"}],
        statuses=["ok"],
        mutated=False,
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"


def test_first_tool_call_never_blocked():
    reload_config()
    set_judge_override(None)
    out = check_duplicate(tool_name="read_file", args={"path": "/a"}, session_id="reg2")
    assert out is None


def test_pre_tool_block_shape_is_hermes_compatible():
    reload_config()
    set_judge_override(
        lambda m, t, s: DuplicateJudgment(
            redundancy=0.99, relevance=0.1, is_observational=True, reason="dup"
        )
    )
    s = STORE.get("reg3")
    s.record_call(
        tool="read_file", args={"path": "/a"}, result="x", success=True,
        observational=True, mutating=False,
    )
    out = check_duplicate(tool_name="read_file", args={"path": "/a"}, session_id="reg3")
    assert out is not None
    assert set(out.keys()) >= {"action", "message"}
    assert out["action"] == "block"
    assert isinstance(out["message"], str) and out["message"].strip()


def test_transform_preserves_non_string_by_returning_none():
    from compactor import compact_tool_result

    # Non-string results must not be rewritten (Hermes may pass dict/list).
    assert compact_tool_result(tool_name="x", args={}, result={"ok": True}, session_id="t") is None
    assert compact_tool_result(tool_name="x", args={}, result=["a", "b"], session_id="t") is None
    assert compact_tool_result(tool_name="x", args={}, result=None, session_id="t") is None
    # Short strings unchanged
    assert compact_tool_result(tool_name="x", args={}, result="short", session_id="t") is None


def test_hook_wrappers_fail_open_on_bad_shapes():
    import jev_router as plugin

    # Malformed / empty — wrappers must not raise and must not invent a block.
    assert plugin._on_pre_tool_call(tool_name="read_file", args={"path": "/z"}, session_id="fresh") is None

    class Boom:
        pass

    assert plugin._on_transform_tool_result(tool_name="x", args={}, result=Boom(), session_id="t") is None

"""Unit-test the post_tool_round_control helper contract WITHOUT a Hermes install.

Mirrors get_post_tool_round_control from the core patch, mocking invoke_hook.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_post_tool_round_control_mockable(invoke_hook, **kwargs) -> Optional[Dict[str, Any]]:
    """Copy of the patched helper logic for offline testing."""
    try:
        hook_results = invoke_hook("post_tool_round_control", **kwargs)
    except Exception:
        return None
    for result in hook_results:
        if not isinstance(result, dict):
            continue
        action = str(result.get("action") or "").strip().lower()
        if action == "finish":
            message = result.get("message")
            if isinstance(message, str) and message.strip():
                return {"action": "finish", "message": message.strip()}
    return None


def test_finish_wins():
    def inv(name, **kw):
        return [{"action": "continue"}, {"action": "finish", "message": "All good"}]

    out = get_post_tool_round_control_mockable(inv, session_id="s")
    assert out == {"action": "finish", "message": "All good"}


def test_continue_and_none():
    assert get_post_tool_round_control_mockable(lambda *a, **k: [], session_id="s") is None
    assert get_post_tool_round_control_mockable(
        lambda *a, **k: [{"action": "continue"}], session_id="s"
    ) is None


def test_fail_open_on_exception():
    def boom(*a, **k):
        raise RuntimeError("x")

    assert get_post_tool_round_control_mockable(boom) is None


def test_empty_finish_message_ignored():
    def inv(name, **kw):
        return [{"action": "finish", "message": "  "}]

    assert get_post_tool_round_control_mockable(inv) is None


def test_plugin_policy_matches_helper_shape():
    """End-to-end: plugin should_finish_round output is helper-compatible."""
    from policy import should_finish_round
    from config import reload_config
    from state import STORE
    from jev import set_judge_override

    reload_config()
    STORE.clear()
    set_judge_override(None)
    # Use fast path
    decision = should_finish_round(
        session_id="s",
        user_goal="rm file",
        tool_calls=[{"name": "terminal"}],
        tool_results=[{"name": "terminal", "content": "deleted ok", "status": "ok"}],
        statuses=["ok"],
        api_call_count=1,
    )
    assert decision and decision["action"] == "finish"
    # Feed through helper mock
    out = get_post_tool_round_control_mockable(lambda *a, **k: [decision])
    assert out["action"] == "finish"

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
        user_goal="run tests",
        tool_calls=[{"name": "terminal"}],
        tool_results=[{"name": "terminal", "content": "12 passed", "status": "ok"}],
        statuses=["ok"],
        api_call_count=1,
    )
    assert decision and decision["action"] == "finish"
    # Feed through helper mock
    out = get_post_tool_round_control_mockable(lambda *a, **k: [decision])
    assert out["action"] == "finish"


def _tool_result_status_for_control(content_s: str) -> str:
    """Mirror of hermes-core-snippets helper for offline testing (no Hermes install)."""
    import re as _re

    text = content_s if isinstance(content_s, str) else str(content_s or "")
    low = text.lower()

    _SUCCESS_PHRASES = (
        "0 failures",
        "0 failed",
        "all tests passed",
        "tests passed",
    )
    _SUCCESS_VERIFY_RE = _re.compile(
        r"(?i)(?<![A-Za-z0-9_])\d+\s+passed(?![A-Za-z0-9_])"
    )
    has_success = any(p in low for p in _SUCCESS_PHRASES) or bool(
        _SUCCESS_VERIFY_RE.search(text)
    )

    scrubbed = _re.sub(
        r"""(?ix)["']?error["']?\s*:\s*(?:null|none|false|""|''|\[\s*\]|\{\s*\})""",
        " ",
        text,
    )
    for phrase in _SUCCESS_PHRASES:
        scrubbed = _re.sub(_re.escape(phrase), " ", scrubbed, flags=_re.IGNORECASE)
    scrubbed = _SUCCESS_VERIFY_RE.sub(" ", scrubbed)

    _CLEAR_FAILURE_RE = _re.compile(
        r"(?i)(?<![A-Za-z0-9_])("
        r"traceback|"
        r"exception\s*:|"
        r"fatal\s+error|"
        r"command\s+failed|"
        r"exit[_ ]?code\s*[:=]\s*[1-9]\d*"
        r")(?![A-Za-z0-9_])"
    )
    if _CLEAR_FAILURE_RE.search(scrubbed):
        return "error"

    _FAIL_WORD_RE = _re.compile(
        r"(?i)(?<![A-Za-z0-9_])(error|failed|failure|exception|fatal)(?![A-Za-z0-9_])"
    )
    if has_success:
        return "error" if _FAIL_WORD_RE.search(scrubbed) else "ok"
    return "error" if _FAIL_WORD_RE.search(scrubbed) else "ok"


def test_core_status_ignores_null_error_json():
    assert _tool_result_status_for_control('12 passed\n{"error": null}') == "ok"
    assert _tool_result_status_for_control('all tests passed') == "ok"
    assert _tool_result_status_for_control('{"error": null, "exit_code": 0}') == "ok"
    assert _tool_result_status_for_control("Traceback (most recent call last)") == "error"
    assert _tool_result_status_for_control("exception: boom") == "error"
    assert _tool_result_status_for_control("command failed") == "error"
    assert _tool_result_status_for_control("exit_code: 2") == "error"
    assert _tool_result_status_for_control("12 passed\nTraceback (most recent)") == "error"
    assert _tool_result_status_for_control("something failed") == "error"

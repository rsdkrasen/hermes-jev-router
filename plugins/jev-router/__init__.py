"""jev-router — TypeSafe Jev decides WHETHER; Python decides HOW.

Standalone Hermes plugin (install to ~/.hermes/plugins/jev-router/).
Requires the tiny core patch that adds the ``post_tool_round_control`` hook.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import plan as plan_mod
from .compactor import compact_tool_result
from .config import get_config, reload_config
from .jev import set_judge_override
from .policy import check_duplicate, is_mutating, is_observational, should_finish_round
from .schemas import GoalClass
from .state import STORE
from .telemetry import aggregate, record_event
from .jev import judge

logger = logging.getLogger("hermes.plugins.jev_router")

__all__ = [
    "register",
    "set_judge_override",
    "reload_config",
    "STORE",
    "aggregate",
]


def _session_key(session_id: str = "", task_id: str = "") -> str:
    return session_id or task_id or "default"


def _on_pre_llm_call(
    session_id: str = "",
    task_id: str = "",
    user_message: str = "",
    conversation_history: Any = None,
    is_first_turn: bool = False,
    **_: Any,
) -> None:
    """F6: capture goal (+ optional cheap classification). Do NOT inject context (cache-friendly)."""
    cfg = get_config()
    if not cfg.enabled:
        return
    try:
        sid = _session_key(session_id, task_id)
        session = STORE.get(sid)
        text = user_message if isinstance(user_message, str) else ""
        if text.strip():
            session.user_goal = text.strip()[:800]
        if cfg.preflight_classify and text.strip() and cfg.typesafe_ready:
            g = judge(GoalClass, {"user_message": text[:600]})
            if g is not None:
                session.expects_explanation = bool(g.expects_explanation)
                session.goal_kind = g.goal_kind
                if g.short_goal:
                    session.user_goal = g.short_goal[:800]
        record_event(session, "preflight", goal_kind=session.goal_kind,
                     expects_explanation=session.expects_explanation)
    except Exception as exc:
        logger.debug("pre_llm_call failed open: %s", exc)
        try:
            sid = _session_key(session_id, task_id)
            record_event(STORE.get(sid), "fail_open", hook="pre_llm_call",
                         reason="exception", error=type(exc).__name__)
        except Exception:
            pass
    # Intentionally return None — no prompt injection.


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    task_id: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """F2: duplicate suppression. Only Hermes-recognized block shape may deny a tool."""
    try:
        out = check_duplicate(
            tool_name=tool_name,
            args=args,
            session_id=_session_key(session_id, task_id),
            **kwargs,
        )
    except Exception as exc:
        logger.debug("pre_tool_call failed open: %s", exc)
        try:
            record_event(
                STORE.get(_session_key(session_id, task_id)), "fail_open",
                hook="pre_tool_call", reason="exception",
                tool=tool_name, error=type(exc).__name__,
            )
        except Exception:
            pass
        return None
    if out is None:
        return None
    # Fail-open on malformed deny directives (empty message is ignored by Hermes anyway,
    # but a wrong action key must never look like a soft-block).
    if not isinstance(out, dict):
        return None
    if out.get("action") != "block":
        return None
    msg = out.get("message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    return {"action": "block", "message": msg}


def _on_transform_tool_result(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    status: str = "",
    **kwargs: Any,
) -> Optional[str]:
    """F1: compaction. Hermes keeps the first *string* return; never return a non-str."""
    try:
        out = compact_tool_result(
            tool_name=tool_name,
            args=args,
            result=result,
            session_id=_session_key(session_id, task_id),
            task_id=task_id,
            status=status,
            **kwargs,
        )
    except Exception as exc:
        logger.debug("transform_tool_result failed open: %s", exc)
        try:
            record_event(
                STORE.get(_session_key(session_id, task_id)), "fail_open",
                hook="transform_tool_result", reason="exception",
                tool=tool_name, error=type(exc).__name__,
            )
        except Exception:
            pass
        return None
    # Fail-open on wrong shape — a non-str would replace the tool result incorrectly
    # on some Hermes builds, or be ignored on others. Only strings may rewrite.
    if out is None:
        return None
    if not isinstance(out, str):
        logger.debug("transform_tool_result ignored non-str %s", type(out).__name__)
        return None
    return out


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    **_: Any,
) -> None:
    """Track call history for duplicate detection / mutation epochs."""
    try:
        sid = _session_key(session_id, task_id)
        session = STORE.get(sid)
        result_s = result if isinstance(result, str) else str(result or "")
        low = result_s.lower()
        success = not any(t in low for t in ("error", "traceback", "exception", "failed", "fatal"))
        session.record_call(
            tool=tool_name,
            args=args or {},
            result=result_s,
            success=success,
            observational=is_observational(tool_name),
            mutating=is_mutating(tool_name),
            history_size=get_config().dup_history_size,
        )
    except Exception as exc:
        logger.debug("post_tool_call track failed open: %s", exc)


def _on_post_tool_round_control(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """F3: decide whether to finish without another main-model API call."""
    sid = _session_key(kwargs.get("session_id", ""), kwargs.get("task_id", ""))
    kwargs = {**kwargs, "session_id": sid}
    return should_finish_round(**kwargs)


def _on_session_end(session_id: str = "", **_: Any) -> None:
    try:
        stats = aggregate(session_id)
        logger.info("jev-router session end: %s", stats)
        STORE.drop(session_id)
    except Exception:
        pass


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint."""
    reload_config()
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("post_tool_round_control", _on_post_tool_round_control)
    ctx.register_hook("on_session_end", _on_session_end)

    # Optional tool for F5 — only useful when JEV_AUTONOMOUS_PLAN_ENABLED=true
    try:
        ctx.register_tool(
            name="jev_execution_plan",
            toolset="jev-router",
            schema={
                "name": "jev_execution_plan",
                "description": (
                    "Register a pre-specified autonomous execution plan. "
                    "Jev only gates the next step. Disabled unless JEV_AUTONOMOUS_PLAN_ENABLED=true."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tool_name": {"type": "string"},
                                    "args": {"type": "object"},
                                    "note": {"type": "string"},
                                },
                                "required": ["tool_name"],
                            },
                        }
                    },
                    "required": ["steps"],
                },
            },
            handler=lambda args, **kw: plan_mod.handle_jev_execution_plan(
                args if isinstance(args, dict) else {},
                session_id=str(kw.get("session_id") or ""),
            ),
            description="Register a Jev-gated autonomous execution plan (opt-in).",
        )
    except TypeError:
        # Kw-only / positional signature drift across Hermes versions — retry minimal form.
        try:
            ctx.register_tool(
                "jev_execution_plan",
                "jev-router",
                {
                    "name": "jev_execution_plan",
                    "description": "Register a Jev-gated autonomous execution plan (opt-in).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "steps": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["steps"],
                    },
                },
                lambda args, **kw: plan_mod.handle_jev_execution_plan(
                    args if isinstance(args, dict) else {},
                    session_id=str(kw.get("session_id") or ""),
                ),
            )
        except Exception as exc:
            logger.debug("jev_execution_plan tool registration skipped: %s", exc)
    except Exception as exc:
        logger.debug("jev_execution_plan tool registration skipped: %s", exc)

    logger.info("jev-router registered (model=%s)", get_config().model)

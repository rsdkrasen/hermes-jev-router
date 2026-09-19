"""F2 duplicate suppression + F3 post_tool_round_control skip policy."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from .config import get_config
from .jev import judge_timed, get_judge_override
from .renderer import fast_path_decision, render_fast_path, render_from_evidence
from .schemas import DuplicateJudgment, RoundControlJudgment
from .state import STORE, SessionState
from .telemetry import record_event, tool_names

logger = logging.getLogger("hermes.plugins.jev_router.policy")

# Fast-path skip reasons that themselves explain why we continue (prefer over jev_unavailable).
_STRUCTURAL_CONTINUE = frozenset(
    {
        "file_mutation_no_fast_path",
        "observational_only",
        "no_verify_phrase",
        "expects_explanation",
        "not_terminal_verify_tools",
        "status_failure",
        "failure_in_content",
        "no_results",
    }
)


def is_mutating(tool_name: str) -> bool:
    return tool_name in get_config().mutating_tools


def is_observational(tool_name: str) -> bool:
    cfg = get_config()
    if tool_name in cfg.mutating_tools:
        return False
    return tool_name in cfg.observational_tools


def check_duplicate(
    *,
    tool_name: str,
    args: Optional[Dict[str, Any]],
    session_id: str = "",
    turn_id: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """pre_tool_call: block observational duplicates. Fail-open → None (approve)."""
    cfg = get_config()
    if not cfg.enabled:
        return None
    tid = turn_id or str(kwargs.get("turn_id") or "")
    try:
        return _check_duplicate(
            cfg, tool_name=tool_name, args=args or {}, session_id=session_id, turn_id=tid,
        )
    except Exception as exc:
        logger.debug("duplicate check failed open: %s", exc)
        try:
            session = STORE.get(session_id)
            record_event(
                session, "fail_open",
                hook="pre_tool_call", reason="exception", tool=tool_name,
                error=type(exc).__name__, turn_id=tid,
            )
        except Exception:
            pass
        return None


def _check_duplicate(
    cfg, *, tool_name: str, args: Dict[str, Any], session_id: str, turn_id: str = "",
) -> Optional[Dict[str, Any]]:
    session = STORE.get(session_id)
    if turn_id:
        session.last_turn_id = turn_id

    # Never aggressively block mutators
    if is_mutating(tool_name):
        record_event(session, "pre_tool_allow", tool=tool_name, reason="mutating_tool")
        return None

    prior = session.recent_same_tool(tool_name, args)
    if prior is None:
        record_event(session, "pre_tool_allow", tool=tool_name, reason="no_prior")
        return None

    # Allow re-verify after mutation
    if prior.mutation_epoch < session.mutation_epoch:
        record_event(session, "pre_tool_allow", tool=tool_name, reason="mutation_epoch_advanced")
        return None

    if not (prior.observational or is_observational(tool_name)):
        record_event(session, "pre_tool_allow", tool=tool_name, reason="not_observational")
        return None

    state = {
        "tool_name": tool_name,
        "args": args,
        "user_goal": session.user_goal[:300],
        "prior_seq": prior.seq,
        "prior_success": prior.success,
        "mutation_epoch": session.mutation_epoch,
        "prior_mutation_epoch": prior.mutation_epoch,
        "same_args": True,
    }
    # Hermes fail-closes timed-out pre_tool_call (blocks the tool). Never make a
    # network Jev call on this hot path — use the injectable override for tests only.
    judgment = None
    jev_ms = None
    if get_judge_override() is not None:
        judgment, jev_ms = judge_timed(DuplicateJudgment, state)
        if jev_ms is not None:
            session.last_jev_ms = jev_ms

    # Deterministic fallback when Jev unavailable: same tool+args, no mutation → block
    if judgment is None:
        redundancy, relevance = 1.0, 0.2
        observational = True
        reason = "identical observational call with no intervening mutation"
    else:
        redundancy = float(judgment.redundancy)
        relevance = float(judgment.relevance)
        observational = bool(judgment.is_observational)
        reason = judgment.reason or "duplicate"

    extra_ms = {"jev_ms": jev_ms} if jev_ms is not None else {}
    if observational and redundancy >= cfg.dup_redundancy_min and relevance <= cfg.dup_relevance_max:
        session.duplicates_blocked += 1
        record_event(
            session, "dup_block",
            tool=tool_name, action="block", reason=reason,
            redundancy=redundancy, relevance=relevance,
            turn_id=turn_id, **extra_ms,
        )
        return {
            "action": "block",
            "message": (
                f"[jev-router] Blocked duplicate observational call `{tool_name}` "
                f"(redundancy={redundancy:.2f}, relevance={relevance:.2f}): {reason}. "
                f"Reuse prior result (seq={prior.seq})."
            ),
        }

    record_event(
        session, "pre_tool_allow",
        tool=tool_name, reason="below_dup_thresholds",
        redundancy=redundancy, relevance=relevance,
        turn_id=turn_id, **extra_ms,
    )
    return None


def should_finish_round(
    *,
    session_id: str = "",
    task_id: str = "",
    turn_id: str = "",
    user_goal: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    tool_results: Optional[List[Dict[str, Any]]] = None,
    statuses: Optional[List[str]] = None,
    errors: Optional[List[Dict[str, Any]]] = None,
    mutated: bool = False,
    verification: Optional[Dict[str, Any]] = None,
    api_call_count: int = 0,
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """post_tool_round_control decision. Fail-open → None (continue)."""
    cfg = get_config()
    if not cfg.enabled:
        return None
    try:
        return _should_finish(
            cfg,
            session_id=session_id,
            user_goal=user_goal,
            tool_calls=tool_calls or [],
            tool_results=tool_results or [],
            statuses=statuses or [],
            errors=errors or [],
            mutated=mutated,
            api_call_count=api_call_count,
            turn_id=turn_id,
        )
    except Exception as exc:
        logger.debug("round control failed open: %s", exc)
        try:
            session = STORE.get(session_id)
            record_event(
                session, "fail_open",
                hook="post_tool_round_control",
                reason="exception",
                action="continue",
                error=type(exc).__name__,
                tools=tool_names(tool_calls, tool_results),
                mutated=mutated,
                api_call_count=api_call_count,
                turn_id=turn_id,
            )
        except Exception:
            pass
        return None


def _thresholds_met(cfg, j: RoundControlJudgment) -> bool:
    return (
        j.goal_satisfied >= cfg.goal_satisfied_min
        and j.evidence_sufficient >= cfg.evidence_sufficient_min
        and j.contains_failure <= cfg.contains_failure_max
        and j.another_tool_needed <= cfg.another_tool_needed_max
        and j.requires_main_model <= cfg.requires_main_model_max
        and j.outcome == "success"
    )


def _status_names(statuses: Sequence[str]) -> List[str]:
    """Status strings only (for decision logs / analysis)."""
    return [str(s) for s in (statuses or ())]


def _log_continue(
    session: SessionState,
    *,
    reason: str,
    tools: List[str],
    mutated: bool,
    api_call_count: int,
    expects_explanation: bool,
    statuses: Optional[Sequence[str]] = None,
    **extra: Any,
) -> None:
    record_event(
        session, "round_continue",
        action="continue",
        reason=reason,
        tools=tools,
        statuses=_status_names(statuses or ()),
        mutated=mutated,
        expects_explanation=expects_explanation,
        api_call_count=api_call_count,
        **extra,
    )


def _log_finish(
    session: SessionState,
    *,
    reason: str,
    tools: List[str],
    mutated: bool,
    api_call_count: int,
    expects_explanation: bool,
    statuses: Optional[Sequence[str]] = None,
    **extra: Any,
) -> None:
    record_event(
        session, "round_finish",
        action="finish",
        reason=reason,
        tools=tools,
        statuses=_status_names(statuses or ()),
        mutated=mutated,
        expects_explanation=expects_explanation,
        api_call_count=api_call_count,
        **extra,
    )


def _should_finish(
    cfg,
    *,
    session_id: str,
    user_goal: str,
    tool_calls: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
    statuses: List[str],
    errors: List[Dict[str, Any]],
    mutated: bool,
    api_call_count: int,
    turn_id: str = "",
) -> Optional[Dict[str, Any]]:
    session = STORE.get(session_id)
    if turn_id:
        session.last_turn_id = turn_id
    if user_goal and not session.user_goal:
        session.user_goal = user_goal
    session.api_call_count = api_call_count
    session.last_tool_calls = list(tool_calls)
    session.last_tool_results = list(tool_results)
    session.last_statuses = list(statuses)
    session.last_mutated = mutated

    tools = tool_names(tool_calls, tool_results)
    expects = bool(session.expects_explanation)

    # Fast path: obvious success, no explanation expected.
    ok, fp_reason = fast_path_decision(
        tool_results=tool_results,
        statuses=statuses,
        expects_explanation=expects,
        tool_calls=tool_calls,
        mutated=mutated,
        observational_tools=cfg.observational_tools,
        mutating_tools=cfg.mutating_tools,
    )
    if ok:
        message = render_fast_path(tool_results, tool_calls)
        session.finishes += 1
        session.main_model_calls_avoided += 1
        _log_finish(
            session,
            reason="fast_path_terminal_verify",
            tools=tools,
            statuses=statuses,
            mutated=mutated,
            api_call_count=api_call_count,
            expects_explanation=expects,
            fast_path_reason=fp_reason,
            **({"turn_id": turn_id} if turn_id else {}),
        )
        return {"action": "finish", "message": message}

    state = {
        "user_goal": (user_goal or session.user_goal)[:400],
        "expects_explanation": expects,
        "tool_calls": tool_calls,
        "tool_results": [
            {**r, "content": str(r.get("content") or "")[: cfg.result_preview_chars]} for r in tool_results
        ],
        "statuses": statuses,
        "errors": errors[:5],
        "mutated": mutated,
        "api_call_count": api_call_count,
    }
    judgment, jev_ms = judge_timed(RoundControlJudgment, state)
    if jev_ms is not None:
        session.last_jev_ms = jev_ms
    ms_kw = {"jev_ms": jev_ms} if jev_ms is not None else {}
    tid_kw = {"turn_id": turn_id} if turn_id else {}

    if judgment is None:
        # Prefer structural fast-path skip reason so live logs explain *why*
        # we did not finish (file write / observational / no verify phrase).
        reason = fp_reason if fp_reason in _STRUCTURAL_CONTINUE else "jev_unavailable"
        _log_continue(
            session,
            reason=reason,
            tools=tools,
            statuses=statuses,
            mutated=mutated,
            api_call_count=api_call_count,
            expects_explanation=expects,
            fast_path_reason=fp_reason,
            jev="unavailable",
            **tid_kw,
            **ms_kw,
        )
        return None  # fail open → continue to main model

    if not _thresholds_met(cfg, judgment):
        _log_continue(
            session,
            reason="thresholds_not_met",
            tools=tools,
            statuses=statuses,
            mutated=mutated,
            api_call_count=api_call_count,
            expects_explanation=expects,
            fast_path_reason=fp_reason,
            goal_satisfied=judgment.goal_satisfied,
            evidence_sufficient=judgment.evidence_sufficient,
            contains_failure=judgment.contains_failure,
            another_tool_needed=judgment.another_tool_needed,
            requires_main_model=judgment.requires_main_model,
            outcome=judgment.outcome,
            **tid_kw,
            **ms_kw,
        )
        return {"action": "continue"}

    message = render_from_evidence(
        user_goal=user_goal or session.user_goal,
        tool_calls=tool_calls,
        tool_results=tool_results,
        judgment=judgment,
    )
    if not message:
        # Thresholds say finish but renderer cannot — continue to model (never invent)
        _log_continue(
            session,
            reason="cannot_render",
            tools=tools,
            statuses=statuses,
            mutated=mutated,
            api_call_count=api_call_count,
            expects_explanation=expects,
            fast_path_reason=fp_reason,
            goal_satisfied=judgment.goal_satisfied,
            outcome=judgment.outcome,
            **tid_kw,
            **ms_kw,
        )
        return {"action": "continue"}

    session.finishes += 1
    session.main_model_calls_avoided += 1
    _log_finish(
        session,
        reason="jev_judgment",
        tools=tools,
        statuses=statuses,
        mutated=mutated,
        api_call_count=api_call_count,
        expects_explanation=expects,
        fast_path_reason=fp_reason,
        goal_satisfied=judgment.goal_satisfied,
        evidence_sufficient=judgment.evidence_sufficient,
        requires_main_model=judgment.requires_main_model,
        outcome=judgment.outcome,
        **tid_kw,
        **ms_kw,
    )
    return {"action": "finish", "message": message}

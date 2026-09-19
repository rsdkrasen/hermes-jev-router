"""F5: optional autonomous continuation plan. Default OFF. Isolated from normal skip path."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .config import get_config
from .jev import judge
from .schemas import ExecutionPlan, PlanGateJudgment, PlanStep
from .state import STORE
from .telemetry import record_event

logger = logging.getLogger("hermes.plugins.jev_router.plan")


def register_plan(session_id: str, steps: List[Dict[str, Any]]) -> ExecutionPlan:
    plan = ExecutionPlan(
        steps=[PlanStep(tool_name=s["tool_name"], args=dict(s.get("args") or {}), note=str(s.get("note") or ""))
               for s in steps],
        current_index=0,
        active=True,
    )
    session = STORE.get(session_id)
    session.plan = plan.model_dump()
    record_event(session, "plan_registered", n_steps=len(plan.steps))
    return plan


def clear_plan(session_id: str) -> None:
    session = STORE.get(session_id)
    session.plan = None


def gate_next_step(session_id: str) -> Optional[Dict[str, Any]]:
    """Return next step dict if allowed, else None. Fail-open → None (no forced step)."""
    cfg = get_config()
    if not cfg.autonomous_plan_enabled:
        return None
    session = STORE.get(session_id)
    if not session.plan:
        return None
    try:
        plan = ExecutionPlan.model_validate(session.plan)
    except Exception:
        return None
    if not plan.active or plan.current_index >= len(plan.steps):
        return None

    step = plan.steps[plan.current_index]
    state = {
        "user_goal": session.user_goal[:300],
        "step_index": plan.current_index,
        "step": step.model_dump(),
        "remaining": len(plan.steps) - plan.current_index,
    }
    judgment = judge(PlanGateJudgment, state)
    if judgment is None:
        # Fail open: do not force autonomous step
        return None
    if judgment.skip_remaining:
        plan.active = False
        session.plan = plan.model_dump()
        record_event(session, "plan_skipped_remaining")
        return None
    if not judgment.allow_next_step:
        return None

    plan.current_index += 1
    if plan.current_index >= len(plan.steps):
        plan.active = False
    session.plan = plan.model_dump()
    record_event(session, "plan_step_allowed", index=plan.current_index - 1)
    return step.model_dump()


def handle_jev_execution_plan(args: Dict[str, Any], session_id: str = "") -> str:
    """Plugin tool handler: register a pre-specified execution plan."""
    cfg = get_config()
    if not cfg.autonomous_plan_enabled:
        return json.dumps({
            "ok": False,
            "error": "JEV_AUTONOMOUS_PLAN_ENABLED is false — plan registration ignored",
        })
    steps = args.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return json.dumps({"ok": False, "error": "steps must be a non-empty list"})
    plan = register_plan(session_id, steps)
    return json.dumps({"ok": True, "n_steps": len(plan.steps), "active": plan.active})

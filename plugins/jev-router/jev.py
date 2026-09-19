"""Jev client wrapper. Typed judge() interface — mockable; fail-open on any error."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from .config import get_config
from .redact import redact_state

logger = logging.getLogger("hermes.plugins.jev_router")

T = TypeVar("T", bound=BaseModel)

# Injectable judge for tests. Signature: (model, output_type, state_dict) -> model_instance | None
_JUDGE_OVERRIDE: Optional[Callable[..., Any]] = None

# Side-channel: latency of the most recent override/network judge attempt (ms), or None
# when judge() returned early without invoking a judge (disabled / missing key).
_last_judge_ms: Optional[float] = None


def set_judge_override(fn: Optional[Callable[..., Any]]) -> None:
    """Tests inject a mock judge; pass None to restore real client."""
    global _JUDGE_OVERRIDE
    _JUDGE_OVERRIDE = fn


def get_judge_override() -> Optional[Callable[..., Any]]:
    """Return the injectable override (None when using the real client)."""
    return _JUDGE_OVERRIDE


def get_last_judge_ms() -> Optional[float]:
    """Milliseconds for the last override/network judge call, or None if none ran."""
    return _last_judge_ms


def judge(output_type: Type[T], state: Dict[str, Any], *, model: Optional[str] = None) -> Optional[T]:
    """Ask Jev for a typed judgment.

    Returns a Pydantic model instance, or None on any failure (fail-open).
    State is redacted before leaving this process.

    Sets module ``_last_judge_ms`` when an override or network judge actually runs;
    clears it to None on early exits (disabled / no API key).
    """
    global _last_judge_ms
    _last_judge_ms = None

    cfg = get_config()
    if not cfg.enabled:
        return None

    safe_state = redact_state(state)
    model_id = model or cfg.model

    if _JUDGE_OVERRIDE is not None:
        t0 = time.perf_counter()
        try:
            result = _JUDGE_OVERRIDE(model_id, output_type, safe_state)
            if result is None:
                return None
            if isinstance(result, output_type):
                return result
            if isinstance(result, dict):
                return output_type.model_validate(result)
            return None
        except Exception as exc:
            logger.debug("jev override judge failed open: %s", exc)
            return None
        finally:
            _last_judge_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    if not cfg.typesafe_ready:
        logger.debug("jev: TYPESAFE_API_KEY missing or disabled — skip")
        return None

    t0 = time.perf_counter()
    try:
        import concurrent.futures

        # Stay well under Hermes plugins.hook_callback_timeout (default 30s).
        # Timed-out pre_tool_call is fail-closed; other hooks fail-open — either
        # way we must not hang the agent loop.
        timeout_s = float(getattr(cfg, "judge_timeout_secs", 8.0) or 8.0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_judge_pydantic_ai, model_id, output_type, safe_state)
            return fut.result(timeout=max(0.5, timeout_s))
    except Exception as exc:
        logger.debug("jev judge failed open: %s", exc)
        return None
    finally:
        _last_judge_ms = round((time.perf_counter() - t0) * 1000.0, 3)


def judge_timed(
    output_type: Type[T],
    state: Dict[str, Any],
    *,
    model: Optional[str] = None,
) -> Tuple[Optional[T], Optional[float]]:
    """Like ``judge`` but also returns latency ms (None when no judge call ran)."""
    result = judge(output_type, state, model=model)
    return result, _last_judge_ms


def _judge_pydantic_ai(model_id: str, output_type: Type[T], state: Dict[str, Any]) -> Optional[T]:
    """Real TypeSafe path via pydantic-ai Agent."""
    from pydantic_ai import Agent

    agent: Agent[None, T] = Agent(
        model_id,
        output_type=output_type,
        system_prompt=(
            "You are Jev, a routing judge. Decide WHETHER based only on the provided state. "
            "Never invent facts. Keep answers inside the typed schema. "
            "Questions to answer are in each field's description."
        ),
    )
    # Keep the user prompt tiny — state only.
    prompt = (
        "Judge the following state. Answer each schema field using only this material:\n"
        f"{state!r}"
    )
    result = agent.run_sync(prompt)
    output = getattr(result, "output", None) or getattr(result, "data", None)
    if isinstance(output, output_type):
        return output
    if output is not None:
        return output_type.model_validate(output)
    return None

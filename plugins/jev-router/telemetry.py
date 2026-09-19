"""Per-turn decision logs + aggregates. No secrets / tool result bodies."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .config import get_config
from .redact import redact_text
from .state import STORE, SessionState

logger = logging.getLogger("hermes.plugins.jev_router.telemetry")

# Keys that must never appear in persisted/log lines (tool bodies / secrets).
_STRIP_KEYS = frozenset(
    {
        "result",
        "output",
        "content",
        "raw",
        "body",
        "text",
        "message_body",
        "tool_results",
        "args",
        "arguments",
        "prompt",
        "payload",
    }
)
_SECRET_KEY_FRAGMENTS = ("key", "token", "secret", "password", "passwd", "authorization", "credential")

# Decision-ish events also mirrored to decisions.jsonl for easy grepping.
_DECISION_EVENTS = frozenset(
    {
        "preflight",
        "pre_tool_allow",
        "dup_block",
        "compact_skip",
        "compact_done",
        "round_continue",
        "round_finish",
        "fail_open",
        # legacy aliases still treated as decisions if written
        "finish_fast_path",
        "finish_jev",
        "continue_round",
        "continue_cannot_render",
    }
)

_LOOKS_LIKE_KEY = re.compile(
    r"(?i)(\bapi[_-]?key\b|\bsecret\b|\bpassword\b|\btoken\s*[=:]|sk-[A-Za-z0-9]{8,}|"
    r"ghp_[A-Za-z0-9]{8,}|xox[baprs]-|bearer\s+[A-Za-z0-9\-._~+/]+)"
)


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env and env.strip():
        return Path(env.strip())
    return Path.home() / ".hermes"


def plugin_log_dir() -> Path:
    """Directory for telemetry.jsonl / decisions.jsonl.

    Prefer ``HERMES_HOME/plugins/jev-router/``. When ``HERMES_HOME`` is unset on
    Windows, use ``%LOCALAPPDATA%/hermes/plugins/jev-router/`` so the desktop
    app always gets logs next to the installed plugin. Otherwise ``~/.hermes/...``.
    """
    hermes = os.environ.get("HERMES_HOME")
    if hermes and hermes.strip():
        return Path(hermes.strip()) / "plugins" / "jev-router"

    local = os.environ.get("LOCALAPPDATA")
    if local and local.strip():
        # Windows Hermes app layout (even when this process runs under WSL/Linux
        # with LOCALAPPDATA forwarded, or on native Windows).
        return Path(local.strip()) / "hermes" / "plugins" / "jev-router"

    return _hermes_home() / "plugins" / "jev-router"


def debug_log_path() -> Path:
    return plugin_log_dir() / "telemetry.jsonl"


def decisions_log_path() -> Path:
    return plugin_log_dir() / "decisions.jsonl"


def goal_preview(goal: str, limit: int = 80) -> str:
    """First ~80 chars of user goal for logs — newlines stripped, secrets redacted."""
    if not goal:
        return ""
    text = str(goal).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = redact_text(text)
    if _LOOKS_LIKE_KEY.search(text):
        # Prefer over-redacting when the preview itself looks like a credential.
        text = redact_text(text)
        if _LOOKS_LIKE_KEY.search(text) and not text.startswith("***") and "REDACTED" not in text:
            text = "***"
    if len(text) > limit:
        text = text[:limit]
    return text


def _sanitize(fields: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for k, v in fields.items():
        if k in _STRIP_KEYS or k.lower() in _STRIP_KEYS:
            continue
        if any(s in k.lower() for s in _SECRET_KEY_FRAGMENTS):
            clean[k] = "***"
            continue
        if isinstance(v, str) and len(v) > 500:
            clean[k] = v[:500]
        else:
            clean[k] = v
    return clean


def _tool_names_from_fields(fields: Dict[str, Any]) -> str:
    tools = fields.get("tools")
    if isinstance(tools, (list, tuple)):
        return ",".join(str(t) for t in tools if t) or "-"
    tool = fields.get("tool") or fields.get("tool_name")
    if tool:
        return str(tool)
    return "-"


def _human_summary(event: str, fields: Dict[str, Any]) -> str:
    """One-line Hermes-friendly summary (no secrets / bodies)."""
    action = fields.get("action")
    reason = fields.get("reason")
    decision = action or (
        "finish"
        if event == "round_finish"
        else "continue"
        if event == "round_continue"
        else "block"
        if event == "dup_block"
        else "allow"
        if event == "pre_tool_allow"
        else event
    )
    parts = [f"jev-router decision={decision}"]
    if reason is not None:
        parts.append(f"reason={reason}")
    tools = _tool_names_from_fields(fields)
    if tools != "-" or "tools" in fields or "tool" in fields:
        parts.append(f"tools={tools}")
    if "statuses" in fields:
        st = fields.get("statuses")
        if isinstance(st, (list, tuple)):
            parts.append("statuses=[" + ",".join(str(s) for s in st) + "]")
        else:
            parts.append(f"statuses={st}")
    if "mutated" in fields:
        parts.append(f"mutated={fields.get('mutated')}")
    if "expects_explanation" in fields:
        parts.append(f"expects_explanation={fields.get('expects_explanation')}")
    if "api_call_count" in fields:
        parts.append(f"api_call_count={fields.get('api_call_count')}")
    if "chars_in" in fields or "chars_out" in fields:
        parts.append(f"chars_in={fields.get('chars_in', '-')}")
        parts.append(f"chars_out={fields.get('chars_out', '-')}")
    if "goal_satisfied" in fields:
        parts.append(f"goal_satisfied={fields.get('goal_satisfied')}")
    if "redundancy" in fields:
        parts.append(f"redundancy={fields.get('redundancy')}")
    if fields.get("turn_id"):
        parts.append(f"turn_id={fields.get('turn_id')}")
    if "seq" in fields:
        parts.append(f"seq={fields.get('seq')}")
    if fields.get("jev_ms") is not None:
        parts.append(f"jev_ms={fields.get('jev_ms')}")
    return " ".join(parts)


def _append_jsonl(path: Path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, default=str)
    if len(line) > 2000:
        line = line[:2000]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _attach_session_context(session: SessionState, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-fill turn_id / seq / goal_preview / counters / jev_ms from session."""
    out = dict(fields)

    # turn_id: remember when provided; else reuse last known for the session
    if "turn_id" in out:
        tid = out.get("turn_id")
        if tid:
            session.last_turn_id = str(tid)
        else:
            out.pop("turn_id", None)
    elif session.last_turn_id:
        out["turn_id"] = session.last_turn_id

    # Monotonic event sequence within the session (ordering aid)
    session.event_seq += 1
    out.setdefault("seq", session.event_seq)

    if "goal_preview" not in out:
        preview = goal_preview(session.user_goal)
        if preview:
            out["goal_preview"] = preview

    out.setdefault("mutation_epoch", session.mutation_epoch)
    out.setdefault("main_model_calls_avoided", session.main_model_calls_avoided)

    # jev_ms: explicit wins; else consume session.last_jev_ms from a preceding judge
    if "jev_ms" in out:
        if out["jev_ms"] is None:
            out.pop("jev_ms", None)
        else:
            session.last_jev_ms = None  # explicit value supersedes pending
    elif session.last_jev_ms is not None:
        out["jev_ms"] = session.last_jev_ms
        session.last_jev_ms = None

    return out


def record_event(session: SessionState, event: str, **fields: Any) -> None:
    """Append a structured JSONL line + logger.info one-liner.

    Never persists tool result bodies or obvious secret fields.
    Auto-attaches ``seq``, ``goal_preview``, ``mutation_epoch``,
    ``main_model_calls_avoided``, and ``turn_id`` / ``jev_ms`` when available.
    """
    cfg = get_config()
    if not cfg.telemetry_enabled:
        return

    enriched = _attach_session_context(session, fields)
    safe = _sanitize(enriched)
    entry = {
        "ts": time.time(),
        "session_id": session.session_id,
        "event": event,
        **safe,
    }

    # In-memory turn stats keep sanitized fields only (still no bodies).
    session.turn_stats.append({"event": event, **safe})

    summary = _human_summary(event, safe)
    try:
        logger.info("%s", summary)
    except Exception:
        pass

    try:
        _append_jsonl(debug_log_path(), entry)
        if event in _DECISION_EVENTS:
            _append_jsonl(decisions_log_path(), entry)
    except Exception as exc:
        logger.debug("telemetry write failed: %s", exc)


def tool_names(tool_calls: Optional[Sequence[Dict[str, Any]]] = None,
               tool_results: Optional[Sequence[Dict[str, Any]]] = None) -> list[str]:
    """Extract tool names for logging (names only, never args/bodies)."""
    names: list[str] = []
    for seq in (tool_calls or (), tool_results or ()):
        for item in seq:
            if not isinstance(item, dict):
                continue
            n = str(item.get("name") or item.get("tool_name") or "")
            if n and n not in names:
                names.append(n)
    return names


def aggregate(session_id: str) -> Dict[str, Any]:
    s = STORE.get(session_id)
    return {
        "session_id": s.session_id,
        "main_model_calls_avoided": s.main_model_calls_avoided,
        "compaction_events": s.compaction_events,
        "duplicates_blocked": s.duplicates_blocked,
        "finishes": s.finishes,
        "mutation_epoch": s.mutation_epoch,
        "api_call_count": s.api_call_count,
        "events": len(s.turn_stats),
        "event_seq": s.event_seq,
        "log_dir": str(plugin_log_dir()),
    }

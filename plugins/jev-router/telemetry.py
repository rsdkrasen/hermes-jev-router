"""Per-turn stats + aggregates. No secrets / full outputs."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import get_config
from .state import STORE, SessionState

logger = logging.getLogger("hermes.plugins.jev_router.telemetry")


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def debug_log_path() -> Path:
    return _hermes_home() / "plugins" / "jev-router" / "telemetry.jsonl"


def record_event(session: SessionState, event: str, **fields: Any) -> None:
    cfg = get_config()
    if not cfg.telemetry_enabled:
        return
    entry = {
        "ts": time.time(),
        "session_id": session.session_id,
        "event": event,
        **{k: v for k, v in fields.items() if k not in {"result", "output", "content", "raw"}},
    }
    # Strip obvious secret-ish values
    for k, v in list(entry.items()):
        if isinstance(v, str) and any(s in k.lower() for s in ("key", "token", "secret", "password")):
            entry[k] = "***"
    session.turn_stats.append({"event": event, **fields})
    try:
        path = debug_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str)[:2000] + "\n")
    except Exception as exc:
        logger.debug("telemetry write failed: %s", exc)


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
    }

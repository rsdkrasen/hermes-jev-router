"""Per-session state for jev-router. Keep small — only material being judged."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


def _fingerprint(tool: str, args: Any, result: str = "") -> str:
    try:
        payload = json.dumps({"tool": tool, "args": _normalize_args(args), "r": result[:200]},
                            sort_keys=True, default=str)
    except Exception:
        payload = f"{tool}:{args!r}:{result[:80]}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _normalize_args(args: Any) -> Any:
    if not isinstance(args, dict):
        return args
    # Drop volatile keys that defeat duplicate detection.
    skip = {"timestamp", "request_id", "nonce", "datetime"}
    return {k: v for k, v in sorted(args.items()) if k not in skip}


@dataclass
class CallRecord:
    tool: str
    args_norm: Any
    result_fp: str
    mutation_epoch: int
    success: bool
    seq: int
    observational: bool
    ts: float = field(default_factory=time.time)


@dataclass
class SessionState:
    session_id: str
    user_goal: str = ""
    expects_explanation: bool = False
    goal_kind: str = "other"
    mutation_epoch: int = 0
    seq: int = 0
    history: Deque[CallRecord] = field(default_factory=lambda: deque(maxlen=32))
    last_tool_results: List[Dict[str, Any]] = field(default_factory=list)
    last_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    last_statuses: List[str] = field(default_factory=list)
    last_mutated: bool = False
    api_call_count: int = 0
    main_model_calls_avoided: int = 0
    compaction_events: int = 0
    duplicates_blocked: int = 0
    finishes: int = 0
    plan: Optional[Dict[str, Any]] = None
    turn_stats: List[Dict[str, Any]] = field(default_factory=list)

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def record_call(
        self,
        *,
        tool: str,
        args: Any,
        result: str,
        success: bool,
        observational: bool,
        mutating: bool,
        history_size: int = 32,
    ) -> CallRecord:
        if mutating:
            self.mutation_epoch += 1
        if self.history.maxlen != history_size:
            self.history = deque(self.history, maxlen=history_size)
        rec = CallRecord(
            tool=tool,
            args_norm=_normalize_args(args),
            result_fp=_fingerprint(tool, args, result),
            mutation_epoch=self.mutation_epoch,
            success=success,
            seq=self.next_seq(),
            observational=observational,
        )
        self.history.append(rec)
        return rec

    def recent_same_tool(self, tool: str, args: Any) -> Optional[CallRecord]:
        norm = _normalize_args(args)
        for rec in reversed(self.history):
            if rec.tool == tool and rec.args_norm == norm:
                return rec
        return None


class StateStore:
    """Thread-safe session state map."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        key = session_id or "default"
        with self._lock:
            if key not in self._sessions:
                self._sessions[key] = SessionState(session_id=key)
            return self._sessions[key]

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id or "default", None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


STORE = StateStore()

"""F1: transform_tool_result compaction — preserve original selected chunks; never rewrite."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import JevConfig, get_config
from .jev import judge_timed
from .schemas import CompactionJudgment, ChunkScore
from .state import STORE
from .telemetry import record_event

logger = logging.getLogger("hermes.plugins.jev_router.compactor")

_DIAGNOSTIC_RE = re.compile(
    r"(?i)\b("
    r"error|fatal|failed|failure|exception|traceback|assertion|warning|"
    r"segfault|permission denied|test failure|tests? failed|errno"
    r")\b"
)


def _is_mostly_json(text: str) -> bool:
    s = text.lstrip()
    if not s or s[0] not in "{[":
        return False
    try:
        json.loads(text)
        return True
    except Exception:
        # Partial / large JSON — still treat conservatively if it looks like JSON.
        return s[0] in "{[" and ("{" in s[:200] or "[" in s[:200])


def _chunk_text(text: str, size: int) -> List[str]:
    if size <= 0:
        return [text]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _deterministic_sample(text: str, budget: int) -> str:
    """Head + middle + tail sample for huge outputs before Jev."""
    if len(text) <= budget:
        return text
    third = max(budget // 3, 1)
    head = text[:third]
    mid_start = max((len(text) - third) // 2, 0)
    mid = text[mid_start : mid_start + third]
    tail = text[-third:]
    return (
        f"{head}\n\n...[jev-router sampled middle]...\n\n{mid}"
        f"\n\n...[jev-router sampled end]...\n\n{tail}"
    )


def _diagnostic_indices(chunks: Sequence[str]) -> List[int]:
    return [i for i, c in enumerate(chunks) if _DIAGNOSTIC_RE.search(c)]


def _header(original_len: int, kept: int, total: int) -> str:
    return (
        f"[jev-router compacted: kept {kept}/{total} original chunks "
        f"from {original_len} chars — content not rewritten]\n\n"
    )


def compact_tool_result(
    *,
    tool_name: str,
    args: Optional[Dict[str, Any]],
    result: Any,
    session_id: str = "",
    task_id: str = "",
    status: str = "",
    turn_id: str = "",
    **kwargs: Any,
) -> Optional[str]:
    """Return compacted string or None to leave result unchanged (fail-open)."""
    cfg = get_config()
    if not cfg.enabled:
        return None
    if not isinstance(result, str):
        return None
    if len(result) < cfg.compaction_min_chars:
        # Too short to consider — silent (would drown decision logs).
        return None

    tid = turn_id or str(kwargs.get("turn_id") or "")
    session = STORE.get(session_id)
    if tid:
        session.last_turn_id = tid
    try:
        out = _compact(
            cfg, tool_name=tool_name, args=args or {}, result=result,
            session_id=session_id, status=status, turn_id=tid,
        )
        if out is None:
            # Eligible size but left unchanged (e.g. small JSON, keep-all chunks).
            record_event(
                session, "compact_skip",
                tool=tool_name, reason="no_reduction",
                chars_in=len(result), chars_out=len(result),
                turn_id=tid,
            )
        return out
    except Exception as exc:
        logger.debug("compaction failed open: %s", exc)
        record_event(
            session, "fail_open",
            hook="transform_tool_result", reason="exception",
            tool=tool_name, error=type(exc).__name__,
            chars_in=len(result), turn_id=tid,
        )
        return None


def _compact(
    cfg: JevConfig,
    *,
    tool_name: str,
    args: Dict[str, Any],
    result: str,
    session_id: str,
    status: str,
    turn_id: str = "",
) -> Optional[str]:
    # Conservative with JSON: only light sampling if huge, never score-rewrite structure.
    if _is_mostly_json(result):
        if len(result) < cfg.compaction_huge_chars:
            return None
        sampled = _deterministic_sample(result, cfg.compaction_sample_chars)
        header = (
            f"[jev-router: large JSON sampled {len(sampled)}/{len(result)} chars — "
            f"structure preserved in sample]\n\n"
        )
        session = STORE.get(session_id)
        session.compaction_events += 1
        record_event(session, "compact_done", tool=tool_name, reason="json_sample",
                     chars_in=len(result), chars_out=len(sampled),
                     original=len(result), kept=len(sampled),
                     turn_id=turn_id)
        return header + sampled

    working = result
    if len(working) >= cfg.compaction_huge_chars:
        working = _deterministic_sample(working, cfg.compaction_sample_chars)

    chunks = _chunk_text(working, cfg.compaction_chunk_chars)
    if len(chunks) <= 1:
        return None

    # Cap chunks sent to Jev
    to_score = chunks[: cfg.compaction_max_chunks_to_jev]
    diag = set(_diagnostic_indices(to_score))

    session = STORE.get(session_id)
    state = {
        "tool_name": tool_name,
        "user_goal": session.user_goal[:300],
        "status": status,
        "n_chunks": len(to_score),
        "chunk_previews": [
            {"index": i, "preview": c[:240], "diagnostic": i in diag} for i, c in enumerate(to_score)
        ],
    }
    judgment, jev_ms = judge_timed(CompactionJudgment, state)
    if jev_ms is not None:
        session.last_jev_ms = jev_ms

    keep: set[int] = set(diag)
    if judgment and judgment.scores:
        ranked = sorted(judgment.scores, key=lambda s: s.relevance, reverse=True)
        for s in ranked:
            if s.keep or s.relevance >= 0.5:
                keep.add(int(s.chunk_index))
            if len(keep) >= cfg.compaction_keep_top_k + len(diag):
                break
    else:
        # Fail-open-ish but still reduce: keep first, last, and diagnostics.
        keep.update({0, len(to_score) - 1})
        # Evenly sample a few more
        step = max(len(to_score) // max(cfg.compaction_keep_top_k, 1), 1)
        keep.update(range(0, len(to_score), step))

    keep = {i for i in keep if 0 <= i < len(to_score)}
    if len(keep) >= len(to_score):
        return None

    ordered = sorted(keep)
    parts: List[str] = []
    prev = -1
    for idx in ordered:
        if prev >= 0 and idx > prev + 1:
            parts.append("\n...[chunk omitted]...\n")
        parts.append(to_score[idx])  # ORIGINAL chunk text — never rewritten
        prev = idx

    body = "".join(parts)
    session.compaction_events += 1
    ms_kw = {"jev_ms": jev_ms} if jev_ms is not None else {}
    record_event(
        session, "compact_done",
        tool=tool_name, reason="chunk_select",
        chars_in=len(result), chars_out=len(body),
        original=len(result), kept_chunks=len(ordered), total_chunks=len(to_score),
        turn_id=turn_id, **ms_kw,
    )
    return _header(len(result), len(ordered), len(to_score)) + body

"""Deterministic final-response renderer. Never invents — evidence only."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .schemas import RoundControlJudgment


_SUCCESS_MARKERS = (
    "passed",
    "ok",
    "success",
    "deleted",
    "removed",
    "created",
    "updated",
    "wrote",
    "done",
    "complete",
    "0 failures",
    "0 failed",
)


def can_fast_path_success(
    *,
    tool_results: Sequence[Dict[str, Any]],
    statuses: Sequence[str],
    expects_explanation: bool,
) -> bool:
    """Obvious success with no narrative expected → skip Jev + main model."""
    if expects_explanation:
        return False
    if not tool_results:
        return False
    if any(s == "error" for s in statuses):
        return False
    joined = " ".join(str(r.get("content") or "") for r in tool_results).lower()
    return any(m in joined for m in _SUCCESS_MARKERS)


def render_from_evidence(
    *,
    user_goal: str,
    tool_calls: Sequence[Dict[str, Any]],
    tool_results: Sequence[Dict[str, Any]],
    judgment: Optional[RoundControlJudgment] = None,
) -> Optional[str]:
    """Build a concise factual answer from evidence. Return None if cannot (→ continue to model)."""
    if judgment is not None:
        if not judgment.can_render_deterministically:
            return None
        bullets = [b.strip() for b in (judgment.evidence_bullets or []) if isinstance(b, str) and b.strip()]
        if bullets:
            lines = ["Done."]
            for b in bullets[:8]:
                lines.append(f"- {b}")
            return "\n".join(lines)

    # Fallback: stitch truncated tool result snippets — never invent.
    snippets: List[str] = []
    for r in tool_results:
        content = str(r.get("content") or "").strip()
        name = str(r.get("name") or "tool")
        if not content:
            continue
        # Prefer short results
        if len(content) > 400:
            content = content[:397] + "..."
        snippets.append(f"{name}: {content}")
    if not snippets:
        return None

    names = [str(c.get("name") or "") for c in tool_calls]
    header = f"Completed ({', '.join(n for n in names if n) or 'tools'})."
    body = "\n".join(f"- {s}" for s in snippets[:6])
    return f"{header}\n{body}"


def render_fast_path(tool_results: Sequence[Dict[str, Any]], tool_calls: Sequence[Dict[str, Any]]) -> str:
    rendered = render_from_evidence(
        user_goal="", tool_calls=tool_calls, tool_results=tool_results, judgment=None
    )
    return rendered or "Done."

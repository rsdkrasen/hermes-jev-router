"""Deterministic final-response renderer. Never invents — evidence only."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .schemas import RoundControlJudgment


# Phrase-level success evidence. Short tokens MUST use word boundaries so that
# "ok" does not match inside "Token" / "looking" / "hooks". That substring bug
# finished tool rounds early and broke multi-step tool calling when the core
# post_tool_round_control patch was applied.
_SUCCESS_PHRASES = (
    "0 failures",
    "0 failed",
    "all tests passed",
    "tests passed",
    "successfully deleted",
    "successfully removed",
    "successfully created",
    "successfully wrote",
    "successfully updated",
)

_SUCCESS_WORD_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])("
    r"passed|success|successful|deleted|removed|created|updated|wrote|done|"
    r"complete|completed|ok|okay"
    r")(?![A-Za-z0-9_])"
)

_FAILURE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])("
    r"error|failed|failure|traceback|exception|fatal|denied|incomplete|"
    r"not found|no such file"
    r")(?![A-Za-z0-9_])"
)


def _joined_content(tool_results: Sequence[Dict[str, Any]]) -> str:
    return " ".join(str(r.get("content") or "") for r in tool_results)


def _has_success_evidence(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in _SUCCESS_PHRASES):
        return True
    return bool(_SUCCESS_WORD_RE.search(text))


def _tool_names(
    tool_calls: Sequence[Dict[str, Any]], tool_results: Sequence[Dict[str, Any]]
) -> List[str]:
    names: List[str] = []
    for c in tool_calls:
        n = str(c.get("name") or c.get("tool_name") or "")
        if n:
            names.append(n)
    if not names:
        for r in tool_results:
            n = str(r.get("name") or r.get("tool_name") or "")
            if n:
                names.append(n)
    return names


def can_fast_path_success(
    *,
    tool_results: Sequence[Dict[str, Any]],
    statuses: Sequence[str],
    expects_explanation: bool,
    tool_calls: Sequence[Dict[str, Any]] = (),
    mutated: bool = False,
    observational_tools: Sequence[str] = (),
    mutating_tools: Sequence[str] = (),
) -> bool:
    """Obvious success with no narrative expected → skip Jev + main model.

    Prefer continuing to the main model over finishing early. Observational-only
    rounds never fast-path (reads/greps are mid-task evidence gathering).
    """
    if expects_explanation:
        return False
    if not tool_results:
        return False
    if any(str(s).lower() in {"error", "failed", "failure"} for s in statuses):
        return False

    joined = _joined_content(tool_results)
    if _FAILURE_RE.search(joined):
        return False
    if not _has_success_evidence(joined):
        return False

    names = _tool_names(tool_calls, tool_results)
    obs = set(observational_tools)
    mut = set(mutating_tools)

    # When taxonomy is available, never finish after observational-only rounds —
    # those are mid-task (reads/greps), not completed work.
    if names and (obs or mut):
        only_observational = all(n in obs for n in names) and not any(n in mut for n in names)
        if only_observational:
            return False
        if not mutated and not any(n in mut for n in names):
            return False

    return True


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

    snippets: List[str] = []
    for r in tool_results:
        content = str(r.get("content") or "").strip()
        name = str(r.get("name") or "tool")
        if not content:
            continue
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

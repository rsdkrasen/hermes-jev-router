"""Deterministic final-response renderer. Never invents — evidence only."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .schemas import RoundControlJudgment


# Strong verification phrases only. Bare words like "ok" / "done" / "success"
# are too common in tool JSON (e.g. lint: {"status": "ok"}) and must not
# trigger deterministic fast-path finish.
_SUCCESS_PHRASES = (
    "0 failures",
    "0 failed",
    "all tests passed",
    "tests passed",
)

# Clear test-count outcomes: "12 passed", "3 passed".
_SUCCESS_VERIFY_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])\d+\s+passed(?![A-Za-z0-9_])"
)

_FAILURE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])("
    r"error|failed|failure|traceback|exception|fatal|denied|incomplete|"
    r"not found|no such file"
    r")(?![A-Za-z0-9_])"
)

# File editors never fast-path — mid-task writes need the main model to continue.
_FILE_MUTATION_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "patch",
        "apply_patch",
        "delete_file",
        "move_file",
        "create_file",
    }
)

# Only terminal/bash/shell-style rounds may use deterministic fast-path.
_TERMINAL_VERIFY_TOOLS = frozenset(
    {
        "terminal",
        "bash",
        "shell",
        "run_terminal_cmd",
        "execute_code",
    }
)

_FILE_EDITOR_MARKERS = (
    "write_file",
    "edit_file",
    "apply_patch",
    "delete_file",
    "move_file",
    "create_file",
    "write",
    "edit",
    "patch",
)


def _joined_content(tool_results: Sequence[Dict[str, Any]]) -> str:
    return " ".join(str(r.get("content") or "") for r in tool_results)


def _has_success_evidence(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in _SUCCESS_PHRASES):
        return True
    return bool(_SUCCESS_VERIFY_RE.search(text))


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


def _is_file_mutation_tool(name: str, mutating_tools: Sequence[str]) -> bool:
    """True for write/edit/patch/delete/move/create style tools (not terminal)."""
    if name in _FILE_MUTATION_TOOLS:
        return True
    if name in _TERMINAL_VERIFY_TOOLS:
        return False
    mut = set(mutating_tools)
    if name not in mut:
        return False
    low = name.lower().replace("-", "_")
    return any(m in low for m in _FILE_EDITOR_MARKERS)


def _is_terminal_verify_tool(name: str) -> bool:
    return name in _TERMINAL_VERIFY_TOOLS or name.lower().replace("-", "_") in _TERMINAL_VERIFY_TOOLS


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
    """Obvious terminal verification success → skip Jev + main model.

    Prefer continuing to the main model over finishing early. File-mutation
    rounds and observational-only rounds never fast-path. Bare JSON success
    tokens (ok/done/created/…) are not enough — only strong test/verify phrases.
    """
    del mutated  # kept for call-site compatibility; taxonomy drives the gate
    if expects_explanation:
        return False
    if not tool_results:
        return False
    if any(str(s).lower() in {"error", "failed", "failure"} for s in statuses):
        return False

    joined = _joined_content(tool_results)
    # Strip strong success phrases before failure scan so "0 failed" is not a miss.
    scrubbed = joined
    low = joined.lower()
    for phrase in _SUCCESS_PHRASES:
        if phrase in low:
            # case-insensitive remove of each phrase occurrence
            scrubbed = re.sub(re.escape(phrase), " ", scrubbed, flags=re.IGNORECASE)
    scrubbed = _SUCCESS_VERIFY_RE.sub(" ", scrubbed)
    if _FAILURE_RE.search(scrubbed):
        return False

    names = _tool_names(tool_calls, tool_results)

    # Never finish after file writes/edits/patches — agent must continue.
    if any(_is_file_mutation_tool(n, mutating_tools) for n in names):
        return False

    obs = set(observational_tools)
    mut = set(mutating_tools)

    # Observational-only rounds are mid-task evidence gathering.
    if names and (obs or mut):
        only_observational = all(n in obs for n in names) and not any(n in mut for n in names)
        if only_observational:
            return False

    # Deterministic fast-path is reserved for terminal/bash/shell verification.
    if not names or not all(_is_terminal_verify_tool(n) for n in names):
        return False

    if not _has_success_evidence(joined):
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

"""Deterministic final-response renderer. Never invents — evidence only."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

# Harmless JSON error fields ("error": null / false / "") must not trip failure scans.
_NULLISH_ERROR_JSON_RE = re.compile(
    r"""(?ix)
    ["']?error["']?\s*:\s*
    (?:null|none|false|""|''|\[\s*\]|\{\s*\})
    """
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


def _scrub_for_failure_scan(text: str) -> str:
    """Remove strong success phrases and nullish JSON error keys before failure scan."""
    scrubbed = text
    low = text.lower()
    for phrase in _SUCCESS_PHRASES:
        if phrase in low:
            scrubbed = re.sub(re.escape(phrase), " ", scrubbed, flags=re.IGNORECASE)
    scrubbed = _SUCCESS_VERIFY_RE.sub(" ", scrubbed)
    scrubbed = _NULLISH_ERROR_JSON_RE.sub(" ", scrubbed)
    return scrubbed


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


def fast_path_decision(
    *,
    tool_results: Sequence[Dict[str, Any]],
    statuses: Sequence[str],
    expects_explanation: bool,
    tool_calls: Sequence[Dict[str, Any]] = (),
    mutated: bool = False,
    observational_tools: Sequence[str] = (),
    mutating_tools: Sequence[str] = (),
) -> Tuple[bool, str]:
    """Return ``(may_finish, reason)`` for deterministic fast-path.

    Prefer continuing to the main model over finishing early. File-mutation
    rounds and observational-only rounds never fast-path. Bare JSON success
    tokens (ok/done/created/…) are not enough — only strong test/verify phrases.

    Reason strings are stable for telemetry analysis (see LOGGING.md).
    """
    del mutated  # kept for call-site compatibility; taxonomy drives the gate
    if expects_explanation:
        return False, "expects_explanation"
    if not tool_results:
        return False, "no_results"

    joined = _joined_content(tool_results)
    # Strip success phrases + nullish "error" JSON before failure scan so
    # "0 failed" / {"error": null} do not look like real failures.
    scrubbed = _scrub_for_failure_scan(joined)
    scrubbed_has_failure = bool(_FAILURE_RE.search(scrubbed))
    has_verify_success = _has_success_evidence(joined)
    status_looks_failed = any(
        str(s).lower() in {"error", "failed", "failure"} for s in statuses
    )

    # Core may mark status=error on harmless JSON ("error": null). If content has
    # strong verify success and the scrubbed failure scan is clean, keep evaluating
    # fast-path instead of returning status_failure (so terminal verify can finish).
    if status_looks_failed:
        if has_verify_success and not scrubbed_has_failure:
            pass  # status_failure overridden by verify success — continue gates
        else:
            return False, "status_failure"

    if scrubbed_has_failure:
        return False, "failure_in_content"

    names = _tool_names(tool_calls, tool_results)

    # Never finish after file writes/edits/patches — agent must continue.
    if any(_is_file_mutation_tool(n, mutating_tools) for n in names):
        return False, "file_mutation_no_fast_path"

    obs = set(observational_tools)
    mut = set(mutating_tools)

    # Observational-only rounds are mid-task evidence gathering.
    if names and (obs or mut):
        only_observational = all(n in obs for n in names) and not any(n in mut for n in names)
        if only_observational:
            return False, "observational_only"

    # Deterministic fast-path is reserved for terminal/bash/shell verification.
    if not names or not all(_is_terminal_verify_tool(n) for n in names):
        return False, "not_terminal_verify_tools"

    if not _has_success_evidence(joined):
        return False, "no_verify_phrase"

    return True, "fast_path_terminal_verify"


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
    """Obvious terminal verification success → skip Jev + main model."""
    ok, _reason = fast_path_decision(
        tool_results=tool_results,
        statuses=statuses,
        expects_explanation=expects_explanation,
        tool_calls=tool_calls,
        mutated=mutated,
        observational_tools=observational_tools,
        mutating_tools=mutating_tools,
    )
    return ok


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

"""Secret redaction before any payload is sent to TypeSafe/Jev."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, MutableMapping, Union

# Conservative patterns — prefer over-redacting.
_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]\s*['\"]?([^\s'\"]{8,})", re.M),
     r"\1=***REDACTED***"),
    (re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*", re.M), "Bearer ***REDACTED***"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***REDACTED***"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***REDACTED***"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "xox-***REDACTED***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "***REDACTED PRIVATE KEY***"),
    (re.compile(r"(?i)typesafe[_-]?api[_-]?key\s*[=:]\s*\S+"), "TYPESAFE_API_KEY=***REDACTED***"),
]


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_obj(value: Any) -> Any:
    """Deep-redact strings inside nested dict/list structures (fail-open)."""
    try:
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, Mapping):
            return {k: redact_obj(v) for k, v in value.items()}
        if isinstance(value, list):
            return [redact_obj(v) for v in value]
        if isinstance(value, tuple):
            return tuple(redact_obj(v) for v in value)
        return value
    except Exception:
        return value


def redact_state(state: MutableMapping[str, Any] | Dict[str, Any]) -> Dict[str, Any]:
    """Return a redacted shallow copy suitable for Jev."""
    try:
        return redact_obj(dict(state))  # type: ignore[return-value]
    except Exception:
        return {}

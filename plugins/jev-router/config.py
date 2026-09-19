"""Jev-router configuration from environment variables and optional plugin settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet, Tuple


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_csv(name: str, default: Tuple[str, ...]) -> FrozenSet[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return frozenset(default)
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


_DEFAULT_MUTATORS = (
    "write_file",
    "patch",
    "delete_file",
    "move_file",
    "terminal",
    "execute_code",
    "run_terminal_cmd",
    "bash",
    "shell",
    "edit_file",
    "apply_patch",
)

_DEFAULT_OBSERVERS = (
    "read_file",
    "search_files",
    "grep",
    "glob",
    "list_dir",
    "web_search",
    "browser_snapshot",
    "session_search",
    "memory",
    "todo_list",
)


@dataclass(frozen=True)
class JevConfig:
    """All knobs for jev-router. Env vars take precedence; defaults are aggressive-skip."""

    enabled: bool = True
    model: str = "typesafe:jev-1.13.0"
    api_key_env: str = "TYPESAFE_API_KEY"

    # Compaction (F1)
    compaction_min_chars: int = 12_000
    compaction_chunk_chars: int = 2_000
    compaction_max_chunks_to_jev: int = 24
    compaction_keep_top_k: int = 8
    compaction_huge_chars: int = 80_000
    compaction_sample_chars: int = 24_000

    # Duplicate suppression (F2)
    dup_redundancy_min: float = 0.95
    dup_relevance_max: float = 0.40
    dup_history_size: int = 32

    # Round-control skip thresholds (F3) — aggressive
    goal_satisfied_min: float = 0.90
    evidence_sufficient_min: float = 0.85
    contains_failure_max: float = 0.20
    another_tool_needed_max: float = 0.25
    requires_main_model_max: float = 0.35

    # Autonomous plan (F5) — default OFF
    autonomous_plan_enabled: bool = False

    # Preflight (F6)
    preflight_classify: bool = True

    # Telemetry (F7)
    telemetry_enabled: bool = True

    # Tool classification
    mutating_tools: FrozenSet[str] = field(default_factory=lambda: frozenset(_DEFAULT_MUTATORS))
    observational_tools: FrozenSet[str] = field(default_factory=lambda: frozenset(_DEFAULT_OBSERVERS))

    # Truncation for hook payloads / Jev state
    result_preview_chars: int = 800
    args_preview_chars: int = 300

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") or ""

    @property
    def typesafe_ready(self) -> bool:
        return bool(self.api_key) and self.enabled


def load_config() -> JevConfig:
    """Load config from JEV_* / TYPESAFE_* environment variables."""
    model = _env_str("JEV_MODEL", "typesafe:jev-1.13.0")
    # Allow bare "jev-latest" / "jev-1.13.0"
    if model and not model.startswith("typesafe:"):
        model = f"typesafe:{model}"

    return JevConfig(
        enabled=_env_bool("JEV_ENABLED", True),
        model=model,
        compaction_min_chars=_env_int("JEV_COMPACTION_MIN_CHARS", 12_000),
        compaction_chunk_chars=_env_int("JEV_COMPACTION_CHUNK_CHARS", 2_000),
        compaction_max_chunks_to_jev=_env_int("JEV_COMPACTION_MAX_CHUNKS", 24),
        compaction_keep_top_k=_env_int("JEV_COMPACTION_KEEP_TOP_K", 8),
        compaction_huge_chars=_env_int("JEV_COMPACTION_HUGE_CHARS", 80_000),
        compaction_sample_chars=_env_int("JEV_COMPACTION_SAMPLE_CHARS", 24_000),
        dup_redundancy_min=_env_float("JEV_DUP_REDUNDANCY_MIN", 0.95),
        dup_relevance_max=_env_float("JEV_DUP_RELEVANCE_MAX", 0.40),
        dup_history_size=_env_int("JEV_DUP_HISTORY_SIZE", 32),
        goal_satisfied_min=_env_float("JEV_GOAL_SATISFIED_MIN", 0.90),
        evidence_sufficient_min=_env_float("JEV_EVIDENCE_SUFFICIENT_MIN", 0.85),
        contains_failure_max=_env_float("JEV_CONTAINS_FAILURE_MAX", 0.20),
        another_tool_needed_max=_env_float("JEV_ANOTHER_TOOL_NEEDED_MAX", 0.25),
        requires_main_model_max=_env_float("JEV_REQUIRES_MAIN_MODEL_MAX", 0.35),
        autonomous_plan_enabled=_env_bool("JEV_AUTONOMOUS_PLAN_ENABLED", False),
        preflight_classify=_env_bool("JEV_PREFLIGHT_CLASSIFY", True),
        telemetry_enabled=_env_bool("JEV_TELEMETRY_ENABLED", True),
        mutating_tools=_env_csv("JEV_MUTATING_TOOLS", _DEFAULT_MUTATORS),
        observational_tools=_env_csv("JEV_OBSERVATIONAL_TOOLS", _DEFAULT_OBSERVERS),
        result_preview_chars=_env_int("JEV_RESULT_PREVIEW_CHARS", 800),
        args_preview_chars=_env_int("JEV_ARGS_PREVIEW_CHARS", 300),
    )


# Process-wide cached config; refresh via reload_config() in tests.
_CONFIG: JevConfig | None = None


def get_config() -> JevConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def reload_config() -> JevConfig:
    global _CONFIG
    _CONFIG = load_config()
    return _CONFIG

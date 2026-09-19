"""Pytest fixtures: load hyphenated plugin dir as package ``jev_router``, reset state."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "jev-router"


def _ensure_package() -> None:
    """Map plugins/jev-router/ onto import name jev_router (Hermes loads by directory)."""
    if "jev_router" in sys.modules and hasattr(sys.modules["jev_router"], "register"):
        return
    pkg = types.ModuleType("jev_router")
    pkg.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    pkg.__file__ = str(PLUGIN / "__init__.py")
    sys.modules["jev_router"] = pkg

    # Preload submodules so relative imports in __init__ resolve
    for mod_name in (
        "config", "schemas", "redact", "state", "jev", "telemetry",
        "renderer", "compactor", "policy", "plan",
    ):
        full = f"jev_router.{mod_name}"
        spec = importlib.util.spec_from_file_location(full, PLUGIN / f"{mod_name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "jev_router"
        sys.modules[full] = mod
        spec.loader.exec_module(mod)

    # Load package __init__
    spec = importlib.util.spec_from_file_location("jev_router", PLUGIN / "__init__.py",
                                                 submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    init_mod = importlib.util.module_from_spec(spec)
    init_mod.__package__ = "jev_router"
    init_mod.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["jev_router"] = init_mod
    spec.loader.exec_module(init_mod)

    # Also expose top-level aliases used by older tests (import state / import policy)
    for mod_name in (
        "config", "schemas", "redact", "state", "jev", "telemetry",
        "renderer", "compactor", "policy", "plan",
    ):
        sys.modules[mod_name] = sys.modules[f"jev_router.{mod_name}"]


_ensure_package()


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from jev_router import state as state_mod
    from jev_router import config as config_mod
    from jev_router import jev as jev_mod

    state_mod.STORE.clear()
    jev_mod.set_judge_override(None)
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-used")
    monkeypatch.delenv("JEV_AUTONOMOUS_PLAN_ENABLED", raising=False)
    config_mod.reload_config()
    yield
    jev_mod.set_judge_override(None)
    state_mod.STORE.clear()

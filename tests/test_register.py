"""Smoke-test register() against a fake PluginContext."""

from __future__ import annotations

from typing import Any, Callable, Dict, List


class FakeCtx:
    def __init__(self):
        self.hooks: Dict[str, List[Callable]] = {}
        self.tools: Dict[str, Any] = {}

    def register_hook(self, name: str, cb: Callable) -> None:
        self.hooks.setdefault(name, []).append(cb)

    def register_tool(self, name: str, toolset: str = "", schema: dict = None, handler=None, **kw) -> None:
        self.tools[name] = {"schema": schema, "handler": handler, "toolset": toolset}


def test_register_hooks():
    import jev_router as plugin
    from config import reload_config
    reload_config()
    ctx = FakeCtx()
    plugin.register(ctx)
    for h in (
        "pre_llm_call", "pre_tool_call", "transform_tool_result",
        "post_tool_call", "post_tool_round_control", "on_session_end",
    ):
        assert h in ctx.hooks
    assert "jev_execution_plan" in ctx.tools

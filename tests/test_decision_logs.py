"""QA: every continue/finish path writes analyzable decision log lines."""

from __future__ import annotations

import json
from pathlib import Path

from config import reload_config
from jev import set_judge_override
from policy import should_finish_round
from renderer import fast_path_decision
from schemas import RoundControlJudgment
from state import STORE
from telemetry import debug_log_path, decisions_log_path, plugin_log_dir, record_event


OBS = ("read_file", "search_files", "grep", "glob", "list_dir")
MUT = ("write_file", "terminal", "bash", "shell", "edit_file", "patch", "delete_file")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


def _success_judgment(**over):
    base = dict(
        goal_satisfied=0.95,
        evidence_sufficient=0.9,
        contains_failure=0.05,
        another_tool_needed=0.1,
        requires_main_model=0.2,
        outcome="success",
        expects_explanation=False,
        evidence_bullets=["tests passed: 12 ok"],
        can_render_deterministically=True,
    )
    base.update(over)
    return RoundControlJudgment(**base)


def test_log_path_prefers_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert plugin_log_dir() == tmp_path / "plugins" / "jev-router"
    assert debug_log_path() == tmp_path / "plugins" / "jev-router" / "telemetry.jsonl"


def test_log_path_falls_back_to_localappdata(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert plugin_log_dir() == tmp_path / "AppData" / "Local" / "hermes" / "plugins" / "jev-router"


def test_record_event_writes_parseable_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    s = STORE.get("log1")
    s.user_goal = "Ship the router\nwith care"
    s.mutation_epoch = 2
    s.main_model_calls_avoided = 4
    record_event(
        s, "round_continue",
        action="continue", reason="file_mutation_no_fast_path",
        tools=["write_file"], mutated=True, api_call_count=2,
        turn_id="turn-42",
        # Must be stripped — never land in the file
        content="SECRET body api_key=sk-abc",
        result="should not appear",
        api_key="sk-leak",
    )
    entries = _read_jsonl(debug_log_path())
    assert entries
    last = entries[-1]
    assert last["event"] == "round_continue"
    assert last["reason"] == "file_mutation_no_fast_path"
    assert last["tools"] == ["write_file"]
    assert "content" not in last
    assert "result" not in last
    assert last.get("api_key") == "***"
    assert last["seq"] == 1
    assert last["turn_id"] == "turn-42"
    assert last["goal_preview"] == "Ship the router with care"
    assert last["mutation_epoch"] == 2
    assert last["main_model_calls_avoided"] == 4
    assert "jev_ms" not in last  # no judge ran
    # decisions.jsonl mirror
    dec = _read_jsonl(decisions_log_path())
    assert any(e["event"] == "round_continue" for e in dec)


def test_write_file_lint_ok_continues_with_logged_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda *a, **k: None)
    out = should_finish_round(
        session_id="dl_write",
        user_goal="edit the router and continue",
        tool_calls=[{"name": "write_file", "args": {"path": "/a"}}],
        tool_results=[{
            "name": "write_file",
            "content": 'bytes_written: 0\nverified: true\nlint: {"status": "ok"}',
            "status": "ok",
        }],
        statuses=["ok"],
        mutated=True,
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"
    events = _read_jsonl(debug_log_path())
    cont = [e for e in events if e.get("event") == "round_continue"]
    assert cont, f"expected round_continue, got {events}"
    assert cont[-1]["reason"] == "file_mutation_no_fast_path"
    assert "write_file" in cont[-1].get("tools", [])
    assert cont[-1].get("mutated") is True
    assert "seq" in cont[-1]
    assert "mutation_epoch" in cont[-1]
    assert "main_model_calls_avoided" in cont[-1]
    assert "goal_preview" in cont[-1]


def test_read_file_token_looking_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda *a, **k: None)
    out = should_finish_round(
        session_id="dl_read",
        user_goal="inspect then edit",
        tool_calls=[{"name": "read_file"}],
        tool_results=[{"name": "read_file", "content": "Token budget\nlooking good", "status": "ok"}],
        statuses=["ok"],
        mutated=False,
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"
    events = _read_jsonl(debug_log_path())
    cont = [e for e in events if e.get("event") == "round_continue"]
    assert cont
    assert cont[-1]["reason"] == "observational_only"


def test_terminal_12_passed_finishes_fast_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda *a, **k: None)
    session = STORE.get("dl_term")
    session.expects_explanation = False
    out = should_finish_round(
        session_id="dl_term",
        user_goal="run tests",
        tool_calls=[{"name": "terminal"}],
        tool_results=[{"name": "terminal", "content": "12 passed", "status": "ok"}],
        statuses=["ok"],
        mutated=False,
        api_call_count=1,
    )
    assert out is not None
    assert out["action"] == "finish"
    events = _read_jsonl(debug_log_path())
    fin = [e for e in events if e.get("event") == "round_finish"]
    assert fin
    assert fin[-1]["reason"] == "fast_path_terminal_verify"


def test_fast_path_decision_reasons():
    ok, reason = fast_path_decision(
        tool_results=[{"name": "write_file", "content": 'lint: {"status": "ok"}'}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "write_file"}],
        mutating_tools=MUT,
        observational_tools=OBS,
        mutated=True,
    )
    assert not ok and reason == "file_mutation_no_fast_path"

    ok, reason = fast_path_decision(
        tool_results=[{"name": "read_file", "content": "Token looking"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "read_file"}],
        mutating_tools=MUT,
        observational_tools=OBS,
    )
    assert not ok and reason == "observational_only"

    ok, reason = fast_path_decision(
        tool_results=[{"name": "terminal", "content": "12 passed"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "terminal"}],
        mutating_tools=MUT,
        observational_tools=OBS,
    )
    assert ok and reason == "fast_path_terminal_verify"

    ok, reason = fast_path_decision(
        tool_results=[{"name": "terminal", "content": "deleted successfully"}],
        statuses=["ok"],
        expects_explanation=False,
        tool_calls=[{"name": "terminal"}],
        mutating_tools=MUT,
        observational_tools=OBS,
    )
    assert not ok and reason == "no_verify_phrase"


def test_jev_finish_logs_judgment_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    # Disable fast path via expects_explanation, let Jev finish.
    set_judge_override(lambda m, t, s: _success_judgment())
    session = STORE.get("dl_jev")
    session.expects_explanation = True
    out = should_finish_round(
        session_id="dl_jev",
        user_goal="summarize test results briefly",
        tool_calls=[{"name": "terminal"}],
        tool_results=[{"name": "terminal", "content": "12 passed", "status": "ok"}],
        statuses=["ok"],
        mutated=False,
        api_call_count=2,
        turn_id="t-jev-1",
    )
    # expects_explanation blocks fast path; Jev may still finish if thresholds met
    # and can_render — but expects_explanation on session doesn't automatically
    # change judgment. thresholds can still finish.
    assert out is not None
    assert out["action"] == "finish"
    events = _read_jsonl(debug_log_path())
    fin = [e for e in events if e.get("event") == "round_finish"]
    assert fin and fin[-1]["reason"] == "jev_judgment"
    assert "goal_satisfied" in fin[-1]
    assert fin[-1]["turn_id"] == "t-jev-1"
    assert isinstance(fin[-1].get("jev_ms"), (int, float))
    assert fin[-1]["jev_ms"] >= 0
    assert fin[-1]["goal_preview"].startswith("summarize test results")
    assert "seq" in fin[-1]


def test_thresholds_not_met_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda m, t, s: _success_judgment(
        requires_main_model=0.99, can_render_deterministically=False, evidence_bullets=[],
    ))
    out = should_finish_round(
        session_id="dl_thr",
        user_goal="explain architecture",
        tool_calls=[{"name": "read_file"}],
        tool_results=[{"name": "read_file", "content": "class Foo", "status": "ok"}],
        statuses=["ok"],
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"
    events = _read_jsonl(debug_log_path())
    cont = [e for e in events if e.get("event") == "round_continue"]
    assert cont and cont[-1]["reason"] == "thresholds_not_met"


def test_seq_monotonic_across_events(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    s = STORE.get("seq1")
    s.user_goal = "one two three"
    record_event(s, "preflight", goal_kind="other")
    record_event(s, "round_continue", action="continue", reason="observational_only")
    record_event(s, "round_finish", action="finish", reason="jev_judgment")
    events = _read_jsonl(debug_log_path())
    seqs = [e["seq"] for e in events if e.get("session_id") == "seq1"]
    assert seqs == [1, 2, 3]


def test_goal_preview_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    s = STORE.get("gp1")
    s.user_goal = "deploy with api_key=sk-abcdefghijklmnopqrstuvwxyz123456"
    record_event(s, "preflight", goal_kind="other")
    last = _read_jsonl(debug_log_path())[-1]
    preview = last["goal_preview"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in preview
    assert "REDACTED" in preview or preview == "***" or "sk-***" in preview


def test_fast_path_finish_omits_jev_ms(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda *a, **k: None)
    session = STORE.get("dl_term_ms")
    session.expects_explanation = False
    out = should_finish_round(
        session_id="dl_term_ms",
        user_goal="run tests",
        tool_calls=[{"name": "terminal"}],
        tool_results=[{"name": "terminal", "content": "12 passed", "status": "ok"}],
        statuses=["ok"],
        mutated=False,
        api_call_count=1,
        turn_id="t-fp",
    )
    assert out and out["action"] == "finish"
    fin = [e for e in _read_jsonl(debug_log_path()) if e.get("event") == "round_finish"]
    assert fin and fin[-1]["reason"] == "fast_path_terminal_verify"
    assert "jev_ms" not in fin[-1]
    assert fin[-1]["turn_id"] == "t-fp"


def test_terminal_null_error_json_finishes_and_logs_statuses(tmp_path, monkeypatch):
    """12 passed + \"error\": null must finish even when statuses look failed."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda *a, **k: None)
    session = STORE.get("dl_null_err")
    session.expects_explanation = False
    content = '12 passed\nall tests passed\n{"error": null}'
    out = should_finish_round(
        session_id="dl_null_err",
        user_goal="run tests",
        tool_calls=[{"name": "terminal"}],
        tool_results=[{"name": "terminal", "content": content, "status": "error"}],
        statuses=["error"],
        mutated=False,
        api_call_count=1,
        turn_id="t-null",
    )
    assert out is not None and out["action"] == "finish"
    fin = [e for e in _read_jsonl(debug_log_path()) if e.get("event") == "round_finish"]
    assert fin
    assert fin[-1]["reason"] == "fast_path_terminal_verify"
    assert fin[-1]["statuses"] == ["error"]  # logged as received; override still finished
    assert fin[-1]["tools"] == ["terminal"]


def test_write_file_with_null_error_still_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reload_config()
    set_judge_override(lambda *a, **k: None)
    out = should_finish_round(
        session_id="dl_write_null",
        user_goal="edit then verify",
        tool_calls=[{"name": "write_file"}],
        tool_results=[{
            "name": "write_file",
            "content": 'bytes_written: 10\n{"error": null}\nlint: {"status": "ok"}',
            "status": "ok",
        }],
        statuses=["ok"],
        mutated=True,
        api_call_count=1,
    )
    assert out is None or out.get("action") == "continue"
    cont = [e for e in _read_jsonl(debug_log_path()) if e.get("event") == "round_continue"]
    assert cont and cont[-1]["reason"] == "file_mutation_no_fast_path"
    assert cont[-1]["statuses"] == ["ok"]

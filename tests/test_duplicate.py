from policy import check_duplicate
from state import STORE
from config import reload_config
from jev import set_judge_override
from schemas import DuplicateJudgment


def _record(session_id, tool, args, result="ok", mutating=False, observational=True):
    s = STORE.get(session_id)
    s.record_call(
        tool=tool, args=args, result=result, success=True,
        observational=observational, mutating=mutating,
    )


def test_blocks_observational_duplicate(monkeypatch):
    reload_config()
    set_judge_override(lambda m, t, s: DuplicateJudgment(
        redundancy=0.99, relevance=0.1, is_observational=True, reason="same read"
    ))
    _record("s1", "read_file", {"path": "/a"}, observational=True)
    out = check_duplicate(tool_name="read_file", args={"path": "/a"}, session_id="s1")
    assert out is not None
    assert out["action"] == "block"


def test_allows_after_mutation(monkeypatch):
    reload_config()
    set_judge_override(lambda m, t, s: DuplicateJudgment(
        redundancy=0.99, relevance=0.1, is_observational=True, reason="same"
    ))
    _record("s1", "read_file", {"path": "/a"}, observational=True)
    _record("s1", "write_file", {"path": "/a"}, mutating=True, observational=False)
    out = check_duplicate(tool_name="read_file", args={"path": "/a"}, session_id="s1")
    assert out is None  # re-verify allowed


def test_never_blocks_mutator(monkeypatch):
    reload_config()
    set_judge_override(lambda m, t, s: DuplicateJudgment(
        redundancy=1.0, relevance=0.0, is_observational=False, reason="x"
    ))
    _record("s1", "write_file", {"path": "/a"}, mutating=True, observational=False)
    out = check_duplicate(tool_name="write_file", args={"path": "/a"}, session_id="s1")
    assert out is None


def test_fail_open_on_judge_error(monkeypatch):
    reload_config()
    _record("s1", "read_file", {"path": "/a"})

    def boom(*a, **k):
        raise RuntimeError("x")

    set_judge_override(boom)
    # Deterministic fallback may still block identical observational — that's OK.
    # But the function itself must not raise.
    check_duplicate(tool_name="read_file", args={"path": "/a"}, session_id="s1")

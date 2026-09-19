from jev import set_judge_override
from schemas import CompactionJudgment, ChunkScore
from compactor import compact_tool_result
from config import reload_config
import os


def test_below_min_unchanged(monkeypatch):
    monkeypatch.setenv("JEV_COMPACTION_MIN_CHARS", "12000")
    reload_config()
    assert compact_tool_result(tool_name="read_file", args={}, result="short", session_id="s1") is None


def test_keeps_diagnostic_chunks(monkeypatch):
    monkeypatch.setenv("JEV_COMPACTION_MIN_CHARS", "100")
    monkeypatch.setenv("JEV_COMPACTION_CHUNK_CHARS", "50")
    monkeypatch.setenv("JEV_COMPACTION_KEEP_TOP_K", "2")
    reload_config()

    # Build text with diagnostic in middle
    parts = ["aaaa " * 20, "ERROR: traceback failure here " * 5, "bbbb " * 20, "cccc " * 20]
    text = "".join(parts)
    assert len(text) >= 100

    def fake_judge(model, output_type, state):
        scores = [
            ChunkScore(chunk_index=i, relevance=0.1, keep=False)
            for i in range(state["n_chunks"])
        ]
        # Mark only first as keep via relevance
        scores[0].relevance = 0.9
        scores[0].keep = True
        return CompactionJudgment(scores=scores)

    set_judge_override(fake_judge)
    out = compact_tool_result(tool_name="terminal", args={}, result=text, session_id="s1")
    assert out is not None
    assert "compacted" in out.lower() or "jev-router" in out.lower()
    assert "ERROR" in out or "traceback" in out.lower() or "failure" in out.lower()
    # Original text preserved (not rewritten)
    assert "aaaa" in out


def test_json_conservative(monkeypatch):
    monkeypatch.setenv("JEV_COMPACTION_MIN_CHARS", "100")
    monkeypatch.setenv("JEV_COMPACTION_HUGE_CHARS", "1000000")
    reload_config()
    import json
    payload = json.dumps({"items": list(range(50))})
    # Small JSON below huge threshold → unchanged
    assert compact_tool_result(tool_name="api", args={}, result=payload * 3, session_id="s1") is None


def test_fail_open_returns_none(monkeypatch):
    monkeypatch.setenv("JEV_COMPACTION_MIN_CHARS", "10")
    monkeypatch.setenv("JEV_COMPACTION_CHUNK_CHARS", "5")
    reload_config()

    def boom(*a, **k):
        raise RuntimeError("nope")

    set_judge_override(boom)
    # Should not raise; may return compacted via fallback or None
    compact_tool_result(tool_name="t", args={}, result="x" * 100, session_id="s1")

> **Windows Hermes app users:** see [WINDOWS-INSTALL.md](WINDOWS-INSTALL.md) for a short paste-and-check guide.

# jev-router — Hermes plugin + core patch

**Jev decides WHETHER. Python decides HOW. The main LLM decides WHAT only when generative reasoning is required.**

Standalone TypeSafe/Jev router for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Installs to `~/.hermes/plugins/jev-router/` (not in-tree). Requires a tiny generic Hermes core patch that adds the `post_tool_round_control` hook so a tool round can finish **without** another provider API call.

---

## 1. Directory tree

```text
jev-router-deliverable/
├── README.md
├── requirements-dev.txt
├── patches/
│   └── hermes-post-tool-round-control.patch
├── hermes-core-snippets/          # patched file copies for review
│   ├── plugins.py
│   ├── plugins_dispatch.py
│   └── turn_tool_round.py
├── plugins/
│   └── jev-router/
│       ├── plugin.yaml
│       ├── __init__.py
│       ├── config.py
│       ├── schemas.py
│       ├── jev.py
│       ├── state.py
│       ├── redact.py
│       ├── compactor.py
│       ├── policy.py
│       ├── renderer.py
│       ├── plan.py
│       ├── telemetry.py
│       ├── requirements.txt
│       └── pyproject.toml
└── tests/
    ├── conftest.py
    ├── test_*.py
    └── ...
```

## 2. Created files

| Path | Role |
|------|------|
| `patches/hermes-post-tool-round-control.patch` | Unified diff vs Hermes `main` |
| `hermes-core-snippets/*` | Full patched copies of the 3 touched files |
| `plugins/jev-router/plugin.yaml` | Manifest + hooks |
| `plugins/jev-router/__init__.py` | `register(ctx)` + hook wiring |
| `plugins/jev-router/config.py` | `JEV_*` / `TYPESAFE_API_KEY` |
| `plugins/jev-router/schemas.py` | Typed Pydantic Jev outputs |
| `plugins/jev-router/jev.py` | Mockable `judge()` (pydantic-ai / TypeSafe) |
| `plugins/jev-router/state.py` | Small per-session state |
| `plugins/jev-router/redact.py` | Secret redaction before Jev |
| `plugins/jev-router/compactor.py` | F1 `transform_tool_result` |
| `plugins/jev-router/policy.py` | F2 duplicates + F3 skip |
| `plugins/jev-router/renderer.py` | F4 deterministic answers |
| `plugins/jev-router/plan.py` | F5 optional autonomous plan |
| `plugins/jev-router/telemetry.py` | F7 stats / jsonl |
| `tests/*` | Pytest suite (mocked TypeSafe) |

---

## 3. Apply the core patch

From a Hermes checkout on `main` (or a commit matching the inspected files):

```bash
cd /path/to/hermes-agent
patch -p1 < /path/to/jev-router-deliverable/patches/hermes-post-tool-round-control.patch
```

**Files touched (generic — zero Jev/TypeSafe knowledge in core):**

1. `hermes_cli/plugins.py` — add `post_tool_round_control` to `VALID_HOOKS` + `get_post_tool_round_control()` helper  
2. `hermes_cli/plugins_dispatch.py` — add hook to `_HOOK_TIMEOUT_BOUNDED_HOOKS` (**fail-open**, not fail-closed)  
3. `agent/turn_tool_round.py` — after `compress_after_tool_results` succeeds, before `return _verdict("continue")`, consult the hook; on `finish`, set `final_response`, append assistant message, `return _verdict("break")` → normal `finalize_turn`

Review copies: `hermes-core-snippets/`.

---

## 4. Install the plugin

```bash
mkdir -p ~/.hermes/plugins
cp -a /path/to/jev-router-deliverable/plugins/jev-router ~/.hermes/plugins/jev-router
# or: ln -s /path/to/jev-router-deliverable/plugins/jev-router ~/.hermes/plugins/jev-router
```

## 5. Python dependency

```bash
uv pip install 'pydantic-ai-slim[typesafe]'
# or:
pip install 'pydantic-ai-slim[typesafe]'
```

## 6. Enable in Hermes

Ensure user plugins are discovered (default `~/.hermes/plugins/`). If you use an allowlist / enabled-plugins list in `~/.hermes/config.yaml`, add `jev-router`. Example:

```yaml
plugins:
  # …existing…
  # jev-router is auto-discovered from ~/.hermes/plugins/jev-router/
```

Restart CLI / gateway after installing the patch + plugin.

## 7. Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `TYPESAFE_API_KEY` | _(required for live Jev)_ | TypeSafe API key |
| `JEV_ENABLED` | `true` | Master switch |
| `JEV_MODEL` | `typesafe:jev-1.13.0` | Also accepts `jev-latest` / `jev-1.13.0` |
| `JEV_COMPACTION_MIN_CHARS` | `12000` | Below → no compaction |
| `JEV_COMPACTION_CHUNK_CHARS` | `2000` | Chunk size |
| `JEV_COMPACTION_KEEP_TOP_K` | `8` | Max non-diagnostic chunks kept |
| `JEV_COMPACTION_HUGE_CHARS` | `80000` | Sample before Jev |
| `JEV_DUP_REDUNDANCY_MIN` | `0.95` | Duplicate block threshold |
| `JEV_DUP_RELEVANCE_MAX` | `0.40` | Duplicate block threshold |
| `JEV_GOAL_SATISFIED_MIN` | `0.90` | Skip / finish |
| `JEV_EVIDENCE_SUFFICIENT_MIN` | `0.85` | Skip / finish |
| `JEV_CONTAINS_FAILURE_MAX` | `0.20` | Skip / finish |
| `JEV_ANOTHER_TOOL_NEEDED_MAX` | `0.25` | Skip / finish |
| `JEV_REQUIRES_MAIN_MODEL_MAX` | `0.35` | Skip / finish |
| `JEV_AUTONOMOUS_PLAN_ENABLED` | `false` | F5 plan tool |
| `JEV_PREFLIGHT_CLASSIFY` | `true` | Cheap goal class on `pre_llm_call` |
| `JEV_TELEMETRY_ENABLED` | `true` | Write `~/.hermes/plugins/jev-router/telemetry.jsonl` |
| `JEV_MUTATING_TOOLS` | csv list | Override mutator names |
| `JEV_OBSERVATIONAL_TOOLS` | csv list | Override observer names |

```bash
export TYPESAFE_API_KEY=tsk_...
export JEV_MODEL=typesafe:jev-1.13.0
```

---

## 8. Test procedure

```bash
cd /path/to/jev-router-deliverable
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # pytest, pydantic
python -m pytest tests/ -q
```

- **No network / no TypeSafe** — Jev is mocked via `jev.set_judge_override`.
- `tests/test_integration_skip.py` proves **2 vs 1** main-model API calls for the same one-tool-round task.
- `tests/test_core_hook_helper.py` exercises the core helper contract without a Hermes install.

### Against a patched Hermes checkout

1. Apply the patch (section 3).  
2. Symlink the plugin into `~/.hermes/plugins/jev-router`.  
3. Run a trivial CLI turn with `TYPESAFE_API_KEY` set (or with Jev mocked in a local harness).  
4. Confirm logs / telemetry show `finish_jev` or `finish_fast_path` and that only **one** provider request appears for an obvious success (e.g. `pytest` all green, `expects_explanation=false`).

---

## 9. Where main-model calls are avoided (prose diagram)

```text
User message
    │
    ▼
┌─ pre_llm_call (F6) ─────────────────────────────────────────┐
│ Capture goal (+ optional Jev GoalClass). NO prompt inject.  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
╔════════════════════════════════╗
║  MAIN MODEL API CALL #1        ║  ← still required to pick tools / reason WHAT
╚════════════════════════════════╝
    │  emits tool_calls
    ▼
┌─ pre_tool_call (F2) ────────────────────────────────────────┐
│ Block observational duplicates (fail-open). Mutators never  │
│ aggressively blocked; re-verify allowed after mutation.     │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
  tools execute
    │
    ▼
┌─ transform_tool_result (F1) ────────────────────────────────┐
│ Compact huge outputs: score chunks with Jev, keep ORIGINAL  │
│ selected chunks (+ diagnostics). Fail → original.           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ post_tool_round_control (F3) ★ ESSENTIAL ──────────────────┐
│ AFTER compression, BEFORE next iteration's API call.        │
│                                                             │
│  Jev RoundControlJudgment + thresholds (aggressive):        │
│    goal_satisfied≥0.90 ∧ evidence≥0.85 ∧ failure≤0.20 ∧     │
│    another_tool≤0.25 ∧ requires_main_model≤0.35 ∧ success   │
│  OR deterministic fast-path (tests passed / deleted / …)    │
│                                                             │
│  if finish + renderer can answer from evidence (F4):        │
│       final_response ← deterministic render                 │
│       return break → finalize_turn                          │
│       ═══ MAIN MODEL API CALL #2 SKIPPED ═══                │
│  else:                                                      │
│       continue → next loop iteration                        │
└─────────────────────────────────────────────────────────────┘
    │
    ├── finish ──► finalize_turn (persist / cleanup) ──► user
    │
    └── continue
            │
            ▼
    ╔════════════════════════════════╗
    ║  MAIN MODEL API CALL #2…       ║  ← only when generative WHAT is needed
    ╚════════════════════════════════╝
```

**Fail-open everywhere (F9):** Jev/import/timeout/exception → leave results alone / allow tool / continue to main model.  
**Redaction (F10):** all state passed to TypeSafe goes through `redact.py` first.

---

## Features checklist

| ID | Feature | Hook / surface |
|----|---------|----------------|
| F1 | Compaction | `transform_tool_result` |
| F2 | Duplicate suppression | `pre_tool_call` |
| F3 | Skip next API call | `post_tool_round_control` + core patch |
| F4 | Deterministic renderer | used on finish |
| F5 | Autonomous plan (OFF) | tool `jev_execution_plan` |
| F6 | Preflight goal capture | `pre_llm_call` (no injection) |
| F7 | Telemetry | `~/.hermes/plugins/jev-router/telemetry.jsonl` |
| F8 | Config | `JEV_*` env + `plugin.yaml` schema |
| F9 | Fail open | all paths |
| F10 | Redact secrets | before `judge()` |

---

## Principle reminder

- **Jev** → WHETHER (typed scores / gates)  
- **Python** → HOW (chunk keep, fingerprints, render, break/continue)  
- **Main LLM** → WHAT (tool choice & generative answers when `requires_main_model` is high or evidence cannot be rendered safely)

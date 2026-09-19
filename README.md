# jev-router

**Cut expensive Hermes main-model tokens with a cheap TypeSafe Jev decision layer.**

> Windows Hermes app: short paste-and-check guide → [WINDOWS-INSTALL.md](WINDOWS-INSTALL.md)

Hermes is great when your **main reasoning model** is strong — and expensive. A typical agent turn often looks like:

```text
main model → tools → main model again → (maybe more tools) → main model…
```

That second (and third) main-model call is often just to say “tests passed” or to re-read a huge tool dump that already answered the question. **jev-router** inserts a tiny, typed judgment layer so Hermes only pays for the big model when generative reasoning is actually needed.

```text
Jev decides WHETHER.
Python decides HOW.
The main LLM decides WHAT — only when generation/reasoning is required.
```

Jev is **not** another chat model. It scores small pieces of existing state (probabilities + enums). Python keeps or drops text, blocks duplicate tools, and may finish the turn with a deterministic one-liner built from tool evidence.

---

## Why this saves tokens (and money)

There are **two** expensive costs in a Hermes loop:

| Cost | What burns tokens | How jev-router helps |
|------|-------------------|----------------------|
| **Input / context** | Huge tool outputs (terminal logs, file dumps, search pages) get appended to history and re-sent on every later main-model call | **Compaction** keeps only relevant original chunks before they enter history |
| **API round-trips** | After tools succeed, Hermes normally calls the main model again to narrate the result | **Post-tool finish** can skip that next provider request entirely |

A third waste: the main model sometimes re-runs the **same observational tool** (`read_file`, `git status`, same grep) with no state change. **Duplicate suppression** blocks that before it runs.

### Rough intuition (not a guarantee)

Suppose your main model costs ~$X per 1M input tokens and a turn often:

1. Calls the model once to choose tools  
2. Pulls back **40k characters** of terminal/test output into context  
3. Calls the model **again** just to say “128 passed”

With jev-router on a clear success:

- Compaction might shrink that 40k → ~8k of **original** text (still not a summary — selected chunks only) before call #2 would have happened  
- Finish skip means **call #2 never happens at all** — zero output tokens and zero input for that round

On “research and explain the tradeoffs”, the plugin **continues** to the main model on purpose. Savings come from mechanical / verification turns, not from starving real writing.

Telemetry (`telemetry.jsonl`) records skips, chars before/after compaction, and fail-open counts so you can measure your own workload.

---

## What you get

| Feature | Hook | Token effect |
|---------|------|----------------|
| **Tool result compaction** | `transform_tool_result` | Fewer input tokens on later main-model calls |
| **Duplicate tool guard** | `pre_tool_call` | Avoids wasted tool + follow-up model work |
| **Skip next main-model call** | `post_tool_round_control` *(needs core patch)* | Drops an entire expensive API round-trip |
| **Deterministic “Done …” replies** | renderer on finish | No generative prose when evidence is enough |
| **Preflight goal class** | `pre_llm_call` | Improves later skip decisions; does **not** inject into the system prompt (cache-friendly) |
| **Telemetry** | jsonl under plugin data | Prove savings |
| **Fail-open** | everywhere | Hermes never depends on Jev for correctness |
| **Secret redaction** | before TypeSafe | Don’t ship keys to Jev |

Optional (off by default): bounded **pre-planned** tool continuation (`jev_execution_plan`) so verification steps can chain without inventing commands. See `JEV_AUTONOMOUS_PLAN_ENABLED`.

---

## Architecture

```text
USER
  │
  ▼
MAIN MODEL  (still needed to choose tools / reason WHAT)
  │
  ▼
TOOLS
  │
  ├─► Jev compaction     (shrink huge results before history)
  │
  ▼
Jev loop decision
  ├── FINISH  → deterministic reply → finalize_turn
  │              ★ next main-model API call SKIPPED
  └── CONTINUE → MAIN MODEL again (explanations, ambiguity, failures)
```

### When it finishes without the main model

Defaults are intentionally aggressive (all configurable):

- `goal_satisfied ≥ 0.90`
- `evidence_sufficient ≥ 0.85`
- `contains_failure ≤ 0.20`
- `another_tool_needed ≤ 0.25`
- `requires_main_model ≤ 0.35`
- `outcome == success`

Plus fast paths for obvious successes (e.g. tests all green, file deleted, simple read done) when the user is **not** asking for a long explanation.

### When it always continues to the main model

Examples:

- “Research X and explain the tradeoffs”
- Tool failures / ambiguous outcomes
- Renderer cannot build a truthful reply from evidence (prefer an extra main-model call over inventing text)
- TypeSafe down / no API key / validation error → **fail open**

---

## Install (overview)

1. **Patch Hermes** (required for skip):

```bash
cd /path/to/hermes-agent
patch -p1 < /path/to/hermes-jev-router/patches/hermes-post-tool-round-control.patch
```

Touches only three files, generically — Hermes core knows nothing about TypeSafe/Jev:

- `hermes_cli/plugins.py` — register `post_tool_round_control`
- `hermes_cli/plugins_dispatch.py` — timeout-bounded, **fail-open**
- `agent/turn_tool_round.py` — after tools + compression, before the next API iteration

2. **Install plugin** (standalone, not in-tree):

```bash
mkdir -p ~/.hermes/plugins   # or %LOCALAPPDATA%\hermes\plugins on Windows
cp -a plugins/jev-router ~/.hermes/plugins/jev-router
```

3. **Dependency** (same Python Hermes uses):

```bash
uv pip install --upgrade 'pydantic-ai-slim[typesafe]'
```

4. **Enable** (user plugins are opt-in):

```bash
hermes plugins enable jev-router
hermes plugins list   # must show enabled
```

5. **Env** (e.g. `~/.hermes/.env` or Windows Hermes `.env`):

```env
TYPESAFE_API_KEY=tsk_...
JEV_MODEL=typesafe:jev-1.13.0
JEV_ENABLED=true
```

Restart CLI / Windows app after patch + plugin.

Full Windows walkthrough: [WINDOWS-INSTALL.md](WINDOWS-INSTALL.md).

---

## Quick check

**Should skip** a second main-model narration:

```text
Run this in the terminal and stop when done: echo jev-router-ok
```

Expect a short Done-style finish, not a long second reasoning essay.

**Should still call** the main model:

```text
Research what a mutex is and explain the tradeoffs versus a semaphore.
```

---

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `TYPESAFE_API_KEY` | — | Required for live Jev |
| `JEV_ENABLED` | `true` | Master switch |
| `JEV_MODEL` | `typesafe:jev-1.13.0` | Or `typesafe:jev-latest` |
| `JEV_COMPACTION_MIN_CHARS` | `12000` | Below → leave result unchanged |
| `JEV_COMPACTION_CHUNK_CHARS` | `2000` | Chunk size for scoring |
| `JEV_COMPACTION_KEEP_TOP_K` | `8` | Max non-diagnostic chunks kept |
| `JEV_COMPACTION_HUGE_CHARS` | `80000` | Deterministic sample before Jev |
| `JEV_DUP_REDUNDANCY_MIN` | `0.95` | Duplicate block |
| `JEV_DUP_RELEVANCE_MAX` | `0.40` | Duplicate block |
| `JEV_GOAL_SATISFIED_MIN` | `0.90` | Skip threshold |
| `JEV_EVIDENCE_SUFFICIENT_MIN` | `0.85` | Skip threshold |
| `JEV_CONTAINS_FAILURE_MAX` | `0.20` | Skip threshold |
| `JEV_ANOTHER_TOOL_NEEDED_MAX` | `0.25` | Skip threshold |
| `JEV_REQUIRES_MAIN_MODEL_MAX` | `0.35` | Skip threshold |
| `JEV_AUTONOMOUS_PLAN_ENABLED` | `false` | Experimental plan tool |
| `JEV_PREFLIGHT_CLASSIFY` | `true` | Cheap goal class |
| `JEV_TELEMETRY_ENABLED` | `true` | Write telemetry jsonl |
| `JEV_DEBUG` | `false` | Extra logging |

Compaction always preserves diagnostic-looking chunks (`error`, `traceback`, `failed`, …) and never rewrites kept text — only selects original slices. JSON is handled conservatively.

---

## Repository layout

```text
.
├── README.md
├── WINDOWS-INSTALL.md
├── requirements-dev.txt
├── patches/
│   └── hermes-post-tool-round-control.patch
├── hermes-core-snippets/     # full patched file copies for review/rebase
├── plugins/jev-router/       # copy/symlink → ~/.hermes/plugins/jev-router
└── tests/                    # offline pytest (mocked TypeSafe)
```

---

## Tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

No network required. Integration-style tests assert the **2 vs 1** main-model call pattern for a deterministic successful tool round.

---

## Safety & privacy

- Jev/TypeSafe is an **optimization**, not a dependency: timeouts, missing key, and errors **fail open**.
- Selected tool/state snippets are sent to TypeSafe after basic redaction (`Authorization`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, private keys, etc.).
- Does not replace Hermes approval / safety controls.
- Does not invent final answers: if evidence is insufficient, Hermes continues to the main model.

---

## Principle

| Layer | Role |
|-------|------|
| **Jev (TypeSafe)** | WHETHER — typed probabilistic judgments |
| **Python (this plugin)** | HOW — keep/drop chunks, fingerprints, render, break/continue |
| **Main LLM** | WHAT — tool choice and real generative answers |

Built for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). Rebase later by re-applying the one small generic core-hook patch.

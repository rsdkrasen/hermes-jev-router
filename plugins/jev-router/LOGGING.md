# jev-router decision logging

Analyzable JSONL of what the plugin decided — **no tool result bodies, no secrets**.

## Where to look

| Platform | Path |
|----------|------|
| Windows Hermes app | `%LOCALAPPDATA%\hermes\plugins\jev-router\telemetry.jsonl` |
| `HERMES_HOME` set | `%HERMES_HOME%\plugins\jev-router\telemetry.jsonl` |
| Linux / default | `~/.hermes/plugins/jev-router/telemetry.jsonl` |

Resolution order:

1. `$HERMES_HOME/plugins/jev-router/` when `HERMES_HOME` is set
2. Else `%LOCALAPPDATA%/hermes/plugins/jev-router/` (Windows app layout)
3. Else `~/.hermes/plugins/jev-router/`

The same directory also gets `decisions.jsonl` (subset of decision events for easy grepping). Hermes console logs get one-line summaries such as:

```text
jev-router decision=continue reason=file_mutation_no_fast_path tools=write_file statuses=[ok] mutated=true turn_id=abc seq=3
jev-router decision=finish reason=fast_path_terminal_verify tools=terminal statuses=[ok] mutated=false seq=4
jev-router decision=finish reason=jev_judgment tools=terminal statuses=[ok] jev_ms=412.5 seq=5
```

Disable with `JEV_TELEMETRY_ENABLED=false`.

## Event types

| Event | When |
|-------|------|
| `preflight` | `pre_llm_call` captured goal / expects_explanation |
| `pre_tool_allow` | Tool call approved (not a duplicate block) |
| `dup_block` | Observational duplicate blocked |
| `compact_skip` | Result was large enough to consider, left unchanged |
| `compact_done` | Result compacted (`chars_in` / `chars_out`) |
| `round_continue` | Round continues to main model (`reason` explains why) |
| `round_finish` | Round finishes without another main-model call |
| `fail_open` | Exception caught; plugin continued safely |

### `round_continue` reasons

- `file_mutation_no_fast_path` — write/edit/patch round (must keep tool-calling)
- `observational_only` — read/search-only evidence gathering
- `no_verify_phrase` — terminal-like tools but no strong verify phrase
- `expects_explanation` — user goal needs a model answer
- `jev_unavailable` — Jev missing and no structural fast-path skip
- `thresholds_not_met` — Jev scores below finish thresholds
- `cannot_render` — thresholds OK but renderer refused to invent text
- `not_terminal_verify_tools` / `status_failure` / `failure_in_content` / `no_results`

**`status_failure` false positives:** Hermes core used to mark `status=error` when tool
JSON merely contained the substring `"error"` (e.g. `"error": null` in a successful
terminal payload). That made live verify rounds log `round_continue reason=status_failure`
even when content showed `12 passed` / `all tests passed`. The plugin now overrides that
gate when strong verify success is present and the scrubbed failure scan is clean (nullish
`"error"` JSON keys are ignored). Prefer the core patch so statuses themselves are `ok`.

### `round_finish` reasons

- `fast_path_terminal_verify` — deterministic terminal/bash success (`12 passed`, …)
- `jev_judgment` — Jev scores + deterministic renderer

## Fields (typical)

Always / auto-attached from session when available:

| Field | Meaning |
|-------|---------|
| `ts`, `session_id`, `event` | Envelope |
| `seq` | Monotonic event sequence within the session (ordering) |
| `turn_id` | Hermes turn id from hook kwargs when provided |
| `goal_preview` | First ~80 chars of `user_goal` (newlines stripped; secrets redacted) |
| `mutation_epoch` | Session mutation counter at event time |
| `main_model_calls_avoided` | Session skip counter at event time |
| `jev_ms` | Judge latency in ms when an override/network Jev call ran; **omitted** otherwise |

Decision-specific: `action`, `reason`, `tools`, `statuses` (list of status name strings only — e.g. `["ok"]` / `["error"]` — for analyzing `status_failure` vs real failures), `mutated`, `expects_explanation`, `api_call_count`, judgment scores when present (`goal_satisfied`, …), `chars_in` / `chars_out` for compaction.

Never logged: raw tool `content` / `result` / `args` / API keys / tokens / full huge goals.

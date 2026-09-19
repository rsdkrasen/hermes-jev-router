# jev-router — quick install (Hermes Windows app)

## Give them

1. This repo (or a zip of it)
2. Their own `TYPESAFE_API_KEY`

## 1. Hermes home (typical)

```text
C:\Users\<You>\AppData\Local\hermes\
C:\Users\<You>\AppData\Local\hermes\hermes-agent\
C:\Users\<You>\AppData\Local\hermes\config.yaml
C:\Users\<You>\AppData\Local\hermes\.env
C:\Users\<You>\AppData\Local\hermes\plugins\
```

## 2. Apply the core patch (required to skip the next main-model call)

```bash
cd /mnt/c/Users/<You>/AppData/Local/hermes/hermes-agent
patch -p1 < /path/to/this-repo/patches/hermes-post-tool-round-control.patch
```

Patches: `hermes_cli/plugins.py`, `hermes_cli/plugins_dispatch.py`, `agent/turn_tool_round.py`.

## 3. Install the plugin

Copy `plugins/jev-router/` to:

```text
C:\Users\<You>\AppData\Local\hermes\plugins\jev-router\
```

## 4. Python dependency (same env Hermes uses)

```bash
cd /mnt/c/Users/<You>/AppData/Local/hermes/hermes-agent
source .venv/bin/activate   # create with: uv venv
uv pip install --upgrade 'pydantic-ai-slim[typesafe]'
python -c "import typesafe_sdk; print('ok')"
```

## 5. Env

In `C:\Users\<You>\AppData\Local\hermes\.env`:

```env
TYPESAFE_API_KEY=tsk_your_key_here
JEV_MODEL=typesafe:jev-1.13.0
JEV_ENABLED=true
```

## 6. Enable (user plugins are opt-in)

```bash
hermes plugins enable jev-router
hermes plugins list
```

Or in `config.yaml`:

```yaml
plugins:
  enabled:
    - jev-router
```

Fully quit and reopen the Windows app.

## 7. Check

**Should skip second model call** (short Done… reply):

```text
Run this in the terminal and stop when done: echo jev-router-ok
```

**Should still call the main model** (explanation):

```text
Research what a mutex is and explain the tradeoffs versus a semaphore.
```

Optional: `...\plugins\jev-router\telemetry.jsonl`

| Symptom | Fix |
|--------|-----|
| not enabled | `hermes plugins enable jev-router` |
| not listed | wrong `plugins\` folder |
| always second long reply | patch missing / wrong checkout / no key / no typesafe extra |

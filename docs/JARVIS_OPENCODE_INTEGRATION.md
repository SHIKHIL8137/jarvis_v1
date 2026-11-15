# Jarvis OpenCode Integration Guide

## 1. Overview

`OpenCodeAgent` (`src/openjarvis/agents/opencode.py`) wraps the `opencode` CLI (https://opencode.ai) via its headless HTTP server mode. This allows OpenJarvis to leverage OpenCode's agentic loop, multi-step planning, and editing tools while OpenJarvis supplies the local inference model and resource controls.

---

## 2. Environment Verification

On the current host machine:
- **Binary**: Located at `C:\Users\shikh\AppData\Roaming\npm\opencode.CMD`
- **Version**: `1.19.5`
- **Availability**: `is_opencode_available()` returns `True`.

---

## 3. Server Lifecycle & Provider Wiring

When FORGE triggers an OpenCode coding session:
1. `OpenCodeAgent._ensure_server()` writes an ephemeral configuration file (`opencode.json`) specifying an OpenAI-compatible provider wired to OpenJarvis's active inference server (vLLM, LM Studio, Ollama).
2. The agent spawns `opencode serve --port 0 --hostname 127.0.0.1` as a background process with shared authentication.
3. The server communicates via HTTP REST:
   - **V1 API**: `/api/session`, `/api/session/{id}/prompt`
   - **V2 API**: `/api/session`, `/api/session/{id}/message`
4. When the session finishes or errors out, the server process is cleanly terminated.

---

## 4. Seamless Fallback to Native GoalLoop

If the `opencode` binary is uninstalled, fails to bind a port, or returns an execution error, `ForgeCodingEngine` automatically falls back to Jarvis's native `GoalLoop`:

```python
if opencode_usable:
    try:
        # Run OpenCodeAgent
        ...
    except Exception as err:
        logger.warning("OpenCodeAgent failed; falling back to native GoalLoop: %s", err)
        opencode_usable = False

if not opencode_usable:
    # Run native GoalLoop (roles.build + roles.review + AGENTS.md)
    goal_res = run_goal(objective=objective, model=coding_model, workdir=workdir)
```

This dual-tier approach guarantees that FORGE will never fail to execute a coding task due to an external binary failure.

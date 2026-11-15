# Jarvis V1 — Coding Integration & OpenCode Source Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Architect & OpenCode Integration Engineer  
**Scope:** `src/openjarvis/coding/`, `src/openjarvis/agents/opencode.py`, `src/openjarvis/workforce/`

---

## 1. Executive Summary

This audit evaluates the relationship between the workforce specialist **FORGE** and the existing coding system in Jarvis V1 (`openjarvis/coding/` and `openjarvis/agents/opencode.py`).

The existing codebase contains a rich, OpenCode-inspired native coding system featuring:
- A multi-round **Goal Loop** (`src/openjarvis/coding/goal.py`)
- Four specialized coding roles: `build`, `explore`, `plan`, and `review` (`src/openjarvis/coding/roles.py`)
- Slash commands: `/goal`, `/plan`, `/review`, `/init` (`src/openjarvis/coding/commands.py`)
- Automated `AGENTS.md` / `CLAUDE.md` discovery (`src/openjarvis/coding/runtime.py`)
- Read-only subagents (`src/openjarvis/tools/subagent.py`)
- Structured context compaction for long runs (`src/openjarvis/coding/compaction.py`)
- An OpenCode server adapter (`src/openjarvis/agents/opencode.py`)

**The Critical Disconnect:** In the workforce implementation, FORGE was defined with generic tools (`file_read`, `file_write`, `think`) and disconnected from the `coding/` goal loop and `OpenCodeAgent`. When ATLAS assigned a software engineering task to FORGE, FORGE simply executed a one-off tool call instead of invoking the full iterative build-review cycle!

---

## 2. Detailed Findings

### Issue CI-1: FORGE Bypasses the Native Coding Goal Loop
- **Source Files:** `src/openjarvis/workforce/definitions.py`, `src/openjarvis/tasks/engine.py`
- **Root Cause:** FORGE’s manifest lists individual tools, and `BackgroundTaskEngine._execute_step()` treats FORGE like any single tool-caller.
- **Integration Impact:** Complex development tasks (e.g. "Implement inventory feature in ERP") require multiple exploratory, implementation, test, and review rounds. Treating FORGE as a single-turn step leads to incomplete, untested code changes.
- **Proposed Fix:** Wire FORGE directly to the existing `GoalLoop` in `openjarvis/coding/goal.py`. When FORGE executes a coding task, it invokes the native coding goal loop or the `OpenCodeAgent`, collecting test evidence and reviewer sign-off before returning its report to ATLAS.

### Issue CI-2: OpenCode Binary Detection & Server API Compatibility
- **Source File:** `src/openjarvis/agents/opencode.py` (Lines 37-40, 150-240)
- **Root Cause:** `OpenCodeAgent` expects an external `opencode` binary on the host `PATH`. If not present, instantiation or execution fails unless cleanly caught.
- **Integration Impact:** If a user does not have `opencode` installed via npm or brew, attempting to use OpenCode directly causes an error rather than gracefully falling back to Jarvis's native Python coding loop (`openjarvis/coding/goal.py`).
- **Proposed Fix:** Implement a resilient tiering:
  1. Check `is_opencode_available()`. If true and server is reachable, FORGE uses `OpenCodeAgent`.
  2. If `opencode` is not installed or the server fails to start, FORGE seamlessly uses the native Python coding workflow (`openjarvis/coding/goal.py`), which provides identical `/goal`, `/plan`, and `/review` functionality natively.

### Issue CI-3: Hardcoded Model in Coding Runtime
- **Source File:** `src/openjarvis/coding/runtime.py` (Lines 40-44)
- **Root Cause:** `pick_model()` delegates to `auto_router.model_for(kind)`. In `auto_router.py`, if discovery fails or pins are missing, it falls back to a hardcoded string `"qwen2.5:3b"`.
- **Integration Impact:** If the user has a different model available (e.g. DeepSeek-Coder, Claude 3.5 Sonnet, or GPT-4o), the coding runtime might attempt to invoke a missing local model.
- **Proposed Fix:** Connect `runtime.py` to the centralized Model Selection Engine, requesting a model with `coding: true` and `tools: true`.

### Issue CI-4: Lack of Workspace File Locking in Multi-Agent Coding
- **Source File:** `src/openjarvis/tasks/engine.py`
- **Root Cause:** When multiple agents run in parallel, there is no file locking mechanism to prevent two agents (e.g., Scout and Forge) from reading/writing overlapping files simultaneously.
- **Integration Impact:** Potential race conditions during parallel DAG execution.
- **Proposed Fix:** Introduce workspace ownership: only FORGE may acquire write locks on code files; other specialists (SCOUT, SENTINEL, VANGUARD) acquire read-only locks.

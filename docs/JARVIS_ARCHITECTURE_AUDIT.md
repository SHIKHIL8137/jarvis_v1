# Jarvis V1 Architecture and Technical Audit
**Date:** 2026-11-15  
**Auditor:** Principal AI Systems Architect & Lead Engineer  
**System Target:** AMD Ryzen 5 5600H, NVIDIA GTX 1650 (4 GB VRAM), 16 GB RAM, Windows 11

---

## 1. Executive Summary

This document provides a comprehensive technical audit of the OpenJarvis (Jarvis V1) codebase, evaluating its readiness for transformation into an autonomous, persistent, voice-enabled personal AI assistant inspired by Mark-LVII.

The existing repository is a sophisticated local-first AI system featuring an engine abstraction layer, multi-agent frameworks, tool execution engine, SQLite-based telemetry/traces/approvals/sessions, and a modern React 19/TypeScript frontend. However, prior to this upgrade, several critical capabilities were fragmented, partially implemented, disconnected, or missing guardrails:
- **Voice Intelligence:** Audio recording and playback helpers existed in isolation, but lacked continuous session management, barge-in/interruption during speech, local wake-word integration, language consistency, and self-echo protection against Bluetooth/speaker audio loops.
- **Background Autonomy:** `AgentManager` and `agent_tasks` stored basic task records, but lacked comprehensive task lifecycle state machines, step checkpointing, crash recovery with idempotency, steering (pause/resume/cancel/steer via voice and API), execution budgets, and isolated capability profiles.
- **Routines & Scheduling:** Cron/interval scheduling was tied strictly to agent ticks and proactive digests without a unified user-facing routine engine, missed-run catchup logic, or quiet-hour enforcement.
- **Deep Research:** Multi-hop search and synthesis existed, but lacked a formal 6-stage evidence pipeline (Plan, Investigate, Extract, Review, Write, Verify), quotation verification against raw page content, citation provenance, and SSRF/prompt-injection web sanitization.
- **Long-Term Memory:** `LocalFactStore` stored unstructured facts in JSONL, but lacked semantic categorization (preferences, projects, environment, decisions, ongoing tasks), deduplication, contradiction detection, confidence decay, and user management APIs.
- **Security & Approvals:** Approvals existed in `ApprovalStore` for proactive actions, but sensitive local operations (destructive shell, file writes/deletions, desktop control) lacked centralized mandatory approval gating, safe-by-default filesystem path restrictions (empty `allowed_dirs` was unrestricted), action journaling, and file-level compensation/undo.
- **Self-Awareness & Capability Registry:** Jarvis lacked a unified runtime registry reporting hardware, model health, tool capabilities, and permissions to both the system prompt and the UI.
- **Command Center UI:** The frontend had rich visualizations for energy, traces, and chat, but lacked cohesive command-center panels for background tasks, approval handling, scheduled routines, memory curation, and voice settings.

---

## 2. Detailed Component Audit

### 2.1 Engine Abstraction & Model Routing (`src/openjarvis/engine/`)
- **Current State:**
  - `InferenceEngine` base class with streaming (`stream`), non-streaming (`complete`), and tool-calling support.
  - Backends: Ollama, vLLM, SGLang, llama.cpp, MLX, LM Studio, Exo, Nexa, Uzu, Apple FM, Cloud (OpenAI/Anthropic/Google/LiteLLM).
  - Discovery: `_discovery.py` probes engine endpoints and enumerates available models.
- **Gaps & Defects:**
  - Model selection is static or hardcoded per route.
  - No hardware-aware routing for 4 GB VRAM: loading heavy models locally causes OOM or crashes.
  - No dynamic voice-activated model switching ("Switch to Qwen", "Which model is active?").

### 2.2 Voice & Speech Pipeline (`src/openjarvis/speech/`, `src/openjarvis/wake/`)
- **Current State:**
  - Speech-to-Text: `faster_whisper.py` (local Faster-Whisper), `openai_whisper.py`, `deepgram.py`.
  - Text-to-Speech: `kokoro_tts.py` (local 82M Kokoro), `openai_tts.py`, `cartesia_tts.py`, `elevenlabs_tts.py`.
  - Wake-word: Standalone prototype in `src/openjarvis/wake/__main__.py` using `openwakeword`.
  - Audio I/O: `voice_io.py` with `record_until_silence` and `play_wav` via `sounddevice`.
- **Gaps & Defects:**
  - **Self-Echo:** Jarvis frequently transcribes its own speech playback when using laptop or Bluetooth speakers, causing endless feedback loops.
  - **Lack of Continuous Session Manager:** No stateful audio pipeline that transitions cleanly between Idle, Wake-Word Listening, Active Listening, Processing, Speaking, and Interrupted.
  - **No Barge-In Support:** Playback does not stop immediately when the user speaks ("stop", "wait", "cancel").
  - **Language Inconsistency:** Language detection is not centralized; multilingual queries can trigger unintended translation.
  - **No Instant Voice Acknowledgment:** Background tasks do not emit immediate verbal feedback before starting execution.

### 2.3 Autonomous Task Engine (`src/openjarvis/agents/`)
- **Current State:**
  - `AgentManager` (`manager.py`) manages SQLite `managed_agents` and `agent_tasks`.
  - `AgentExecutor` (`executor.py`) coordinates tool-calling loop with `loop_guard.py`.
- **Gaps & Defects:**
  - Task status model is rudimentary (`pending`, `running`, `completed`). Missing: `created`, `planning`, `running`, `waiting_approval`, `paused`, `recovering`, `verifying`, `failed`, `cancelled`.
  - No step-by-step checkpointing of plan, tools executed, partial outputs, and idempotency keys.
  - On process restart, interrupted tasks are either abandoned or blindly restarted from the beginning, risking duplicated side-effects.
  - No steering interface to pause, resume, cancel, or steer ongoing tasks.
  - Missing resource budgets (time, token limit, tool call ceiling, retry count).
  - Agents are not sandboxed by capability profiles (e.g. research agents could invoke shell tools if registered).

### 2.4 Routines and Proactive Engine (`src/openjarvis/agents/scheduler.py`, `proactive_agent.py`)
- **Current State:**
  - `AgentScheduler` schedules agent ticks using croniter and intervals.
  - `ProactiveAgent` runs periodic scans.
- **Gaps & Defects:**
  - Two separate scheduling systems without unified state or persistence.
  - No user routine management (one-time, daily, weekly, interval, startup).
  - No quiet-hour enforcement (no unsolicited voice notifications between quiet hours, defaulting to `Asia/Kolkata`).
  - No missed-run policy (system restart could trigger backlogged executions).

### 2.5 Deep Research Engine (`src/openjarvis/agents/deep_research.py`, `research_loop.py`, `server/research_router.py`)
- **Current State:**
  - Multi-hop search using `HybridSearch` and web search tools.
- **Gaps & Defects:**
  - Missing structured 6-phase research workflow (PLAN -> INVESTIGATE -> EXTRACT EVIDENCE -> REVIEW -> WRITE -> VERIFY).
  - Evidence and citations lack verification: hallucinated quotes or snippet-only assumptions are possible.
  - Reports are not structured or systematically saved with versioning in a designated reports directory.
  - Untrusted web content is not sanitized against prompt injection, and HTTP fetch tools lack strict SSRF protection (preventing calls to private IPs and cloud metadata).

### 2.6 Long-Term Memory & Sessions (`src/openjarvis/memory/`, `src/openjarvis/sessions/`)
- **Current State:**
  - `LocalFactStore` saves text facts to `memory_facts.jsonl` with provenance (`auto`, `trusted`, `untrusted`).
  - `SessionStore` persists session messages to `sessions.db`.
- **Gaps & Defects:**
  - Memory facts are flat strings without semantic categories (preferences, projects, environment, tools, decisions, ongoing tasks, long-term goals).
  - No duplicate or contradiction detection between facts.
  - No memory controls in the API or UI (search, edit, delete, pin, view provenance).
  - Session continuity does not extract or carry over active work context and unfinished tasks across conversation boundaries.

### 2.7 Security, Permissions, and Filesystem (`src/openjarvis/security/`, `src/openjarvis/tools/`)
- **Current State:**
  - `ApprovalStore` handles proactive approvals in `approvals.db`.
  - `folder_grants.py` manages persistent directory grants.
  - `FileReadTool`, `FileWriteTool`, `ShellExecTool` enforce basic constraints.
- **Gaps & Defects:**
  - **Filesystem Security Flaw:** If `security.allowed_dirs` is empty, file tools default to unrestricted access across all disk drives.
  - **No Centralized Action Authorization:** Tools execute without a unified permission checker enforcing 5 distinct risk tiers (Read-only, Safe local, Reversible local, Sensitive, Irreversible).
  - **Approval Bypass Risk:** Remembered permissions could inadvertently allow destructive commands or file modifications without fresh approval.
  - **No Action Journal & Undo:** No unified action journal recording tool executions, results, and compensation/undo logic (e.g. restoring modified files).

### 2.8 Capability Registry & Self-Awareness (`src/openjarvis/server/model_capabilities.py`)
- **Current State:**
  - Model capabilities are evaluated for context length, structured outputs, and vision.
- **Gaps & Defects:**
  - No centralized runtime capability registry summarizing host hardware, GPU VRAM, active engines, available tools, permissions, and voice status.
  - The assistant cannot introspect its own real-time state when asked "Can you control my desktop?" or "Which model are you using?".

### 2.9 Frontend Command Center (`frontend/src/`)
- **Current State:**
  - Modern React 19 + TypeScript + Vite application.
  - Existing panels: Chat, Dashboard (Energy, Traces, Cost), Desktop Settings, Memory Browser (partial), ApprovalBell.
- **Gaps & Defects:**
  - Lacks dedicated Background Task Manager view (progress, steps, steering controls).
  - Lacks unified Routine Manager view.
  - Approval center is minimal and not tied into all sensitive tool operations.
  - Voice settings lack microphone/speaker testing, wake-word sensitivity, and audio device selection.

---

## 3. Dependency & Reuse Map

The following existing modules are validated and will be extended directly without rewriting or duplication:

| Capability Area | Existing Module to Extend | Extension / Enhancement Strategy |
| :--- | :--- | :--- |
| **Configuration** | `src/openjarvis/core/config.py` | Add `VoiceSessionConfig`, `RoutinesConfig`, `TaskEngineConfig`, safe `allowed_dirs` defaults. |
| **Voice Audio Pipeline** | `src/openjarvis/speech/voice_io.py`, `_discovery.py` | Implement `VoiceSessionManager`, self-echo guard, barge-in detector, audio device router. |
| **Wake-Word** | `src/openjarvis/wake/` | Integrate `openwakeword` directly into the unified `VoiceSessionManager`. |
| **STT & TTS** | `src/openjarvis/speech/` | Enhance Faster-Whisper & Kokoro with language preservation, speed/volume, and instant acknowledgment. |
| **Task Engine** | `src/openjarvis/agents/manager.py`, `executor.py` | Implement `BackgroundTaskEngine` with full lifecycle, checkpoints, recovery, and steering. |
| **Routines** | `src/openjarvis/agents/scheduler.py` | Extend into unified `RoutineEngine` supporting user routines, quiet hours, and missed runs. |
| **Research Engine** | `src/openjarvis/agents/deep_research.py`, `research_loop.py` | Upgrade to 6-stage evidence pipeline with quotation verification and SSRF guard. |
| **Memory System** | `src/openjarvis/memory/store.py`, `service.py` | Add structured categories, deduplication, contradiction detection, and CRUD APIs. |
| **Action Security & Approvals** | `src/openjarvis/tools/approval_store.py`, `security/runtime.py` | Implement `CentralPermissionEngine`, `ActionJournal`, and file backup/undo. |
| **Filesystem Tools** | `src/openjarvis/tools/_paths.py`, `file_read.py`, `file_write.py` | Enforce safe-by-default canonical path sandboxing and directory grants. |
| **Capability Registry** | `src/openjarvis/server/` | Build runtime `CapabilityRegistry` exposing hardware, AI, tools, and permissions. |
| **Frontend UI** | `frontend/src/` | Enhance Command Center dashboard, task manager, approval center, routine panel, and voice settings. |

---

## 4. Hardware Baseline & Operational Constraints

- **Platform:** Windows 11 (x86_64)
- **CPU:** AMD Ryzen 5 5600H (6 cores, 12 threads)
- **RAM:** 16 GB DDR4
- **GPU:** NVIDIA GeForce GTX 1650 (4 GB GDDR6 VRAM)
- **Constraint Strategy:**
  - VRAM is strictly capped at 4 GB. Local LLMs larger than ~3B-4B Q4 must not be forced into GPU memory alongside Whisper or Kokoro.
  - Faster-Whisper operates optimally on CPU with `int8` or GPU with `float16` when LLM is offloaded or hosted via Ollama.
  - Wake-word detection runs locally on CPU with ONNX runtime to consume < 1% CPU when idle.
  - Memory extraction and background task planning leverage efficient local models or user-configured cloud endpoints.

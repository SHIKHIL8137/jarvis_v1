# JARVIS PHASE 9 — INDEPENDENT REPOSITORY AUDIT

**Date:** 2026-11-16  
**Auditor:** Principal Software Architect & QA Automation Engineering Team  
**Scope:** Full-stack source inspection across backend, frontend, extensions, speech, browser, security, workforce, and test suites.  
**Platform:** Windows 11 (AMD Ryzen 5 5600H, NVIDIA GTX 1650 4GB VRAM, 15.3GB RAM).

---

## 1. Executive Summary

This independent audit investigates the current state of Jarvis V1 to verify claimed capabilities against actual source code, identify defects and technical debt, and establish a repeatable stabilization plan.

Rather than relying on prior documentation claims, this audit inspected the actual Python modules (`src/openjarvis/`), TypeScript frontend (`frontend/`), Chrome extension (`extensions/`), and automated test suites (`tests/`).

### Overall System Health
- **Backend Architecture:** Mature FastAPI and modular subpackages. SQLite-backed persistence for tasks, approvals, routines, scheduler, and memory facts.
- **Frontend Architecture:** React 19, TypeScript, Tailwind CSS v4, Vite, Zustand, and Tauri desktop integration. Vitest suite passes (148 tests), production bundle builds cleanly.
- **Local AI Inference:** Live Ollama daemon is running on `http://127.0.0.1:11434` with `qwen3:4b-instruct` (4.0B, tools/thinking), `qwen2.5vl:3b` (vision), and `qwen3:4b`.
- **Cloud Providers:** Free model discovery on OpenRouter is reachable via public API (`https://openrouter.ai/api/v1/models`). Zero-paid boundary is enforced across selection logic, but contains a policy leak when `ModelPolicy.CLOUD_FIRST` is set.
- **Browser Automation:** Multi-tier architecture (Extension bridge, CDP bridge, Windows Win32 detector). Tab reuse logic exists in `DesktopBrowserManager`, but background tabs are invisible when only title detection is available without extension/CDP.
- **Voice System:** Comprehensive pipeline featuring `faster-whisper`, `kokoro_tts`, `edge_tts`, and `EchoGuard`. Missing Malayalam language mapping in `LanguageManager`, and potential state leakage when Whisper detects non-English language on short utterances.
- **Automated Tests:** Over 9,800 test cases exist in the repository. One collection error discovered in `tests/workforce/test_specialist_workforce.py` due to an incorrect import path for `CancellationToken`.

---

## 2. Actual Subsystem Architectures

### A. AI Model Provider & Selection Engine
- **Source Files:** `src/openjarvis/models/discovery.py`, `src/openjarvis/models/selector.py`, `src/openjarvis/engine/`
- **Current State:**
  - `ModelDiscoveryEngine` probes local Ollama (`/api/tags`), LM Studio, NVIDIA NIM, and cloud engines.
  - Hardware is detected accurately: `NVIDIA GeForce GTX 1650`, `4.0GB VRAM`.
  - `ModelSelectionEngine` dynamically scores models based on task type (`PLANNING`, `CODING`, `RESEARCH`, `SECURITY`, `VERIFICATION`, `OPERATIONS`).
- **Defects Identified:**
  1. *Cloud-First Policy Leak:* In `selector.py:177`, `elif self.policy == ModelPolicy.CLOUD_FIRST:` sets `effective_allow_paid = True`. This violates the mandatory requirement: *"A cloud-first policy must NOT automatically enable paid models. Paid models must be disabled by default."*
  2. *OpenRouter Metadata Filtering:* In `discovery.py:654-661`, free models are detected only via string matching (`":free"` in model name). The provider's explicit `pricing` object (`{"prompt": "0", "completion": "0"}`) returned by OpenRouter API is not parsed.

### B. Browser Session Management & Tab Reuse
- **Source Files:** `src/openjarvis/browser/manager.py`, `matcher.py`, `cdp_bridge.py`, `extension_bridge.py`, `windows_detector.py`, `src/openjarvis/tools/desktop.py`
- **Current State:**
  - `DesktopBrowserManager` uses a 6-step decision pipeline (`plan_open_url_decision`):
    1. Check if browser running.
    2. Retrieve accessible tabs.
    3. Match destination against existing tabs (`TabMatcher.find_best_tab`).
    4. Reuse tab (activate or navigate).
    5. Open new tab only if no matching tab exists.
    6. Launch browser if not running.
- **Root Cause of Duplicate Tabs:**
  - When the user runs Chrome without the companion extension connected or without remote debugging (`--remote-debugging-port`), `list_tabs()` falls back to `detector.get_browser_windows()`.
  - Windows Win32 API (`EnumWindows`) only retrieves the *window title* of the currently *active tab*. Background tabs have empty URLs (`url=""`) and are completely invisible.
  - When a user asks Jarvis to interact with LinkedIn while YouTube is active in Chrome, `find_best_tab` sees only YouTube, concludes LinkedIn is not open, and creates a duplicate tab.
  - Furthermore, `DesktopBrowserManager` needs Playwright integration to reliably inspect and control browser contexts when automation tasks are dispatched.

### C. Voice Assistant Pipeline
- **Source Files:** `src/openjarvis/speech/session_manager.py`, `echo_guard.py`, `language.py`, `faster_whisper.py`, `voice_io.py`, `profiles.py`
- **Current State:**
  - `EchoGuard` tracks playback state and maintains a guard interval to absorb speaker reverberation and avoid capturing Jarvis's own TTS output.
  - `VoiceSessionManager` manages lifecycle states (`IDLE`, `WAKE_LISTENING`, `ACTIVE_LISTENING`, `PROCESSING`, `SPEAKING`, `INTERRUPTED`).
  - TTS supports Edge TTS, Kokoro, and Windows SAPI fallback.
- **Defects Identified:**
  1. *Missing Malayalam Support:* `LanguageManager` (`language.py`) lacks Malayalam (`ml`) in `LANGUAGE_NAMES` and lacks script regex `[\u0D00-\u0D7F]`.
  2. *False Language Sticky State:* If a short utterance or noise is falsely detected as a foreign language, `_active_language` permanently latches to that language, injecting prompt directives that instruct the LLM to reply in that language even when subsequent input is English.

### D. Personal Memory & Conversation Continuity
- **Source Files:** `src/openjarvis/memory/store.py`, `service.py`, `src/openjarvis/tools/storage/context.py`
- **Current State:**
  - Facts are persisted in JSONL with timestamps, category, and source confidence.
  - `CredentialStripper` scrubs API keys and secrets before storage.
  - Expired memories (`expires_at`) are withheld from recall context.
  - Relevance filtering (`filter_relevant_facts`, `min_fact_score`) retrieves relevant facts for user queries.

### E. Workspace & Filesystem Security
- **Source Files:** `src/openjarvis/coding/workspace.py`, `src/openjarvis/sandbox/verification_runner.py`, `src/openjarvis/security/subprocess_sandbox.py`, `src/openjarvis/tools/approval_store.py`
- **Current State:**
  - Canonical path resolution enforces workspace boundary confinement.
  - Symlink escapes, UNC paths, and Windows junction escapes fail closed.
  - `ApprovalStore` enforces atomic test-and-set claiming and single-use execution locks.
  - Subprocess sandboxing uses `CREATE_NEW_PROCESS_GROUP` and process tree cleanup via `taskkill /F /T`.

### F. Specialist Workforce & Task Execution
- **Source Files:** `src/openjarvis/workforce/orchestrator.py`, `registry.py`, `definitions.py`, `src/openjarvis/tasks/engine.py`, `store.py`
- **Current State:**
  - Defined specialists: Atlas (Orchestrator), Forge (Coding), Scout (Research), Sentinel (Security), Vanguard (Verification), Oracle (Knowledge), Pulse (Operations).
  - Background task state machine supports checkpointing and crash recovery.
- **Defects Identified:**
  1. *Collection ImportError:* `tests/workforce/test_specialist_workforce.py` imports `CancellationToken` from `openjarvis.agents._stubs` instead of `openjarvis.core.cancellation`.

---

## 3. Discovered Technical Debt & Defect Inventory

| Defect ID | Severity | Subsystem | Description | Root Cause |
|---|---|---|---|---|
| **DEF-01** | **P0** | Model Selector | `ModelPolicy.CLOUD_FIRST` permits paid models | `elif self.policy == ModelPolicy.CLOUD_FIRST: effective_allow_paid = True` ignores zero-cost boundary |
| **DEF-02** | **P1** | Model Discovery | OpenRouter free filtering relies on model name string heuristics | Discovery does not parse `pricing` metadata object (`prompt == "0"`, `completion == "0"`) |
| **DEF-03** | **P1** | Browser Manager | Duplicate tabs created when extension/CDP disconnected | Win32 fallback can only see active tab title; background tabs invisible |
| **DEF-04** | **P1** | Speech Language | Malayalam missing from language detection; false sticky language latch | `language.py` lacks `ml` script range and persists transient non-English detections |
| **DEF-05** | **P1** | Workforce Tests | Test collection fails on `test_specialist_workforce.py` | Import path for `CancellationToken` moved to `openjarvis.core.cancellation` |
| **DEF-06** | **P2** | Hardware Resource | 4GB VRAM constraint risk when multiple models or parallel workers spawn | Max concurrent local inferences must strictly check available VRAM before loading |

---

## 4. Audit Baseline Verification Status

- **Stage A Read-Only Audit:** Complete.
- **Traceability Matrix:** Generated in `docs/phase-9-verification/REQUIREMENT_TRACEABILITY.md`.
- **Application Code:** Untouched during Stage A audit.

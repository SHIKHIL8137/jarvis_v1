# OpenJarvis Changelog — Mark-LVII Architecture Upgrade

## Version 1.1.0-mark-lvii (2026-11-15)

### Summary
Comprehensive architectural upgrade transforming Jarvis V1 into a production-grade, highly autonomous, persistent, voice-enabled personal AI assistant inspired by Mark-LVII. Engineered specifically for workstation laptops (AMD Ryzen 5 5600H, NVIDIA GTX 1650 4GB VRAM, 16GB RAM).

---

### Added Capabilities & Enhancements by Milestone

#### Milestone 0: Audit & Security Foundation
- **Security Baseline:** Fixed filesystem access vulnerabilities in `src/openjarvis/tools/_paths.py`, `file_read.py`, and `file_write.py`. Empty `allowed_dirs` now falls back safely to `get_safe_default_dirs()` while strictly blocking Windows and Unix system-critical directories (`C:\Windows`, `/etc`, etc.).
- **5-Tier Permission Hierarchy:** Introduced `PermissionLevel` in `src/openjarvis/security/types.py` (`READ_ONLY`, `SAFE_LOCAL`, `REVERSIBLE_LOCAL`, `SENSITIVE`, `IRREVERSIBLE`).
- **Audit Documentation:** Produced `JARVIS_ARCHITECTURE_AUDIT.md`, `JARVIS_IMPLEMENTATION_PLAN.md`, and `JARVIS_FEATURE_STATUS.md`.

#### Milestone 1: Advanced Voice Intelligence
- **EchoGuard (`src/openjarvis/speech/echo_guard.py`):** Solves Bluetooth and laptop speaker feedback loops with playback state tracking, post-playback guard window, and verbal barge-in interruption (`stop`, `wait`, `cancel`, `hold on`, `pause`).
- **Session Manager (`src/openjarvis/speech/session_manager.py`):** 6-state FSM (`IDLE`, `WAKE_LISTENING`, `ACTIVE_LISTENING`, `PROCESSING`, `SPEAKING`, `INTERRUPTED`) supporting local ONNX "Hey Jarvis" wake-word detection and immediate verbal acknowledgment (`generate_instant_ack`).
- **Language Manager (`src/openjarvis/speech/language.py`):** Dynamic script and language detection ensuring Jarvis replies in the user's spoken language while mapping to native Kokoro voice profiles.
- **Audio Device Management (`src/openjarvis/speech/voice_io.py`):** Added `list_audio_devices()` and interruptible chunked audio playback.
- **REST Endpoints:** Added `/v1/speech/devices`, `/v1/speech/state`, `/v1/speech/interrupt`, `/v1/speech/wake/start`, `/v1/speech/wake/stop`.

#### Milestone 2: Autonomous Background Task Engine
- **Task Engine (`src/openjarvis/tasks/`):**
  - 10-state FSM (`CREATED`, `PLANNING`, `RUNNING`, `WAITING_APPROVAL`, `PAUSED`, `RECOVERING`, `VERIFYING`, `COMPLETED`, `FAILED`, `CANCELLED`).
  - Step-by-step checkpointing and idempotency keys persisted in SQLite WAL (`tasks.db`).
  - Crash recovery on startup (`recover_on_startup()`) resuming interrupted tasks without duplicating side-effects.
  - Runtime task steering (`steer_task`, `pause_task`, `resume_task`, `cancel_task`, `skip_step`).
  - Capability profiles (`research`, `coding`, `monitor`, `general`).
- **REST Endpoints:** Added `/v1/tasks`, `/v1/tasks/{id}`, `/v1/tasks/{id}/start`, `/v1/tasks/{id}/pause`, `/v1/tasks/{id}/resume`, `/v1/tasks/{id}/cancel`, `/v1/tasks/{id}/steer`, `/v1/tasks/{id}/skip`, `/v1/tasks/{id}/events`, `/v1/tasks/{id}/artifacts`.

#### Milestone 3: Unified Routines & Scheduling
- **Routine Engine (`src/openjarvis/routines/`):**
  - Unified scheduler for one-time, daily, weekly, interval, cron, and startup routines persisted in `routines.db`.
  - Quiet hours manager (`Asia/Kolkata`, 22:00-07:00) suppressing unsolicited voice synthesis and queuing notifications.
  - Missed-run catchup logic upon reboot.
- **REST Endpoints:** Added `/v1/routines`, `/v1/routines/{id}`, `/v1/routines/{id}/pause`, `/v1/routines/{id}/resume`, `/v1/routines/{id}/run`, `/v1/routines/quiet-hours`.

#### Milestone 4: Deep Research Engine & Categorized Long-Term Memory
- **6-Phase Deep Research (`src/openjarvis/research/`):**
  - Structured pipeline (`PLAN` → `INVESTIGATE` → `EXTRACT EVIDENCE` → `REVIEW` → `WRITE` → `VERIFY`).
  - Quotation verification cross-checking citations against raw source passages.
  - SSRF defense (`is_safe_target_url`) blocking loopback, RFC1918 private IPs, and cloud metadata endpoints.
  - Prompt-injection sanitization neutralizing prompt override payloads and script tags.
  - Markdown report artifact generation saved to `~/.openjarvis/reports/`.
- **Categorized Memory (`src/openjarvis/memory/store.py`):**
  - 8-category taxonomy (`preferences`, `projects`, `environment`, `tools`, `decisions`, `tasks`, `goals`, `facts`).
  - Trust tiers (`trusted`, `auto`, `untrusted`), deduplication, and pin/unpin controls.
- **REST Endpoints:** Added `/v1/memory/facts`, `/v1/memory/facts/{id}`, `/v1/memory/facts/{id}/pin`.

#### Milestone 5: Action Security, Command Center & Model Routing
- **Action Journal & Rollback (`src/openjarvis/security/action_journal.py`):**
  - SQLite audit trail of privileged tool executions.
  - Pre-modification file snapshots saved to `~/.openjarvis/backups/`.
  - Compensation and undo engine (`undo_action`) enabling restoration of modified files or deletion of created files.
- **Approval Enforcement (`src/openjarvis/server/approval_routes.py`):**
  - Backend enforcement requiring fresh explicit user approval for sensitive/irreversible operations.
  - Disallowed `remember=true` bypass for high-risk actions.
- **Runtime Capability Registry (`src/openjarvis/core/capabilities.py`):**
  - Dynamic host hardware detection (Ryzen 5 5600H, GTX 1650 4GB VRAM, RAM, OS).
  - Exposing system telemetry via `/v1/capabilities`.
- **Dynamic Model Router (`src/openjarvis/engine/router.py`):**
  - Natural language and voice intent switching ("Switch to Qwen", "Which model are you using?").
  - 4GB VRAM capacity safeguard preventing local OOM errors.
- **Command Center UI (`frontend/src/`):**
  - Added `TaskCenter.tsx` for live background task management and steering.
  - Added `RoutineManager.tsx` with Asia/Kolkata quiet-hours status.
  - Enhanced `DashboardPage.tsx` with hardware telemetry cards and operations control.
  - Updated `ApprovalBell.tsx` with risk indicators and strict approval enforcement.
  - Expanded `api.ts` with TypeScript client methods.

#### Milestone 6: Verification & Complete Deliverables
- Verified all 69 Python backend unit and integration tests passing.
- Verified all 117 React frontend Vitest tests passing.
- Verified React TypeScript bundle compilation and Vite production build.
- Generated complete documentation:
  - `docs/JARVIS_ARCHITECTURE.md`
  - `docs/JARVIS_API.md`
  - `docs/JARVIS_SECURITY.md`
  - `docs/JARVIS_SETUP.md`
  - `docs/JARVIS_PERFORMANCE.md`
  - `docs/JARVIS_CHANGELOG.md`
  - `docs/JARVIS_FEATURE_STATUS.md`

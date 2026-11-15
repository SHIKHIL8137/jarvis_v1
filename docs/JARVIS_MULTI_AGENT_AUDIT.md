# Jarvis V1 Multi-Agent & Orchestration Audit Report

**Audit Date:** 2026-11-15  
**Auditor:** Principal AI Architect, Systems & Security Engineering  
**Focus:** Existing Agent Frameworks, Orchestration Mechanics, Background Tasks, Runtime Lifecycles, and Multi-Agent Readiness.

---

## 1. Executive Summary

This audit evaluates the current Jarvis V1 codebase to establish the baseline for upgrading Jarvis into a **true multi-agent workforce** (an Avengers-style team led by ATLAS as Chief of Staff / Commander).

The audit reveals strong foundational primitives (e.g. `AgentManager`, `BackgroundTaskEngine`, `RoutineEngine`, `ActionJournal`, `EchoGuard`, `DeepResearchPipeline`, and SQLite WAL persistence), but identifies several critical architectural gaps and integration defects that must be resolved to achieve autonomous multi-agent orchestration.

---

## 2. Detailed Inspection of Existing Subsystems

### 2.1 Agent Frameworks & Lifecycle Management (`src/openjarvis/agents/`)
- **`AgentManager` (`src/openjarvis/agents/manager.py`):**
  - *Current Status:* Provides SQLite persistence for `managed_agents`, `agent_tasks`, `channel_bindings`, `agent_checkpoints`, and `agent_messages`.
  - *Strengths:* Good thread locking (`_db_locked`), WAL mode, and basic recovery for stale ticks (`_clear_stale_running_state`).
  - *Gaps:* Tailored primarily for isolated single operatives (`monitor_operative`) running periodic ticks on cron/interval schedules. Lacks multi-agent team concepts: no dynamic role registry, no real-time heartbeat monitoring, no inter-agent message bus, no shared task context DAG, and no team status queries.
- **`ConductorAgent` (`src/openjarvis/agents/hybrid/conductor.py`):**
  - *Current Status:* Static DAG planner based on Sakana AI's Conductor paper.
  - *Strengths:* Implements structured planning (`model_id`, `subtasks`, `access_list`), prompt builder, and fallback logic.
  - *Gaps:* Disconnected from the real runtime tools and server execution loop. It maps numbered model IDs rather than named specialist agents, lacks parallel execution of independent DAG steps, and does not enforce capability profile sandboxing.
- **`OrchestratorAgent` (`src/openjarvis/agents/orchestrator.py`):**
  - *Current Status:* ReAct/function-calling loop agent executing tools sequentially.
  - *Strengths:* Handles single-agent multi-turn tool loops well.
  - *Gaps:* Executes tools on behalf of a single agent identity; cannot decompose complex multi-domain user goals across multiple specialist agents.

### 2.2 Task Engine & Planning (`src/openjarvis/tasks/`)
- **`BackgroundTaskEngine` (`src/openjarvis/tasks/engine.py`):**
  - *Current Status:* Robust 10-state FSM with SQLite persistence, step checkpointing, idempotency keys, steering, and crash recovery.
  - *Critical Defect (Defect 8.1):* `_generate_plan()` in `engine.py` currently relies on a **hardcoded two-step plan** (`knowledge_search` → `think`). It lacks a dynamic LLM-based planner that analyzes user requests and outputs a multi-step DAG with assigned specialist agents.
  - *Integration Need:* Must be integrated with the ATLAS Orchestrator so that background tasks represent multi-agent DAGs with bounded concurrency.

### 2.3 Application Startup & Lifespan Lifecycle (`src/openjarvis/server/app.py`)
- *Critical Defect (Defect 8.2):* `app.py` has a shutdown handler (`@app.on_event("shutdown")`), but **lacks a startup lifecycle handler**.
  - `task_engine` is not initialized or recovered on server startup.
  - `routine_engine` is not automatically started.
  - No `agent_workforce_registry` is initialized on startup.
  - No agent heartbeat service is launched.
  - Deprecated `@app.on_event` handlers trigger FastAPI deprecation warnings and should be migrated to modern FastAPI `lifespan`.

### 2.4 Security & Tool Capability Isolation (`src/openjarvis/security/`)
- *Strengths:* `PermissionLevel` (5 tiers), `ApprovalStore`, `ActionJournal` (with pre-modification file snapshots and rollback), and filesystem boundary enforcement (`_paths.py`).
- *Gaps (Defects 8.3 & 8.4):* Tool execution permissions must be enforced strictly per agent manifest. An agent manifest must never be allowed to grant itself additional permissions. Sensitive and irreversible operations must continue requiring fresh explicit human approval, strictly preventing agent self-approval or cross-agent privilege escalation.

### 2.5 Voice & Language Intelligence (`src/openjarvis/speech/`)
- *Strengths:* `VoiceSessionManager`, `EchoGuard`, local ONNX wake-word ("Hey Jarvis"), and `LanguageManager`.
- *Gaps (Defect 8.5):* The voice session is currently not wired to answer team status queries ("How many agents are online?", "What is Forge doing?") or to perform spoken team startup announcements respecting `Asia/Kolkata` quiet hours.

### 2.6 Frontend Command Center (`frontend/src/`)
- *Strengths:* Rich React 19 UI with `DashboardPage.tsx`, `TaskCenter.tsx`, `RoutineManager.tsx`, and `ApprovalBell.tsx`.
- *Gaps (Defect 8.9):* `AgentsPage.tsx` focuses on cron operatives and templates; it lacks a dedicated Agent Workforce command center showing the 7 specialist agents, live heartbeats, team metrics, and live communication timeline.

---

## 3. Architecture Blueprint for the Multi-Agent Workforce

To resolve these defects without duplicating code or disrupting existing functionality:

```
┌────────────────────────────────────────────────────────┐
│                   Jarvis Server Lifespan               │
│  - Init TaskEngine & run recover_on_startup()          │
│  - Init RoutineEngine & start scheduling loop          │
│  - Init AgentWorkforceRegistry (Load 7 Specialists)    │
│  - Start Agent Heartbeat & Stale Detector Daemon       │
│  - Publish Team Startup Event & Announcement           │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│         ATLAS: Chief of Staff / Team Commander         │
│  - Dynamic LLM-Based Task Planner                      │
│  - Validates DAG, Capabilities, and Tool Allowlists    │
│  - Coordinates Bounded Parallel Execution              │
│  - Collects Specialist Reports & Synthesizes Final Resp│
└─────────┬─────────────────┬──────────────────┬─────────┘
          │                 │                  │
    ┌─────▼─────┐     ┌─────▼─────┐      ┌─────▼─────┐
    │  ORACLE   │     │   FORGE   │      │ SENTINEL  │ ... (SCOUT,
    │ Research  │     │ Engineering│     │ Security  │     VANGUARD,
    │ Specialist│     │ Specialist│      │ Specialist│     PULSE)
    └─────┬─────┘     └─────┬─────┘      └─────┬─────┘
          │                 │                  │
┌─────────▼─────────────────▼──────────────────▼─────────┐
│              Central Security Gateway                  │
│  - Enforces 5-Tier Permission Hierarchy                │
│  - Strict Backend-Enforced Fresh Explicit Approvals    │
│  - Action Journal Audit Trail & Snapshot Undo          │
└────────────────────────────────────────────────────────┘
```

---

## 4. Remediation Plan

1. **M0:** Fix startup lifecycle and lifespan handling in `src/openjarvis/server/app.py`.
2. **M1:** Build `src/openjarvis/workforce/` (Manifests, 11-State Registry, Heartbeat Service, Status APIs).
3. **M2:** Build `AtlasOrchestrator` & Dynamic LLM Planner replacing hardcoded steps in `tasks/engine.py`.
4. **M3:** Build Team Communication Bus, Shared Context Scoping, and Conflict Resolution.
5. **M4:** Wire Voice queries & announcements; Build Real-time Agent Workforce Dashboard in Frontend.
6. **M5:** Enforce Capability Profiles, Permission Gateway, and Low-VRAM Resource Management.
7. **M6:** Implement optional n8n tool connector, comprehensive automated tests, and final documentation.

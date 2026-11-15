# Jarvis V1 — Multi-Agent Workforce & Autonomous Orchestration Changelog

## Release: Multi-Agent Autonomous Workforce Upgrade (2026-11-15)

### Summary of Completed Milestones

#### Milestone M0 — Comprehensive Technical Source Audit
- Completed full audit of existing `AgentManager`, `ConductorAgent`, `BackgroundTaskEngine`, `RoutineEngine`, `ApprovalStore`, and `server/app.py`.
- Published [docs/JARVIS_MULTI_AGENT_AUDIT.md](file:///d:/projects/oprn-jarvis/docs/JARVIS_MULTI_AGENT_AUDIT.md) identifying 8 critical architectural defects and resolution roadmaps.

#### Milestone M1 — Autonomous Workforce Foundation
- Created `src/openjarvis/workforce/models.py`:
  - Defined 11-state `AgentRuntimeState` lifecycle state machine.
  - Defined `AgentManifest`, `AgentHeartbeat`, `AgentReport`, `TeamSummary`.
- Created `src/openjarvis/workforce/definitions.py`:
  - Configured 7 specialist agent manifests: ATLAS (Chief of Staff), ORACLE (Research), FORGE (Software Engineering), SENTINEL (Security & Infra), SCOUT (File & Knowledge), VANGUARD (QA & Verification), PULSE (Operations & Productivity).
- Created `src/openjarvis/workforce/store.py`:
  - Implemented SQLite WAL persistence in `workforce.db` for manifests, runtime statuses, heartbeats, structured reports, inter-agent messages, and team events.
- Created `src/openjarvis/workforce/registry.py`:
  - Implemented `AgentWorkforceRegistry` singleton with dynamic tool resolution (`_ensure_tools_registered`), background stale agent detection, natural language status queries, and startup announcements.

#### Milestone M2 — Orchestration & Dynamic DAG Planner
- Updated `src/openjarvis/tasks/models.py`:
  - Enhanced `TaskStep` with `assigned_agent`, `dependencies`, `expected_output`, `verification_criteria`.
  - Added `WAITING_APPROVAL` to `StepStatus`.
- Created `src/openjarvis/workforce/orchestrator.py`:
  - Implemented `AtlasOrchestrator` with dynamic task planning (LLM + semantic heuristic fallback), DAG acyclicity validation, bounded concurrency (`max_parallel_workers = 2`), and consolidated executive report synthesis by ATLAS.
- Integrated `AtlasOrchestrator.plan_task()` into `BackgroundTaskEngine._generate_plan()`.

#### Milestone M3 — Team Communication & Conflict Resolution
- Created `src/openjarvis/workforce/communication.py`:
  - Implemented `TeamMessageBus` supporting typed inter-agent messages (`FINDING`, `CONTEXT_REQUEST`, `VERIFICATION_REQUEST`, `BLOCKER`, `HANDOVER`) and scoped context filtering.
  - Implemented `ConflictResolver` detecting contradictions between specialists and enforcing verification against raw evidence.

#### Milestone M4 — Server API, Startup Lifecycle, Voice Integration & Frontend UI
- Created `src/openjarvis/server/workforce_routes.py`:
  - REST endpoints: `/v1/workforce/agents`, `/summary`, `/tasks`, `/query`, `/announcement`, `/timeline`, `/reports`, `/heartbeat`, enable/disable.
- Updated `src/openjarvis/server/api_routes.py`:
  - Mounted workforce routes into the FastAPI application.
- Updated `src/openjarvis/server/app.py`:
  - Fixed server startup lifecycle with `@app.on_event("startup")` recovering background tasks, auto-starting `RoutineEngine`, and initializing `AgentWorkforceRegistry` with heartbeat monitor.
  - Added clean shutdown handlers.
- Updated `src/openjarvis/speech/session_manager.py`:
  - Added spoken startup announcement respecting quiet hours (`Asia/Kolkata`).
  - Added conversational workforce status querying.
- Updated `frontend/src/lib/api.ts`:
  - Added TypeScript bindings for all `/v1/workforce/*` endpoints.
- Created `frontend/src/components/Desktop/AgentWorkforce.tsx`:
  - Interactive Avengers-style agent roster with real-time health badges, team summary bar, details drawer, natural language status query input, multi-agent task delegation modal, and live DAG timeline.
- Mounted `AgentWorkforce` into `frontend/src/pages/DashboardPage.tsx`.
- Successfully verified frontend production build with `tsc -b && vite build` and ran 117 passing vitest tests.

#### Milestone M5 — Security, Capability Profiles & Resource Management
- Updated `src/openjarvis/tasks/engine.py`:
  - Enforced specialist agent tool permissions against manifests; Oracle and Sentinel denied file write / shell commands.
  - Integrated `ApprovalStore` gating for sensitive actions (`file_write`, `shell_exec`, `desktop`, `channel_send`).
  - Implemented `WAITING_APPROVAL` task suspension until explicit human approval is granted.
  - Updated `src/openjarvis/server/approval_routes.py` to auto-resume waiting tasks upon approval.
  - Enforced `max_parallel_workers = 2` to preserve GTX 1650 4 GB VRAM budget.

#### Milestone M6 — n8n Automation Tool, Full Tests & Documentation
- Created `src/openjarvis/tools/n8n.py`:
  - Implemented `N8nTool` with health checks, workflow listing, and webhook execution.
- Created `tests/workforce/test_workforce_e2e.py`:
  - Implemented full ERP review acceptance test verifying Scout -> Forge -> Sentinel -> Oracle -> Vanguard -> Atlas DAG execution, heartbeat updates, inter-agent messaging, and report synthesis.
- Created complete documentation suite in `docs/`:
  - `docs/JARVIS_MULTI_AGENT_ARCHITECTURE.md`
  - `docs/JARVIS_AGENT_TEAM.md`
  - `docs/JARVIS_AGENT_LIFECYCLE.md`
  - `docs/JARVIS_AGENT_SECURITY.md`
  - `docs/JARVIS_MULTI_AGENT_API.md`
  - `docs/JARVIS_MULTI_AGENT_TESTS.md`
  - `docs/JARVIS_MULTI_AGENT_CHANGELOG.md`

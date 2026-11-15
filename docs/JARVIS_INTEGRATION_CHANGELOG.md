# Jarvis V1 Integration & Upgrade Changelog

## Version: Advanced Model Intelligence & Autonomous Workforce Integration
**Date:** 2026-11-15  
**Architect:** Principal AI Systems & Orchestration Architect  

---

## 1. Key Highlights & Achievements

1. **Complete Elimination of Hardcoded Models**:
   - Decoupled agent definitions from fixed model strings across the entire codebase.
   - Built a dynamic **Model Discovery Engine** discovering 86+ models across local endpoints (vLLM, Ollama, LM Studio, NIM) and cloud providers (Anthropic, OpenAI, Google, DeepSeek, OpenRouter).
   - Implemented a hardware-aware **Model Selection Engine** respecting the host's AMD Ryzen 5 5600H CPU and NVIDIA GeForce GTX 1650 (4.0 GB VRAM) boundary with max 2 concurrent inferences.

2. **Unified FORGE Coding Engine**:
   - Connected FORGE to OpenCode (CLI 1.19.5 on PATH) via headless server mode and Jarvis's native `GoalLoop` (`openjarvis/coding/goal.py`).
   - Implemented `WorkspaceLock` for thread-safe file concurrency protection.
   - Built automated test verification (`pytest` / `npm test`) and structured `AgentReport` synthesis.

3. **Production-Grade n8n Integration**:
   - Connected Jarvis and agent PULSE to live instance `https://n8n.kavlien.store`.
   - Built dual authentication supporting API key (`X-N8N-API-KEY`) and session login (`/rest/login` with `emailOrLdapLoginId`).
   - Added in-memory session cookie caching with **6-day TTL** ($518,400\text{ seconds}$), completely preventing HTTP 429 rate limits.
   - Added standard browser User-Agent header to avoid Cloudflare/reverse proxy 403 blocks.
   - Enforced strict human approval gating via `ApprovalStore` for all mutating executions.

4. **REST APIs & Desktop UI**:
   - Added `/v1/models/discovered`, `/v1/models/providers`, `/v1/models/policy`, `/v1/models/select`, `/v1/models/hardware`.
   - Added `/v1/n8n/status`, `/v1/n8n/workflows`, `/v1/n8n/workflows/{id}/execute`, `/v1/n8n/webhooks/{path}`.
   - Built `ModelIntelligencePanel.tsx` and `N8nPanel.tsx` with one-click policy controls, model catalog, and workflow execution.
   - Rebuilt frontend with 0 errors (`tsc -b && vite build`) and verified 117 Vitest tests.

---

## 2. Detailed File Modifications

### 2.1 Model Intelligence (`src/openjarvis/models/`)
- `capabilities.py`: Created `ModelPolicy`, `ModelCapabilityInfo`, and `SelectionResult` with property aliases (`model_name`, `rationale`, `policy_used`, `candidate_scores`).
- `discovery.py`: Created `ModelDiscoveryEngine` for local & cloud probing, capability inference, and endpoint health checking.
- `selector.py`: Created `ModelSelectionEngine` with task-based scoring, hardware allocation, policy switches, and runtime failure penalties.
- `__init__.py`: Exported discovery and selection singletons.

### 2.2 Workforce Decoupling (`src/openjarvis/workforce/`)
- `models.py`: Removed hardcoded `active_model = "qwen2.5:3b"` and `model_preference`; decoupled agent identity.
- `definitions.py`: Stripped hardcoded model assignments across all 7 manifests (Atlas, Oracle, Forge, Sentinel, Scout, Vanguard, Pulse); added `required_capabilities`.
- `registry.py`: Initialized runtime states with dynamically selected models; added automatic manifest loading on init.
- `orchestrator.py`: Replaced hardcoded planning model defaults with `select_model(task_type="planning")`; integrated `ForgeCodingEngine` into step executor.
- `forge_coding.py`: Created `ForgeCodingEngine`, `WorkspaceLock`, and registered `ForgeCodingTool` in `ToolRegistry`.
- `store.py`: Removed hardcoded model fallbacks in SQLite database methods.

### 2.3 Router & Runtime Hardcoding Cleanup
- `src/openjarvis/engine/router.py`: Replaced static model array and default with `ModelDiscoveryEngine.discover_all()` and `ModelSelectionEngine.select_model(task_type="chat")`.
- `src/openjarvis/coding/runtime.py`: Routed `pick_model()` through `ModelSelectionEngine.select_model(task_type="coding")`.

### 2.4 Automation & Tools
- `src/openjarvis/tools/n8n.py`: Completely rewrote with `N8nClient`, dual authentication, session cookie caching (6d TTL), custom User-Agent, and `ApprovalStore` gating.
- `src/openjarvis/tools/__init__.py`: Registered `n8n` and `forge_coding` in `ToolRegistry`.

### 2.5 Server REST Endpoints (`src/openjarvis/server/`)
- `models_routes.py`: Created routes for model discovery, providers, policies, selection explanation, and hardware profile.
- `n8n_routes.py`: Created routes for n8n status, workflow listing, execution, and webhooks.
- `api_routes.py`: Mounted `models_router` and `n8n_router` on FastAPI app.

### 2.6 Frontend (`frontend/src/`)
- `lib/api.ts`: Added TypeScript interfaces and client functions for models and n8n.
- `components/Desktop/ModelIntelligencePanel.tsx`: Created visual control center for VRAM governance, allocation policies, why-model-selected debugger, and model catalog.
- `components/Desktop/N8nPanel.tsx`: Created live connectivity badge, workflow list, and approval-gated execution modal.
- `components/Desktop/AgentWorkforce.tsx`: Added sub-tab navigation ("Workforce Roster", "Model Intelligence & Hardware", "n8n Automation Connector").

### 2.7 Testing Suite (`tests/`)
- `tests/models/test_model_discovery.py`: 6 tests passing.
- `tests/models/test_model_selection.py`: 5 tests passing.
- `tests/workforce/test_forge_coding_integration.py`: 4 tests passing.
- `tests/tools/test_n8n_integration.py`: 5 tests passing.
- `tests/workforce/test_scenarios_e2e.py`: 4 tests passing (Scenarios A, B, C, D).
- `tests/workforce/`: All 24 tests passing.
- `frontend/`: All 117 tests passing across 23 test files.

### 2.8 Documentation Suite (`docs/`)
- `docs/JARVIS_MODEL_INTELLIGENCE_AUDIT.md`
- `docs/JARVIS_CODING_INTEGRATION_AUDIT.md`
- `docs/JARVIS_N8N_INTEGRATION_AUDIT.md`
- `docs/JARVIS_MODEL_DISCOVERY.md`
- `docs/JARVIS_MODEL_SELECTION.md`
- `docs/JARVIS_MODEL_CAPABILITIES.md`
- `docs/JARVIS_CODING_INTEGRATION.md`
- `docs/JARVIS_OPENCODE_INTEGRATION.md`
- `docs/JARVIS_N8N_INTEGRATION.md`
- `docs/JARVIS_MULTI_AGENT_ARCHITECTURE.md`
- `docs/JARVIS_MODEL_TEST_REPORT.md`
- `docs/JARVIS_INTEGRATION_CHANGELOG.md`

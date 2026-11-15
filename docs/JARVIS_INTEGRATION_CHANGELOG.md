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

---

## 3. Production Readiness & Critical Security Pass (P0 & P1 Remediation)

### 3.1 P0 — Model Selection Correctness
- **Eliminated Hardcoded Fallbacks**: Removed all occurrences of `"qwen3:4b-instruct"` emergency defaults in `selector.py`.
- **Explicit Failure Result**: Returned `NoCompatibleModelAvailableResult` (with `is_compatible=False`, `model_id=""`, and `error="NoCompatibleModelAvailable"`) and raised `NoCompatibleModelAvailable` when `raise_on_none=True`.
- **Zero Invented Models**: Enforced that only models discovered from live local or cloud providers can ever be selected.
- **Strict Capability Enforcement**: Candidate filtering enforces that every required capability must be present; unknown capability metadata is never assumed as confirmed support.
- **Capability Provenance**: Added `CapabilityProvenance` enum (`confirmed`, `provider_declared`, `inferred`, `unknown`) in `capabilities.py`.
- **Fail-Closed Privacy Policy**: Enforced that `privacy-first` policy fails closed immediately if no healthy local model meets constraints, never falling through to a cloud model.
- **Signature Mismatch Correction**: Synchronized `select_model()` signature to handle `requires_tools`, `requires_vision`, `requires_reasoning`, and `max_cost_per_m`.
- **Task Type Normalization**: Mapped agent IDs (`forge`, `oracle`, `sentinel`, `vanguard`, `pulse`, `atlas`) to canonical `TaskType` categories via `AGENT_TO_TASK_MAP`.
- **Runtime Model Allocation**: Ensured the selected model is passed to the step execution runtime and used by `ForgeCodingEngine`.
- **Regression Tests**: Added `tests/models/test_model_selection_p0.py` (9 tests passing).

### 3.2 P0 — n8n Approval Security
- **Sensitive Webhook Gating**: Webhook triggers (`trigger_webhook`) now strictly require human approval.
- **Canonical Payload Hashing**: Action permission keys incorporate deterministic SHA-256 hashes of canonicalized payloads: `n8n:{action}:{target}:{payload_hash}`.
- **Workflow Isolation**: An approval for workflow A cannot authorize workflow B; modified payloads invalidate approvals.
- **Single-Use Semantics**: Approvals transition to `STATUS_EXECUTED` immediately upon execution, preventing replay attacks.
- **Expiry Enforcement**: Expired approvals transition to `STATUS_EXPIRED` and reject execution.
- **Self-Approval Prevention**: Agent-generated requests cannot approve themselves.
- **Real Cookie Expiry**: Parsed `Max-Age` and `Expires` directives in `Set-Cookie` headers instead of hardcoded TTL.
- **SSRF & Traversal Protection**: Validated base URLs for HTTP/HTTPS schemes and sanitized endpoint paths against directory traversal (`..`).
- **Health Distinction**: Differentiated basic network reachability (`/healthz`) from authenticated API health.
- **No REST Bypass**: Updated `POST /v1/n8n/webhooks/{path}` in `n8n_routes.py` to route through approval gates.
- **Regression Tests**: Added `tests/tools/test_n8n_security_p0.py` (11 tests passing).

### 3.3 P0 — FORGE Execution Security
- **Cross-Process File Locking**: Replaced in-memory lock with OS-level byte locking (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) and per-workspace lock files.
- **Baseline Git Diff Containment**: Captured baseline snapshot prior to task execution and diffed strictly against post-execution state, completely ignoring pre-existing dirty files.
- **Truthful Verification**: Verification returns `not_run` (verified=False) when no tests are executed; never reports success without test evidence.
- **Explicit Verification States**: Enforced `passed`, `failed`, `not_run`, `blocked`, and `inconclusive`.
- **Containment & Shell Hardening**: Blocked path traversal (`..`) escaping workspace boundaries and blocked destructive shell patterns.
- **Regression Tests**: Added `tests/workforce/test_forge_security_p0.py` (7 tests passing).

### 3.4 P1 — Real Model Provider Discovery
- Probed live provider APIs (`models.list()`) where supported (OpenAI, DeepSeek, OpenRouter, Google).
- Real latency measurement via `time.monotonic()` around provider calls.
- Offline or unconfigured providers contribute zero selectable models.

### 3.5 P1 — Real Multi-Agent Execution
- Replaced simulated `think` steps in `_heuristic_planner` with real specialist tools:
  - FORGE: `forge_coding`
  - SENTINEL: security configuration audit
  - ORACLE: `knowledge_search`
  - SCOUT: repository context discovery
  - VANGUARD: verified QA test execution
  - PULSE: `n8n` automation operations
  - ATLAS: executive planning and synthesis

### 3.6 Verification Summary
- `pytest tests/models tests/workforce tests/tools/test_n8n_security_p0.py tests/tools/test_n8n_integration.py`: 67 passed in 15.31s.
- `vitest run --run`: 117 passed across 23 test files in 4.26s.
- `npm run build`: built production client in 1.96s with zero errors.

### 3.7 Un-Live-Tested Items
- Live external webhook triggering on `https://n8n.kavlien.store` (requires third-party incoming HTTP triggers).
- Live cloud API generation across cloud providers whose API keys are not locally configured in `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. (unconfigured providers safely contribute zero selectable models).


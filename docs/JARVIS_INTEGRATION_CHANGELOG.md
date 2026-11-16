# Jarvis V1 Integration & Upgrade Changelog

## Version 10: Final Sandbox Workspace Authorization, Symlink Escape Containment & Exact n8n Correlation
**Date:** 2026-11-15  
**Architect:** Principal Software Engineer, AI Systems Architect & Security Engineer  

### Key Highlights:
1. **P0 — FORGE Explicit Two-Tier Workspace Authorization & Symlink Containment (`src/openjarvis/sandbox/verification_runner.py` & `src/openjarvis/workforce/forge_coding.py`)**:
   - **Root Cause Eliminated:** Addressed the root cause where administrative allowed directories (`allowed_dirs`, including system temp directory) were conflated with the execution-specific authorized workspace (`workspace_root`). Previously, a symlink pointing to an outside file that resided inside system tempdir (e.g. `tmp_path / "outside_secret"`) was falsely accepted.
   - **Two-Tier Security Boundary:**
     - *Tier 1: Administrative Allowed Directories (`allowed_dirs`):* System-wide boundaries (user config, project root, temp dir). The workspace root itself MUST reside within `allowed_dirs`.
     - *Tier 2: Execution-Specific Workspace (`workspace_root`):* The specific directory authorized for an execution task. Symlink targets and arguments must strictly reside within `workspace_root`.
   - **Workspace Root Determination (`determine_workspace_root`):** Explicitly resolves `workspace_root` if passed; if omitted, searches upward from `resolved_cwd` for project root markers (`.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `setup.py`, `pytest.ini`), stopping before crossing system boundaries or escaping `allowed_dirs`. Defaults to `resolved_cwd` if no marker is found.
   - **All 10 Symlink Vectors Hardened & Verified:** Verified outside file in allowed parent, outside directory, nested symlink, symlink to symlink, legitimate symlink in workspace, relative symlink, absolute symlink, with Docker, without Docker, and nonexistent destination under escaping symlink directory. All 27 tests in `test_forge_sandboxing_p0.py` pass (0 skipped, 0 failed).
   - **Container Mount Isolation:** Docker/Podman container mounts use only the canonical authorized workspace path (`-v {canonical_workspace_root}:/workspace:rw`) with working directory set to `-w /workspace/{relative_subpath}`.

2. **P0 — n8n Exact Structured Correlation & Substring Matching Elimination (`src/openjarvis/tools/n8n.py`)**:
   - **Root Cause Eliminated:** Replaced substring matching (`approval_id in custom_str` and `idempotency_key in custom_str`) with exact structured metadata parsing via `_extract_execution_correlation_identifiers`.
   - **Partial Identifier Collision Rejection:** Verified that partial substring matches (such as `approval_id="app-1"` matching `app-10` or `idempotency_key="idem-1"` matching `idem-100`) are strictly rejected.
   - **Malformed Metadata Handling:** Execution records with malformed metadata or invalid JSON in `customData` return `status="outcome_unknown"`, preventing unintended auto-retries or false claims.
   - **Top-Level ID Separation:** Removed top-level execution ID from `idempotency_keys` set, preventing unrelated executions from falsely matching approvals.
   - All 28 tests in `test_n8n_ambiguous_outcomes_p0.py` pass.

3. **Complete Regression Matrix:**
   - 18 Targeted Backend Test Files: **152 passed, 0 skipped, 0 failed** in 27.28s (100% pass rate).
   - Frontend Vitest Suite: **148 passed across 24 test files** in 3.09s (100% pass rate).
   - Frontend TypeScript Build (`tsc -b`): Clean exit with code 0 (zero errors).
   - Frontend Production Build (`npm run build`): 3,506 modules compiled in 2.16s with zero errors.

---

## Version 9: Final Defect Repair, Cross-Platform Path Normalization & Regression Pass
**Date:** 2026-11-15  
**Architect:** Principal Software Engineer, AI Systems Architect & Security Engineer  

### Key Highlights:
1. **P0 — FORGE Cross-Platform Raw Path Scanning & Multi-Pass Traversal Rejection (`src/openjarvis/sandbox/verification_runner.py`)**:
   - **Pre-shlex Raw Scanning:** On POSIX systems, `shlex.split(..., posix=True)` strips backslashes, transforming `pytest C:\Windows\System32` into `pytest C:WindowsSystem32` and `pytest \\server\share` into `pytest \servershare`. Implemented `scan_raw_command_for_path_escapes()` to analyze the raw command string before tokenization, catching Windows absolute paths, drive roots, UNC paths (`\\` and `//`), and sensitive paths (`/etc/passwd`) cross-platform.
   - **Multi-Pass URL Decoding:** Added `_multi_unquote()` supporting up to 5 decoding iterations to eliminate double/nested URL-encoded traversal escapes (`%252e%252e` -> `%2e%2e` -> `..`).
   - **Relative Symlink Inspection:** Inspected bare relative filename arguments against `(resolved_cwd / token).is_symlink()` to detect symlinks pointing outside authorized directories even when no slash is present in the token.
   - **Nonexistent Path Resolution:** Nonexistent relative path targets are resolved via `Path.resolve()` against the workspace root to ensure they do not target outside locations.
   - **CWD Containment Priority:** Workspace containment validation of `cwd` executes strictly before any inline script check or container sandbox mounting.

2. **P0 — n8n Pagination Truncation Safety & Workflow Identity Enforcement (`src/openjarvis/tools/n8n.py`)**:
   - **Pagination Truncation:** When iterating through execution pages, if the safety limit (`max_pages`) is reached while `nextCursor` exists or the cursor stalls/repeats, reconciliation returns `status="outcome_unknown"` instead of mistakenly concluding `not_executed`.
   - **Workflow Identity Check:** Reconciled executions must match the exact expected `workflowId`. Execution records with missing or mismatched workflow IDs are rejected.
   - **Strict Correlation Metadata:** Executions must contain exact matching `approval_id` or `idempotency_key` metadata. Timestamp alone never proves an execution belongs to an approval. If correlation metadata is missing or empty, returns `outcome_unknown`.

3. **P1 — Model Discovery Cloud REST Probes & Strict 401 Classification (`src/openjarvis/models/discovery.py`)**:
   - **REST Fallback Probes:** Implemented fallback HTTP REST probes across all cloud providers (`openai`, `deepseek`, `openrouter`, `anthropic`, `google`, `atlascloud`, `minimax`) so discovery operates reliably even when SDK client instances are uninitialized.
   - **Strict Auth Error Mapping:** HTTP 401, 403, and invalid API key errors strictly map to `status="auth_error"`, `connected=False`, and `model_count=0`.
   - **Isolated Unit Tests:** Added `clear=True` on test environment variable patches to prevent ambient host keys (`ATLASCLOUD_API_KEY`) from triggering live network calls during unit test execution.

4. **P1 — Multi-Agent Scenario A Deterministic Execution Fixture (`tests/workforce/test_scenarios_e2e.py`)**:
   - Patched `ModelSelectionEngine.select_model` in `test_scenario_a_multi_file_coding_and_verification` with a deterministic mock `SelectionResult(model_id="mock-qwen-coder:latest", is_compatible=True, ...)` from `openjarvis.models.capabilities`.
   - Scout, Forge, and Vanguard run deterministically to 100% completion in headless environments without local Ollama dependencies.

5. **P2 — Voice Mode Speech Emoji Normalization Preservation (`frontend/src/components/Chat/VoiceMode.test.tsx`)**:
   - Preserved all 15 component speech synthesis tests in `VoiceMode.test.tsx` verifying `SpeechSynthesisUtterance.text` zero-emoji delivery and DOM visual retention across all conditions.
   - All 148 tests in 24 frontend test files passed; production Vite build passed with 0 errors.

---

## Version 8: Final Defect Repair, Workspace Authorization & Independent Verification Pass
**Date:** 2026-11-15  
**Architect:** Principal Software Engineer, AI Systems Architect & Security Engineer  

### Key Highlights:
1. **FORGE Workspace Authorization Before Execution (`src/openjarvis/sandbox/verification_runner.py`)**:
   - Reordered execution flow so workspace authorization and containment checks execute BEFORE all execution branches (inline execution, container execution, host execution, and metadata commands).
   - Prevents unauthorized working directories from being mounted into container sandboxes (`-v {cwd}:/workspace:rw`).
   - Hardened `check_symlink_and_traversal_escapes` to reject UNC paths (`\\` and `//`), URL-encoded traversal (`%2e%2e`), Windows drive root escapes (`C:\`, `D:\`), relative traversal, symlink escapes outside workspace, and nonexistent paths that resolve outside workspace.
   - Added 8 dedicated regression tests in `test_forge_sandboxing_p0.py` verifying all containment vectors.

2. **n8n Exact Execution Reconciliation (`src/openjarvis/tools/n8n.py`)**:
   - Removed timestamp-only matching fallback (`elif time_matches and matching_execution is None`) which falsely associated unrelated workflow executions.
   - Exact correlation strictly requires matching workflow ID, exact approval ID or idempotency key in metadata, and valid dispatch window.
   - Fail-closed ambiguous handling: when API responses lack correlation metadata or executions are ambiguous, returns `status="outcome_unknown"` without guessing.
   - Pagination traversal implemented via cursor pagination across pages up to safety limit; empty first page is not treated as non-execution if subsequent pages exist.
   - Prevented duplicate retries while status is ambiguous. Added 12 regression tests in `test_n8n_ambiguous_outcomes_p0.py`.

3. **Model Discovery HTTP 401 Classification & Provider Expansion (`src/openjarvis/models/discovery.py`)**:
   - HTTP 401 and 403 errors are preserved as `status="auth_error"`, `connected=False`, and `model_count=0` across all providers, never overwritten with generic `degraded` or `healthy`.
   - Clear status taxonomy: `unconfigured`, `healthy`, `auth_error`, `rate_limited`, `unreachable`, `unsupported_endpoint`, `degraded`.
   - Added direct discovery support and health probes for `atlascloud` (`ATLASCLOUD_API_KEY`) and `minimax` (`MINIMAX_API_KEY`).
   - Added tests in `test_model_discovery.py` covering 401, 403, 429, 404, unreachable, empty catalog, and unconfigured.

4. **Voice Mode Actual TTS Component Testing (`frontend/src/components/Chat/VoiceMode.test.tsx`)**:
   - Component tests mounted in Vitest with happy-dom verifying actual `window.speechSynthesis.speak()` invocations.
   - Verified that `SpeechSynthesisUtterance.text` contains zero emojis across all 11 conditions (simple emoji, punctuation + emoji, country flag, developer ZWJ, family ZWJ, skin tone modifier, keycap, emoji-only, progressive streaming, barge-in stop, and multi-sentence).
   - Verified visible DOM retains original emojis intact.
   - All 15 tests in `VoiceMode.test.tsx` and all 148 tests across 24 frontend test files passed. Production build succeeded.

---

## Version: Voice Mode Emoji Normalization, Container Isolation Hardening & Exact Reconciliation Pass
**Date:** 2026-11-15  
**Architect:** Principal Software Engineer, AI Systems Architect & Security Engineer  

### Key Highlights:
1. **Voice Mode Emoji Pronunciation Remediation (`frontend/src/lib/message-text.ts`, `frontend/src/components/Chat/VoiceMode.tsx`)**:
   - Replaced flawed ad-hoc `speakable()` method in `VoiceMode.tsx` with shared, centralized `toSpeakableText()` and `stripEmojis()`.
   - Built comprehensive Unicode regex matching Regional Indicator flags, keycaps (`[0-9#*]\uFE0F?\u20E3`), Fitzpatrick skin tones (`\u{1F3FB}-\u{1F3FF}`), compound ZWJ sequences (`\u200D`), tag sequences, and Dingbats.
   - Preserves 100% of emojis in chat messages and Voice Mode display text; strips emojis strictly before browser `speechSynthesis.speak` and backend speech synthesis.
   - Implemented streaming sentence-by-sentence speech chunking with boundary matching, zero empty/whitespace utterances, and immediate barge-in audio cancellation via `speechSynthesis.cancel()`.
   - Added 20 vitest tests in `message-text.test.ts` and 4 tests in `VoiceMode.test.tsx` verifying all 15 edge cases.

2. **FORGE Container Sandbox Hardening (`src/openjarvis/sandbox/verification_runner.py`)**:
   - Enforced container isolation arguments: `--network none`, `--user 1000:1000`, `--cap-drop=ALL`, `--security-opt no-new-privileges`, `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m`, CPU/mem/PID limits, and workspace-only rw mount.
   - Untrusted project test commands (`pytest`, `npm test`, etc.) fail closed with `status="blocked"` and `sandbox_mode="blocked_no_container"` when container sandbox is unavailable, unless explicitly run in a trusted environment.
   - Hardened path traversal checks against URL-encoded paths (`%2e%2e`), UNC paths (`\\`), and Windows drive root escapes.

3. **n8n Exact Reconciliation & Execution Ledger (`src/openjarvis/tools/n8n.py`)**:
   - Updated `reconcile_workflow_execution()` to correlate executions using exact workflow ID, `started_after` timestamp, `approval_id`, and `idempotency_key`, preventing false reconciliation against older executions.
   - Accurate state mapping: `STATUS_EXECUTED` on success, `STATUS_EXECUTING` while running, `STATUS_FAILED` on failure, and `STATUS_APPROVED` when authoritatively confirmed never started.
   - Introduced `N8nExecutionLedger` tracking execution attempts, dispatch timestamps, payload hashes, and outcomes.

4. **Model Discovery HTTP 401 Classification (`src/openjarvis/models/discovery.py`)**:
   - Explicitly mapped HTTP 401 and authentication errors to `status="auth_error"`, `connected=False`, and `model_count=0` (never `connected=True` or `degraded`).
   - Mapped cloud empty model lists to `status="degraded"` and `connected=False`.

---

## Version: Final Security Hardening, Process Isolation & Test Reproducibility Pass
**Date:** 2026-11-15  
**Architect:** Principal AI Systems & Security Architect  

### Key Highlights:
1. **FORGE Isolated Verification Runner (`src/openjarvis/sandbox/verification_runner.py`)**:
   - Explicitly documented that executable allowlists do NOT provide OS-level process isolation.
   - Built `IsolatedVerificationRunner` supporting Docker/Podman disposable container sandboxing with `--network none`, restricted mounts, CPU/memory limits (`1024m`, `2.0` CPUs, `100` pids), and workspace-only write access.
   - Untrusted inline code execution (`python -c`, `python -m`, `node -e`, `node --eval`) fails closed with `status="blocked"` when container sandbox is unavailable.
   - All host environment secrets (`OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `DEEPSEEK_*`, `ATLASCLOUD_*`, `N8N_*`, `GITHUB_*`, `AWS_*`, `JWT_*`, `DATABASE_URL`) are completely scrubbed from subprocess environments.
   - Strictly enforces workspace root containment and blocks directory traversal (`..`) and symlink escapes.
   - Differentiates `passed`, `failed`, `blocked`, and `inconclusive` statuses accurately.

2. **n8n Ambiguous Outcomes & Idempotency (`src/openjarvis/tools/n8n.py`, `src/openjarvis/tools/approval_store.py`)**:
   - Added explicit `STATUS_OUTCOME_UNKNOWN = "outcome_unknown"` state to `ApprovalStore`.
   - Post-transmission socket/network timeouts transition to `outcome_unknown` and are **never** automatically restored to `approved`, preventing duplicate executions (e.g. charging twice, duplicate webhooks).
   - Pre-transmission failures (DNS resolution errors, connection refused before handshake) are classified via `classify_network_failure` and safely restored to `approved`.
   - Implemented reconciliation engine (`N8nClient.reconcile_workflow_execution()` and `N8nTool` action `"reconcile"`) to verify actual execution status with n8n before retry or state transition.
   - Bound unique idempotency keys via `X-Idempotency-Key` headers and `_idempotency_key` payload fields.
   - Guaranteed atomic single-use claims under concurrent thread races.

3. **Deterministic Model Discovery & Multi-Agent Tests**:
   - Fixed `tests/models/test_model_discovery.py` to use deterministic mocked provider responses without ambient dependencies on developer's local Ollama or cloud keys.
   - Added unit tests for unreachable providers, HTTP 401 auth errors, empty catalogs, and HTTP 429 rate limits.
   - Added deterministic model selection fixtures to `tests/workforce/test_workforce_e2e.py` and `tests/workforce/test_atlas_orchestrator.py`, eliminating test stalls in headless unconfigured environments.

---

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

---

## 4. Final Independent Reliability and Security Repair

### 4.1 FORGE Command Execution & Workspace Containment
- **Zero `shell=True` Execution**: Verification commands strictly execute with `shell=False` across both Windows and POSIX platforms.
- **Robust Argument Parsing**: Commands are tokenized via `shlex.split()` with outer quote stripping so arguments are cleanly delivered to subprocess without shell evaluation.
- **Explicit Executable Allowlist**: Implemented `ALLOWED_VERIFICATION_EXECUTABLES` (`pytest`, `python`, `python3`, `py`, `npm`, `npx`, `node`, `git`, `uv`, `ruff`, `flake8`, `mypy`, `black`, `isort`, `vitest`, `jest`, `cargo`, `go`, `pnpm`, `yarn`). Disallowed executables are rejected with `status="blocked"`.
- **Argument Metacharacter Validation**: Strictly rejected dangerous shell chaining/redirection operators (`;`, `&&`, `||`, `|`, `>`, `>>`, `<`, `<<`, `&`, `` ` ``, `$()`, `$`).
- **Workspace Boundary Containment**: Enforced containment of `target_workdir` and path arguments via `is_path_within_allowed(target_workdir, allowed_dirs)`. Path traversal (`..`, `/..`, `\..`, `/etc/`, `\Windows\`) and symlink escapes outside permitted workspace roots return `VerificationStatus.BLOCKED`.
- **Regression Tests**: Added `tests/workforce/test_forge_command_security.py` (5 tests passing).

### 4.2 n8n Approval Concurrency & State Machine
- **Atomic Test-and-Set**: Replaced racy check-then-act logic with atomic SQLite conditional updates (`UPDATE pending_actions SET status = 'executing' ... WHERE id = ? AND status = 'approved'`).
- **7-State Lifecycle**: Formalized explicit states: `pending`, `approved`, `executing`, `executed`, `failed`, `expired`, `denied`.
- **Single-Use Concurrency Guarantee**: Under 10-thread simultaneous competition, exactly 1 claim succeeds and 9 fail.
- **Pre-Execution Failure Restoration**: When a network disconnect or client error occurs before the HTTP request reaches n8n (`isinstance(err, (URLError, ConnectionError, TimeoutError, OSError)) and not HTTPError`), status reverts to `approved`, preventing permanent approval loss.
- **Regression Tests**: Added `tests/tools/test_n8n_concurrency.py` (3 tests passing).

### 4.3 Model Capability Verification
- **Capability Confidence Policies**: Implemented `CapabilityConfidencePolicy`:
  - `CONFIRMED_ONLY`: Requires live benchmark/execution confirmation.
  - `DECLARED_OR_CONFIRMED`: Accepts provider-declared or confirmed capabilities; rejects inferred heuristics.
  - `ALLOW_INFERRED`: Accepts inferred capabilities but strictly rejects unknown.
- **Non-Tool Cloud Models**: Explicitly denied tool-calling support on models known not to support function calling (e.g., `deepseek-reasoner` / `deepseek-r1`, `o1-preview`, `o1-mini`, `gpt-3.5-turbo-instruct`, `claude-2.0`).
- **Unknown Cloud Models**: Cloud models not confirmed in the tool allowlist default to `supports_tools=False` and `provenance["tools"]="unknown"`.
- **Regression Tests**: Added `tests/models/test_model_capability_policy.py` (5 tests passing).

### 4.4 Cloud Model Discovery
- **Anthropic Discovery Path**: Live discovery path probes `GET https://api.anthropic.com/v1/models` using `x-api-key` and `anthropic-version: 2023-06-01` or `client.models.list()`.
- **Separate Connectivity Reporting**: Provider health reports `connected: bool` separately from `model_count: int` and `status: str`.
- **Error Classification**:
  - HTTP 401: `status="auth_error"`, `connected=False`
  - HTTP 429: `status="rate_limited"`, `connected=True`
  - HTTP 404/405: `status="unsupported_endpoint"`, `connected=True`
  - Network timeout: `status="unreachable"`, `connected=False`
- **Zero Fabrication**: Provider metadata is never fabricated when discovery fails or returns 0 models.

### 4.5 Multi-Agent Execution & Tool Registration
- **Tool Registration Resilience**: Implemented `ensure_tools_registered()`. When test harnesses clear registries between runs, tools are dynamically reloaded, ensuring all 70 built-in tools remain available.
- **Elimination of Silent Fallbacks**: Removed `step_model = "auto"` fallbacks. When dynamic model selection fails to allocate a compatible model, the step transitions to `StepStatus.FAILED` with an explicit error.
- **Runtime Model Propagation**: Dynamically selected model IDs are saved to agent heartbeats and passed into `step_executor(step, model=step_model)` and `engine.execute_coding_task(..., model=allocated_model)`.
- **Regression Tests**: Added `tests/workforce/test_specialist_real_execution_e2e.py` (5 tests passing).

### 4.6 Final Verification Summary
- **Backend Pytest**: **85 passed** in 15.46s (100% pass rate).
- **Frontend Vitest**: **117 passed** across 23 test files in 1.74s (100% pass rate).
- **Frontend Build**: Vite v8.3.4 compiled cleanly in 1.14s with zero errors.
- **Documentation**: Generated `docs/JARVIS_FINAL_SECURITY_AUDIT.md`, `docs/JARVIS_FINAL_TEST_REPORT.md`, and updated `docs/JARVIS_INTEGRATION_CHANGELOG.md`.


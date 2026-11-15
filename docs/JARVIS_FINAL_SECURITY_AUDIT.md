# Jarvis V1 — Final Independent Reliability and Security Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Security Architect  
**Target Repository:** Jarvis V1 (`oprn-jarvis`)  
**Scope:** Source-level verification and remediation of FORGE command execution, n8n approval concurrency, model capability policies, cloud provider discovery, and multi-agent DAG execution.

---

## 1. Executive Summary

An independent, rigorous source-level audit of the Jarvis V1 repository was conducted. Previous assumptions and reports were verified directly against code implementations and test suites.

All identified vulnerabilities, concurrency races, and hardcoded fallbacks have been resolved:
1. **FORGE Command Security:** Completely eliminated `shell=True` on all platforms. Verification commands are parsed into token vectors, validated against an explicit executable allowlist (`pytest`, `python`, `npm`, `vitest`, `git`, etc.), checked for dangerous shell chaining/redirection characters, and strictly contained within configured workspace roots with symlink resolution.
2. **n8n Approval Concurrency:** Replaced racy check-then-act logic with atomic SQLite conditional updates (`UPDATE pending_actions SET status = 'executing' ... WHERE id = ? AND status = 'approved'`). Introduced 7 explicit states (`pending`, `approved`, `executing`, `executed`, `failed`, `expired`, `denied`). Guaranteed single-use semantics and pre-execution failure restoration to prevent approval loss during transient network disconnects.
3. **Model Capability Verification:** Enforced strict separation across `confirmed`, `provider_declared`, `inferred`, and `unknown` capability provenances. Removed the assumption that every cloud model supports tools; explicitly flagged models like `deepseek-reasoner` (R1) as non-tool models. Implemented configurable `CapabilityConfidencePolicy` (`CONFIRMED_ONLY`, `DECLARED_OR_CONFIRMED`, `ALLOW_INFERRED`).
4. **Cloud Model Discovery:** Implemented the Anthropic live model discovery path (`GET https://api.anthropic.com/v1/models` / SDK fallback). Distinctly separated provider connectivity (`connected: bool`) from model availability (`model_count`). Handled authentication errors (401), rate limits (429), and unsupported endpoints without fabricating provider metadata.
5. **Multi-Agent Execution Integrity:** Solved ToolRegistry clearing during test cycles and cold imports via `ensure_tools_registered()`, ensuring all 70 built-in tools (`file_read`, `file_write`, `knowledge_search`, `n8n`, etc.) are consistently available. Completely removed silent `"auto"` model fallbacks in planning and execution; failures now transition steps to explicit `FAILED` state.

---

## 2. In-Depth Technical Verification

### 2.1 FORGE Command Execution & Containment (`src/openjarvis/workforce/forge_coding.py`)
- **Zero `shell=True` Execution:** Verification commands are parsed into `tokens` via `shlex.split(clean_cmd, posix=(sys.platform != "win32"))` with outer quote stripping. `subprocess.run([resolved_exec, *tokens[1:]], shell=False, ...)` is strictly used across Windows, macOS, and Linux.
- **Explicit Executable Allowlist:** Commands must stem from `ALLOWED_VERIFICATION_EXECUTABLES` (`pytest`, `python`, `python3`, `py`, `npm`, `npx`, `node`, `git`, `uv`, `ruff`, `flake8`, `mypy`, `black`, `isort`, `vitest`, `jest`, `cargo`, `go`, `pnpm`, `yarn`). Disallowed binaries (`bash`, `powershell`, `cmd`, `curl`, `wget`, `whoami`) are rejected with `status="blocked"`.
- **Dangerous Metacharacter Validation:** Rejects shell chaining and redirection operators (`;`, `&&`, `||`, `|`, `>`, `>>`, `<`, `<<`, `&`, `` ` ``, `$()`, `$`).
- **Workspace Boundary Containment:** Validates `target_workdir` and command path arguments against `is_path_within_allowed(path, allowed_dirs)`. Path traversal (`..`, `/..`, `\..`, `/etc/`, `\Windows\`) and symlink escapes outside permitted workspace roots return `VerificationStatus.BLOCKED`.

### 2.2 n8n Approval Concurrency (`src/openjarvis/tools/approval_store.py` & `n8n.py`)
- **Atomic Test-and-Set:** Claims execute via:
  ```sql
  UPDATE pending_actions 
  SET status = 'executing', decision_at = ? 
  WHERE id = ? AND status = 'approved'
  ```
  If `cur.rowcount == 0`, concurrent claims fail immediately. Verified under 10-thread simultaneous competition: exactly 1 claim succeeds and 9 fail.
- **7-State Lifecycle:** Actions transition through `STATUS_PENDING`, `STATUS_APPROVED`, `STATUS_EXECUTING`, `STATUS_EXECUTED`, `STATUS_FAILED`, `STATUS_EXPIRED`, `STATUS_DENIED`.
- **Pre-Execution Failure Restoration:** When a network disconnect or client error occurs before the HTTP request reaches n8n (`isinstance(err, (URLError, ConnectionError, TimeoutError, OSError)) and not HTTPError`), `complete_approval_execution` resets the status back to `STATUS_APPROVED`. Approvals are never permanently lost due to transient transport errors.
- **Cryptographic Binding:** Each approval is bound to `f"n8n:{action_type}:{target}:{canonical_payload_hash}"`. Altering workflow IDs, webhook paths, or payload fields invalidates the approval.

### 2.3 Model Capability Verification (`src/openjarvis/models/capabilities.py`)
- **Capability Provenance:** Attributes every capability to `CONFIRMED`, `PROVIDER_DECLARED`, `INFERRED`, or `UNKNOWN`.
- **Non-Tool Cloud Models:** Explicitly denies tool-calling support on models known not to support function calling (e.g., `deepseek-reasoner` / `deepseek-r1`, `o1-preview`, `o1-mini`, `gpt-3.5-turbo-instruct`, `claude-2.0`). Unknown cloud models default to `supports_tools=False` and `provenance["tools"]="unknown"`.
- **Configurable Confidence Policy:**
  - `CONFIRMED_ONLY`: Rejects declared and inferred capabilities; requires live benchmark verification.
  - `DECLARED_OR_CONFIRMED`: Accepts declared or confirmed capabilities; rejects inferred heuristics.
  - `ALLOW_INFERRED`: Accepts inferred capabilities but strictly rejects unknown.
  - Unknown capabilities (`prov == "unknown"`) are rejected under all policies.

### 2.4 Cloud Model Discovery (`src/openjarvis/models/discovery.py`)
- **Anthropic Discovery Path:** Live discovery path probes `GET https://api.anthropic.com/v1/models` using `x-api-key` and `anthropic-version: 2023-06-01` or `client.models.list()`.
- **Separate Connectivity Reporting:** Health record returns `connected: bool` separate from `model_count: int` and `status: str`.
  - HTTP 401: `status="auth_error"`, `connected=False`
  - HTTP 429: `status="rate_limited"`, `connected=True`
  - HTTP 404/405: `status="unsupported_endpoint"`, `connected=True`
  - Network timeout: `status="unreachable"`, `connected=False`
- **Zero Fabrication:** Never reports models as healthy merely because client libraries are installed; empty or failing responses return 0 models.

### 2.5 Multi-Agent Execution Integrity (`src/openjarvis/workforce/orchestrator.py`)
- **Tool Registration Resilience:** Implemented `ensure_tools_registered()`. When test harnesses clear registries between runs, tools are dynamically reloaded, ensuring all 70 built-in tools remain available.
- **Elimination of Silent Fallbacks:** Removed `step_model = "auto"` fallbacks. When dynamic model selection fails to allocate a compatible model, the step transitions to `StepStatus.FAILED` with a descriptive error.
- **Runtime Model Propagation:** Dynamically selected model IDs are saved to agent heartbeats and passed into `step_executor(step, model=step_model)` and `engine.execute_coding_task(..., model=allocated_model)`.

---

## 3. Regression Test Coverage

| Test Module | Coverage Focus | Test Count | Status |
|---|---|---|---|
| `tests/workforce/test_forge_command_security.py` | Shell=False, allowlist, arguments, traversal | 5 | PASSED |
| `tests/workforce/test_forge_security_p0.py` | Workspace locking, dirty file diffs, test status | 7 | PASSED |
| `tests/workforce/test_specialist_real_execution_e2e.py` | Tool audit, real specialist execution, no auto fallback | 5 | PASSED |
| `tests/tools/test_n8n_concurrency.py` | 10-thread race, state transitions, pre-exec rollback | 3 | PASSED |
| `tests/tools/test_n8n_security_p0.py` | SSRF, path sanitization, approval hash binding | 11 | PASSED |
| `tests/models/test_model_capability_policy.py` | DeepSeek R1 non-tool, confidence policies, Anthropic | 5 | PASSED |
| `tests/models/test_model_selection_p0.py` | P0 model selection, privacy fail-closed, normalization | 10 | PASSED |
| Full backend suite | Workforce, Models, Tools | 85 | PASSED |
| Frontend Vitest | Store, API, Auth, Telemetry, Capabilities | 117 | PASSED |
| Frontend Build | Production bundle & assets (`npm run build`) | All chunks | SUCCESS |

---

## 4. Final Security Sign-off

Jarvis V1 has met all critical reliability, concurrency, capability enforcement, and sandboxing requirements. All execution gates are fail-closed, verifiable, and protected against unauthorized traversal and concurrency races.

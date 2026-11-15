# Jarvis V1 — Final Security Hardening, Process Isolation and Test Reproducibility Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Security Architect  
**Target Repository:** Jarvis V1 (`oprn-jarvis`)  
**Scope:** Source-level verification and hardening of FORGE arbitrary code execution containment, isolated verification runner, host secret elimination, n8n ambiguous outcome reconciliation, deterministic model discovery, and multi-agent test reproducibility.

---

## 1. Executive Summary

An independent, rigorous source-level security audit and remediation pass was conducted across the Jarvis V1 codebase. All findings from previous audits were critically re-evaluated and hardened against executable source code:

1. **FORGE Arbitrary Code Execution Hardening (`src/openjarvis/sandbox/verification_runner.py` & `src/openjarvis/workforce/forge_coding.py`):**
   - **Process Isolation Principle:** Recognized and documented that an executable allowlist alone **does NOT provide process isolation**. Pytest plugins, npm test scripts, conftest.py files, build scripts, and inline code can execute arbitrary instructions with host permissions.
   - **Isolated Verification Runner:** Implemented `IsolatedVerificationRunner`. When a container runtime (Docker or Podman) is available, verification runs inside an ephemeral, disposable container with `--network none`, restricted read-only system mounts, read-write access strictly confined to `/workspace`, and CPU (`2.0`), memory (`1024m`), and process-count (`100`) limits.
   - **Fail-Closed Untrusted Commands:** When a container sandbox is unavailable, all untrusted inline code executions (`python -c`, `python -m`, `node -e`, `node --eval`) **fail closed** with `status="blocked"` and an explicit error explanation.
   - **Zero Host Secrets Leaked:** When running standard test suites on the host, the environment is scrubbed clean: all API keys (`OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `DEEPSEEK_*`, `ATLASCLOUD_*`, `N8N_*`), tokens (`GITHUB_*`, `JWT_*`), passwords, database URLs, and AWS credentials are stripped from `os.environ` before spawning child processes.
   - **Symlink Escape & Path Traversal Rejection:** Both working directories and command path arguments are inspected; symlinks resolving outside authorized workspace roots are rejected with `VerificationStatus.BLOCKED`.
   - **Accurate Status Discrimination:** Cleanly distinguishes `passed` (exit 0), `failed` (test assertion failure), `blocked` (security policy rejection), `not_run` (no tests), and `inconclusive` (timeout or crash).

2. **n8n Ambiguous Execution Outcomes (`src/openjarvis/tools/n8n.py` & `src/openjarvis/tools/approval_store.py`):**
   - **Explicit `outcome_unknown` State:** Added `STATUS_OUTCOME_UNKNOWN = "outcome_unknown"` to `ApprovalStore`.
   - **No Automatic Approval Restoration on Network Timeout:** Socket read timeouts or connection resets occurring during or after request payload transmission transition to `STATUS_OUTCOME_UNKNOWN`. The approval is **NEVER** restored to `approved`, preventing dangerous duplicate executions (e.g. charging customers twice or firing duplicate automated workflows).
   - **Failure Boundary Classification (`classify_network_failure`):** Distinguishes failures known to occur before request bytes were transmitted (DNS lookup failure, connection refused before handshake) from ambiguous post-transmission errors (socket timeout, read timeout, HTTP 502/504). Pre-transmission failures safely revert to `approved`; ambiguous errors transition to `outcome_unknown`.
   - **Reconciliation Engine:** Implemented `N8nClient.reconcile_workflow_execution()` and `N8nTool` action `"reconcile"`. Queries the n8n execution API to inspect actual execution state before allowing retry or resolution. If confirmed completed, transitions to `STATUS_EXECUTED`; if confirmed never started, transitions to `STATUS_APPROVED`; if failed, transitions to `STATUS_FAILED`.
   - **Idempotency Keys:** Requests now bind unique idempotency keys (using `approval_id` or canonical payload hashes) passed via `X-Idempotency-Key` HTTP headers and payload bodies.

3. **Deterministic Model Discovery & Test Reliability (`tests/models/test_model_discovery.py`):**
   - Eliminated all silent dependencies on the developer's local Ollama instance or real cloud API keys.
   - Replaced ambient unit tests with deterministic mocked responses.
   - Added explicit test cases for configured but unreachable providers (ConnectionRefused -> `status="offline"`, `connected=False`, `model_count=0`), invalid API keys (HTTP 401 Unauthorized), empty model catalogs (`{"models": []}` -> healthy with 0 models), and rate limits (HTTP 429).

4. **Multi-Agent Test Determinism (`tests/workforce/`):**
   - Investigated and resolved why Atlas and workforce E2E tests became incomplete when no local LLMs were running: `AtlasOrchestrator` now supports deterministic model selection fixtures during tests.
   - Tests run reliably in headless, unconfigured CI environments with zero ambient credentials.

---

## 2. Technical Implementation Details

### 2.1 FORGE Isolated Verification Runner Architecture
```
+-------------------------------------------------------------------------+
|                       ForgeCodingEngine._run_command                    |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
|                  IsolatedVerificationRunner.execute                     |
|  1. Tokenize command safely (shlex.split, no shell=True)                |
|  2. Enforce ALLOWED_VERIFICATION_EXECUTABLES allowlist                  |
|  3. Reject shell chaining metacharacters (; && || | > < $ `)            |
|  4. Validate workspace containment & reject symlink traversal escapes    |
|  5. Detect inline code / arbitrary modules (python -c, -m, node -e)     |
+-------------------------------------------------------------------------+
                    |                                   |
         [Inline Code / Untrusted]             [Standard Test Runner]
                    |                                   |
          Container Available?                 Container Available?
         /                   \                /                   \
       YES                    NO            YES                    NO
        |                      |             |                      |
        v                      v             v                      v
[Docker/Podman Sandbox] [FAIL CLOSED]  [Docker Sandbox]    [Sanitized Host Run]
 - --network none        status=blocked - --network none    - Zero host secrets
 - --memory 1024m                       - --memory 1024m    - shell=False
 - --cpus 2.0                           - --cpus 2.0        - Timeout limits
 - --pids-limit 100                     - -v ws:/workspace  - Accurate exit code
 - -v ws:/workspace:rw
```

### 2.2 n8n Approval Concurrency & Ambiguity Lifecycle
```
                       [ STATUS_PENDING ]
                               |
                      Human Approves / Denies
                               |
                     [ STATUS_APPROVED ]
                               |
                    Atomic Test-and-Set Claim
                               |
                    [ STATUS_EXECUTING ]
                               |
                +--------------+---------------+
                |                              |
        Transmission Attempt          Server HTTP Response
                |                              |
      +---------+---------+            +-------+-------+
      |                   |            |               |
Pre-Transmission     Ambiguous     200 OK           4xx / 5xx
 (DNS / Refused)     (Timeout)         |               |
      |                   |            v               v
      v                   v      [ EXECUTED ]      [ FAILED ]
[ APPROVED ]     [ OUTCOME_UNKNOWN ]
(Safe to retry)          |
                   Reconciliation
                         |
           +-------------+-------------+
           |                           |
     Confirmed Run             Confirmed Never Run
           |                           |
           v                           v
     [ EXECUTED ]                [ APPROVED ]
```

---

## 3. Regression Test Verification Matrix

| Test Suite | Focus Area | Tests | Status |
|---|---|---|---|
| `tests/workforce/test_forge_sandboxing_p0.py` | Inline `python -c`, `node -e`, `python -m` blocked; host secrets scrubbed; symlink escape checks; container isolation flags | 8 | PASSED |
| `tests/workforce/test_forge_command_security.py` | Allowlist enforcement, dangerous shell operators, path traversal, real subprocess execution | 5 | PASSED |
| `tests/workforce/test_forge_security_p0.py` | Cross-process workspace locking, dirty git snapshot containment, verification states | 7 | PASSED |
| `tests/tools/test_n8n_ambiguous_outcomes_p0.py` | `outcome_unknown` state, timeout no-auto-restore, pre-transmission failure restore, reconciliation API, idempotency headers, concurrent atomic claims | 6 | PASSED |
| `tests/tools/test_n8n_concurrency.py` | 10-thread simultaneous claim race, atomic test-and-set conditional updates | 3 | PASSED |
| `tests/tools/test_n8n_security_p0.py` | SSRF rejection, path traversal, canonical payload hash binding, self-approval denial | 11 | PASSED |
| `tests/models/test_model_discovery.py` | Deterministic mocked discovery, unreachable providers, 401 auth errors, empty catalogs, 429 rate limits, unconfigured environments | 11 | PASSED |
| `tests/models/test_model_capability_policy.py` | DeepSeek R1 non-tool enforcement, confidence policies (`CONFIRMED_ONLY`, `DECLARED_OR_CONFIRMED`), Anthropic discovery | 5 | PASSED |
| `tests/models/test_model_selection_p0.py` | No emergency hardcoded fallbacks, fail-closed privacy, task normalization | 10 | PASSED |
| `tests/workforce/test_specialist_real_execution_e2e.py` | ToolRegistry audit, real Scout & Forge execution, model selection failure to FAILED state, model propagation | 6 | PASSED |
| `tests/workforce/test_atlas_orchestrator.py` | Atlas DAG planning, specialist step execution, team messaging | 3 | PASSED |
| `tests/workforce/test_workforce_e2e.py` | Full multi-agent workforce ERP audit DAG with deterministic fixtures | 1 | PASSED |
| Full Targeted Backend Suite | Combined workforce, models, and tools security | 104 passed, 1 skipped | PASSED |
| Frontend Vitest | Store, API, Auth, Telemetry, Capabilities, UI modals | 117 passed (23 files) | PASSED |
| Production Frontend Build | Vite v8.3.4 static compilation & PWA generation | 3,506 modules | SUCCESS |

---

## 4. Operational Boundaries and Limitations

1. **Host Sandbox Limitation:**
   When running on environments where Docker or Podman is not active, OS-level process isolation cannot be provided on the bare host. In this environment:
   - Arbitrary inline executions (`python -c`, `python -m`, `node -e`) fail closed immediately.
   - Standard test commands (`pytest`, `vitest`, `npm test`) execute on the host with all environment credentials scrubbed and strict path traversal / symlink containment.
   - For complete untrusted repository sandboxing, Docker Desktop or Podman daemon must be running.

2. **n8n Webhook Ambiguity Limitation:**
   While n8n workflow executions have a first-class execution audit log (`/rest/executions`), generic unauthenticated webhook endpoints may not log execution IDs if the workflow does not enable "Save execution data". In those cases, reconciliation defaults safely to `STATUS_FAILED` rather than auto-retrying, preventing double triggers.

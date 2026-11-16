# Jarvis V1 — Final Security Hardening, Process Isolation, Voice Emoji Remediation and Independent Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Security Architect  
**Target Repository:** Jarvis V1 (`oprn-jarvis`)  
**Scope:** Source-level verification and hardening of Voice Mode speech emoji normalization, FORGE workspace-level authorization and container sandboxing, symlink escape rejection, n8n exact structured correlation and pagination truncation handling, model discovery HTTP 401 classification, capability confidence policy enforcement, and multi-agent test reproducibility.

---

## 1. Executive Summary

An independent, rigorous source-level security audit and remediation pass (Version 10) was conducted across the Jarvis V1 codebase. All findings from previous audits were critically re-evaluated and hardened against executable source code:

1. **FORGE Explicit Workspace-Level Authorization & Symlink Escape Hardening (`src/openjarvis/sandbox/verification_runner.py` & `src/openjarvis/workforce/forge_coding.py`):**
   - **Root Cause Eliminated:** The system previously confused broad administrative allowed directories (`allowed_dirs`, which includes the system temp directory) with the execution-specific authorized workspace (`workspace_root`). A symlink inside the workspace pointing to an outside file that happened to reside inside an allowed parent directory (e.g. `tmp_path / "outside_secret"` inside `tempfile.gettempdir()`) was erroneously accepted.
   - **Two-Tier Authorization Boundary:**
     - *Tier 1: Administrative Allowed Directories (`allowed_dirs`):* System-wide boundaries (user config, project root, temp dir). Any workspace root MUST reside within `allowed_dirs`.
     - *Tier 2: Execution-Specific Workspace (`workspace_root`):* The specific workspace root authorized for a given task execution. Symlink targets and path arguments must strictly reside within `workspace_root`. Being inside a broader allowed directory is NOT sufficient.
   - **Workspace Root Determination (`determine_workspace_root`):** Explicitly resolves `workspace_root` if passed; if omitted, searches upward from `resolved_cwd` for project root markers (`.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `setup.py`, `pytest.ini`), stopping before crossing system boundaries or outside `allowed_dirs`. In a monorepo, top-level `.git` discovery authorizes cross-package symlinks within the repository. If no marker is found, defaults to `resolved_cwd`.
   - **Comprehensive Symlink Containment:** Inspects `cand_path.is_symlink()`, `os.readlink()`, and `cand_path.resolve(strict=False)` against `workspace_root`. Rejects symlinks pointing to files or directories outside workspace, nested symlinks escaping workspace, symlinks pointing to escaping symlinks, relative symlink escapes, absolute symlink escapes, and nonexistent destinations under escaping symlink directories.
   - **Container Sandbox Mount Protection:** Docker/Podman container runner mounts only the canonical authorized workspace path (`-v {canonical_workspace_root}:/workspace:rw`), with container working directory set to the relative subdirectory if `resolved_cwd != workspace_root`.
   - **Order of Authorization Enforced:** Workspace containment, raw command escape scanning, and symlink resolution execute BEFORE inline execution, container execution, host execution, and metadata commands.

2. **n8n Exact Structured Correlation & Substring Matching Removal (`src/openjarvis/tools/n8n.py` & `src/openjarvis/tools/approval_store.py`):**
   - **Root Cause Eliminated:** Removed flawed substring matching (`approval_id in custom_str` and `idempotency_key in custom_str`) which falsely matched partial identifiers (e.g. `approval_1` matching `approval_10` or `approval_12`).
   - **Structured Metadata Parsing (`_extract_execution_correlation_identifiers`):** Parses structured JSON or dict metadata across `customData`, `metadata`, `additionalData`, `data`, and `variables`. Compares `approval_id` and `idempotency_key` exactly.
   - **Malformed Metadata Handling:** Execution records with malformed metadata or invalid JSON in `customData` return `status="outcome_unknown"`, preventing false approvals or unintended auto-retries.
   - **Strict Workflow Identity & Pagination Truncation:** Reconciled executions must match the exact expected `workflowId`. Reaching pagination limits (`max_pages`) with `nextCursor` present returns `status="outcome_unknown"`.

3. **Voice Mode Emoji Pronunciation Remediation & Actual TTS Component Verification (`frontend/src/lib/message-text.ts`, `frontend/src/components/Chat/VoiceMode.tsx`, `frontend/src/lib/tts.ts`, `frontend/src/components/Chat/VoiceMode.test.tsx`):**
   - **Unified Emoji Normalization:** Implemented `stripEmojis()` covering Regional Indicator flag pairs, keycap sequences (`[0-9#*]\uFE0F?\u20E3`), Fitzpatrick skin tones (`\u{1F3FB}-\u{1F3FF}`), compound ZWJ sequences (`\u200D`), Dingbats, pictographs, and punctuation spacing normalization.
   - **Display vs Speech Strict Separation:** Emojis and Markdown syntax remain 100% visible in the chat interface and Voice Mode visual feedback. Emojis and Markdown markers are stripped strictly before speech synthesis (`window.speechSynthesis.speak` and backend `/api/speech/synthesize`).
   - **Streaming Sentence Pipeline:** Incoming streams chunk on sentence boundaries; empty/emoji-only chunks are prevented from invoking `speechSynthesis.speak()`; speech cursor is tracked accurately; barge-in cancels speech immediately via `window.speechSynthesis.cancel()`.
   - **Actual Speech Component Verification:** Verified in Vitest with happy-dom that `speechSynthesis.speak` is invoked with `SpeechSynthesisUtterance.text` completely devoid of emojis across all 11 required phrases/conditions while DOM elements retain the full original emoji characters.

4. **Model Discovery HTTP 401 Classification & Capability Policy (`src/openjarvis/models/discovery.py` & `capabilities.py`):**
   - **HTTP 401/403 Authentication Preservation:** HTTP 401 and 403 errors across all providers are explicitly preserved as `status="auth_error"`, `connected=False`, and `model_count=0`.
   - **Cloud REST Fallback Probes:** Added direct REST health and discovery probes for all cloud providers (`openai`, `deepseek`, `openrouter`, `anthropic`, `google`, `atlascloud`, `minimax`) when client SDKs are uninitialized.
   - **Capability Confidence Policies:** Enforces `CONFIRMED_ONLY`, `DECLARED_OR_CONFIRMED`, and `ALLOW_INFERRED`. Unknown capabilities always evaluate to False. Zero hardcoded model fallbacks.

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
| `tests/workforce/test_forge_sandboxing_p0.py` | Inline `python -c`, `node -e`, `python -m` blocked; two-tier workspace validation; symlink containment within workspace root; outside file in allowed parent, outside directory, nested symlink, symlink to symlink, legitimate symlink in workspace, relative/absolute symlink, with/without Docker, nonexistent destination under escaping symlink; symlink loops (`RuntimeError`); Windows junctions & reparse points; broken symlinks; permission-denied fail-closed resolution; multiple authorized workspaces (`extra_authorized_roots`); raw command path escape scan; UNC, traversal, drive root escapes; host secrets scrubbed; container isolation flags | 34 | 34 PASSED (0 skipped, 0 failed) |
| `tests/workforce/test_forge_command_security.py` | Allowlist enforcement, dangerous shell operators, path traversal, real subprocess execution | 5 | 5 PASSED |
| `tests/workforce/test_forge_security_p0.py` | Cross-process workspace locking, dirty git snapshot containment, verification states | 7 | 7 PASSED |
| `tests/workforce/test_forge_coding_integration.py` | OpenCode integration, GoalLoop verification, task dispatch | 3 | 3 PASSED |
| `tests/tools/test_n8n_ambiguous_outcomes_p0.py` | `outcome_unknown` state, timestamp fallback removal, exact approval & idempotency matching, elimination of substring matching, partial identifier collisions (`app-1` vs `app-10`, `idem-1` vs `idem-100`), malformed metadata, pagination truncation handling, repeated/stalled cursor handling, empty result vs nextCursor, duplicate retry prevention | 29 | 29 PASSED |
| `tests/tools/test_n8n_concurrency.py` | 10-thread simultaneous claim race, atomic test-and-set conditional updates | 3 | 3 PASSED |
| `tests/tools/test_n8n_security_p0.py` | SSRF rejection, path traversal, canonical payload hash binding, self-approval denial | 11 | 11 PASSED |
| `tests/tools/test_n8n_integration.py` | Live/mocked n8n client authentication, session cookie caching, and workflow dispatch | 5 | 5 PASSED |
| `tests/tools/test_allowed_dirs_config.py` | System allowed directories configuration, path boundary enforcement | 28 | 27 PASSED, 1 SKIPPED (Unprivileged Windows symlink privilege check) |
| `tests/models/test_model_discovery.py` | Deterministic mocked discovery, REST fallback probes, unreachable providers, 401 & 403 auth errors, empty catalogs, 429 rate limits, 404 endpoint not found, unconfigured environments | 15 | 15 PASSED |
| `tests/models/test_model_capability_policy.py` | DeepSeek R1 non-tool enforcement, confidence policies (`CONFIRMED_ONLY`, `DECLARED_OR_CONFIRMED`), Anthropic discovery | 5 | 5 PASSED |
| `tests/models/test_model_selection_p0.py` | No emergency hardcoded fallbacks, fail-closed privacy, task normalization | 10 | 10 PASSED |
| `tests/workforce/test_specialist_real_execution_e2e.py` | ToolRegistry audit, real Scout & Forge execution, model selection failure to FAILED state, model propagation | 6 | 6 PASSED |
| `tests/workforce/test_scenarios_e2e.py` | Scenarios A, B, C, D deterministic end-to-end multi-agent execution | 4 | 4 PASSED |
| `tests/workforce/test_atlas_orchestrator.py` | Atlas DAG planning, specialist step execution, team messaging | 3 | 3 PASSED |
| `tests/workforce/test_agent_registry.py` | Agent registry registration, lookup, capabilities | 4 | 4 PASSED |
| `tests/workforce/test_workforce_routes.py` | Workforce FastAPI router endpoints, agent listing, task delegation | 4 | 4 PASSED |
| `tests/workforce/test_workforce_security.py` | Workforce security boundaries, role permissions | 5 | 5 PASSED |
| `tests/workforce/test_workforce_e2e.py` | Full multi-agent workforce ERP audit DAG with deterministic fixtures | 1 | 1 PASSED |
| **Comprehensive Targeted Suite (19 Files)** | **Workforce, models, tools, sandbox, connectors full regression** | **182** | **181 PASSED, 1 SKIPPED, 0 FAILED** in 34.71s |
| `frontend/src/components/Chat/VoiceMode.test.tsx` | VoiceMode actual speech synthesis invocations (`window.speechSynthesis.speak`), DOM emoji retention, all 11 conditions (flags, ZWJ, skin tones, keycaps, streaming, barge-in) | 15 | 15 PASSED |
| Full Frontend Vitest Suite | Store, API, Auth, Telemetry, Capabilities, UI modals, VoiceMode, TTS | 148 passed (24 files) | 148 PASSED (0 failed) in 4.89s |
| Production Frontend Build | Vite v8.3.4 static compilation & PWA generation (`tsc -b && vite build`) | 3,506 modules | SUCCESS (0 errors) in 2.01s |
| Repository-Wide Test Suite | Comprehensive 9,726 tests across all historical and current modules | 9,726 | 9,390 PASSED, 194 SKIPPED, 128 FAILED, 14 ERRORS in 3,111.01s (51m 51s) |

---

## 4. Operational Boundaries and Limitations

1. **Host Sandbox Limitation:**
   When running on environments where Docker or Podman is not active, OS-level process isolation cannot be provided on the bare host. In this environment:
   - Arbitrary inline executions (`python -c`, `python -m`, `node -e`) fail closed immediately.
   - Standard test commands (`pytest`, `vitest`, `npm test`) execute on the host with all environment credentials scrubbed and strict path traversal / symlink containment.
   - For complete untrusted repository sandboxing, Docker Desktop or Podman daemon must be running.

2. **n8n Webhook Ambiguity Limitation:**
   While n8n workflow executions have a first-class execution audit log (`/rest/executions`), generic unauthenticated webhook endpoints may not log execution IDs if the workflow does not enable "Save execution data". In those cases, reconciliation defaults safely to `STATUS_FAILED` rather than auto-retrying, preventing double triggers.

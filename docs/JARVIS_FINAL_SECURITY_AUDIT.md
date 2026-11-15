# Jarvis V1 — Final Security Hardening, Process Isolation, Voice Emoji Remediation and Independent Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Security Architect  
**Target Repository:** Jarvis V1 (`oprn-jarvis`)  
**Scope:** Source-level verification and hardening of Voice Mode speech emoji normalization, FORGE arbitrary code execution containment and container sandboxing, n8n ambiguous outcome reconciliation and execution ledger, model discovery HTTP 401 classification, capability confidence policy enforcement, and multi-agent test reproducibility.

---

## 1. Executive Summary

An independent, rigorous source-level security audit and remediation pass was conducted across the Jarvis V1 codebase. All findings from previous audits were critically re-evaluated and hardened against executable source code:

1. **Voice Mode Emoji Pronunciation Remediation (`frontend/src/lib/message-text.ts`, `frontend/src/components/Chat/VoiceMode.tsx`, `frontend/src/lib/tts.ts`):**
   - **Root Cause Eliminated:** `VoiceMode.tsx` previously implemented a private, duplicate `speakable()` function that omitted emoji stripping entirely, while `message-text.ts` lacked support for keycaps, regional flags, and Fitzpatrick skin tones.
   - **Unified Emoji Normalization:** Implemented `stripEmojis()` covering Regional Indicator flag pairs, keycap sequences (`[0-9#*]\uFE0F?\u20E3`), Fitzpatrick skin tones (`\u{1F3FB}-\u{1F3FF}`), compound ZWJ sequences (`\u200D`), Dingbats, pictographs, and punctuation spacing normalization.
   - **Display vs Speech Strict Separation:** Emojis and Markdown syntax remain 100% visible in the chat interface and Voice Mode visual feedback. Emojis and Markdown markers are stripped strictly before speech synthesis (`window.speechSynthesis.speak` and backend `/api/speech/synthesize`).
   - **Streaming Sentence Pipeline:** Incoming streams chunk on sentence boundaries; empty/emoji-only chunks are prevented from invoking `speechSynthesis.speak()`; speech cursor is tracked accurately; barge-in cancels speech immediately via `window.speechSynthesis.cancel()`.

2. **FORGE Arbitrary Code Execution Hardening (`src/openjarvis/sandbox/verification_runner.py` & `src/openjarvis/workforce/forge_coding.py`):**
   - **Process Isolation Principle:** Recognized that an executable allowlist alone does NOT provide process isolation against untrusted code in `conftest.py`, pytest plugins, or npm scripts.
   - **Container Isolation Hardening:** Ephemeral container sandbox enforces `--network none`, `--user 1000:1000`, `--cap-drop=ALL`, `--security-opt no-new-privileges`, `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m`, CPU (`2.0`), memory (`1024m`), and process-count (`100`) limits.
   - **Fail-Closed Untrusted Commands:** When container sandbox is unavailable, untrusted project test suites and inline scripts fail closed with `status="blocked"` and `sandbox_mode="blocked_no_container"`.
   - **Zero Host Secrets Leaked:** Environment is scrubbed clean of all credentials (`OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `DEEPSEEK_*`, `ATLASCLOUD_*`, `N8N_*`, tokens, passwords, database URLs).
   - **Symlink Escape & Path Traversal Rejection:** Both working directories and command path arguments are inspected; URL-encoded traversal (`%2e%2e`), UNC paths (`\\`), and Windows drive root escapes are rejected with `VerificationStatus.BLOCKED`.

3. **n8n Ambiguous Execution Outcomes (`src/openjarvis/tools/n8n.py` & `src/openjarvis/tools/approval_store.py`):**
   - **Explicit `outcome_unknown` State:** Approvals whose post-transmission status is ambiguous transition to `STATUS_OUTCOME_UNKNOWN` and are never auto-restored to `approved`.
   - **Exact Execution Correlation:** `reconcile_workflow_execution()` correlates executions using workflow ID, `started_after` timestamp, `approval_id`, and `idempotency_key`. Older executions are ignored.
   - **Accurate State Transitions:** Confirmed completed -> `STATUS_EXECUTED`; in-flight/running -> `STATUS_EXECUTING`; confirmed failed -> `STATUS_FAILED`; authoritatively confirmed never started -> `STATUS_APPROVED` (safe to retry).
   - **Execution Ledger:** Added `N8nExecutionLedger` tracking dispatch timestamps, idempotency keys, and outcome statuses.

4. **Model Discovery & Capability Policy (`src/openjarvis/models/discovery.py` & `capabilities.py`):**
   - **HTTP 401 Classification:** HTTP 401 and authentication errors are explicitly classified as `status="auth_error"`, `connected=False`, and `model_count=0` (never `connected=True` or `degraded`).
   - **Cloud Empty Model List:** Empty model lists from cloud providers are classified as `connected=False` and `status="degraded"`.
   - **Capability Confidence Policies:** Enforces `CONFIRMED_ONLY`, `DECLARED_OR_CONFIRMED`, and `ALLOW_INFERRED`. Unknown capabilities always evaluate to False.

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

# Jarvis V1 — Final Security Hardening, Process Isolation, Voice Emoji Remediation and Independent Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Security Architect  
**Target Repository:** Jarvis V1 (`oprn-jarvis`)  
**Scope:** Source-level verification and hardening of Voice Mode speech emoji normalization, FORGE cross-platform path validation and container sandboxing, n8n ambiguous outcome reconciliation and pagination truncation handling, model discovery HTTP 401 classification, capability confidence policy enforcement, and multi-agent test reproducibility.

---

## 1. Executive Summary

An independent, rigorous source-level security audit and remediation pass was conducted across the Jarvis V1 codebase. All findings from previous audits were critically re-evaluated and hardened against executable source code:

1. **Voice Mode Emoji Pronunciation Remediation & Actual TTS Component Verification (`frontend/src/lib/message-text.ts`, `frontend/src/components/Chat/VoiceMode.tsx`, `frontend/src/lib/tts.ts`, `frontend/src/components/Chat/VoiceMode.test.tsx`):**
   - **Root Cause Eliminated:** `VoiceMode.tsx` previously implemented a private, duplicate `speakable()` function that omitted emoji stripping entirely, while `message-text.ts` lacked support for keycaps, regional flags, and Fitzpatrick skin tones.
   - **Unified Emoji Normalization:** Implemented `stripEmojis()` covering Regional Indicator flag pairs, keycap sequences (`[0-9#*]\uFE0F?\u20E3`), Fitzpatrick skin tones (`\u{1F3FB}-\u{1F3FF}`), compound ZWJ sequences (`\u200D`), Dingbats, pictographs, and punctuation spacing normalization.
   - **Display vs Speech Strict Separation:** Emojis and Markdown syntax remain 100% visible in the chat interface and Voice Mode visual feedback. Emojis and Markdown markers are stripped strictly before speech synthesis (`window.speechSynthesis.speak` and backend `/api/speech/synthesize`).
   - **Streaming Sentence Pipeline:** Incoming streams chunk on sentence boundaries; empty/emoji-only chunks are prevented from invoking `speechSynthesis.speak()`; speech cursor is tracked accurately; barge-in cancels speech immediately via `window.speechSynthesis.cancel()`.
   - **Actual Speech Component Verification:** Verified in Vitest with happy-dom that `speechSynthesis.speak` is invoked with `SpeechSynthesisUtterance.text` completely devoid of emojis across all 11 required phrases/conditions (simple emoji, punctuation + emoji, flags, developer ZWJ, family ZWJ, skin tones, keycaps, emoji-only, progressive streaming, barge-in stop, and multi-sentence), while DOM elements retain the full original emoji characters.

2. **FORGE Workspace Authorization, Cross-Platform Path Validation & Sandboxing (`src/openjarvis/sandbox/verification_runner.py` & `src/openjarvis/workforce/forge_coding.py`):**
   - **Order of Authorization Enforced:** Reordered verification runner pipeline so that workspace authorization, containment checks, and symlink resolution execute BEFORE any execution branch (before inline execution, before container execution, before host execution, and before metadata commands).
   - **Raw Command Scanning (`scan_raw_command_for_path_escapes`):** On POSIX platforms, `shlex.split(..., posix=True)` consumes backslashes, blinding downstream checks to Windows drive roots (`C:\Windows\System32`) and UNC paths (`\\server\share`). Implemented pre-shlex scanning on the raw command string to block Windows drive roots, UNC paths, and multi-pass URL-encoded traversals (`%252e%252e`) regardless of host OS.
   - **Multi-Pass URL Decoding:** Added `_multi_unquote()` performing up to 5 decoding iterations to eliminate nested URL-encoded traversal escapes (`%252e%252e` -> `%2e%2e` -> `..`).
   - **Relative Symlink Inspection:** Inspected bare relative filename arguments against `(resolved_cwd / token).is_symlink()` to detect and reject symlinks resolving outside authorized workspace boundaries.
   - **Nonexistent Path Containment:** Validated nonexistent path targets via `Path.resolve()` to prevent escapes using delayed creation semantics.
   - **Process Isolation Principle:** Recognized that an executable allowlist alone does NOT provide process isolation against untrusted code in `conftest.py`, pytest plugins, or npm scripts.
   - **Container Isolation Hardening:** Ephemeral container sandbox enforces `--network none`, `--user 1000:1000`, `--cap-drop=ALL`, `--security-opt no-new-privileges`, `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m`, CPU (`2.0`), memory (`1024m`), and process-count (`100`) limits.
   - **Fail-Closed Untrusted Commands:** When container sandbox is unavailable, untrusted project test suites and inline scripts fail closed with `status="blocked"` and `sandbox_mode="blocked_no_container"`.
   - **Zero Host Secrets Leaked:** Environment is scrubbed clean of all credentials (`OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `DEEPSEEK_*`, `ATLASCLOUD_*`, `N8N_*`, tokens, passwords, database URLs).

3. **n8n Exact Execution Reconciliation, Pagination Truncation & Ambiguous Outcomes (`src/openjarvis/tools/n8n.py` & `src/openjarvis/tools/approval_store.py`):**
   - **Timestamp Fallback Removed:** Removed dangerous `elif time_matches and matching_execution is None` fallback that falsely associated unrelated executions. Timestamp alone NEVER proves an execution belongs to an approval.
   - **Strict Workflow Identity:** Every examined execution record must match the exact expected `workflowId`. Execution records with missing or mismatched workflow IDs are rejected.
   - **Pagination Truncation Safety:** Traverses paginated execution logs; if safety limit (`max_pages`) is reached while more pages remain (`nextCursor` present or cursor stalls), returns `status="outcome_unknown"` rather than falsely declaring `not_executed`.
   - **Correlation Metadata Enforcement:** Missing or empty correlation metadata (`approval_id` or `idempotency_key`) immediately yields `status="outcome_unknown"`, blocking duplicate retries.
   - **Execution Ledger:** Tracks dispatch timestamps, idempotency keys, and outcome statuses.

4. **Model Discovery HTTP 401 Classification & Capability Policy (`src/openjarvis/models/discovery.py` & `capabilities.py`):**
   - **HTTP 401/403 Authentication Preservation:** HTTP 401 and 403 errors across all providers are explicitly preserved as `status="auth_error"`, `connected=False`, and `model_count=0` (never overwritten with generic `degraded` or `healthy`).
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
| `tests/workforce/test_forge_sandboxing_p0.py` | Inline `python -c`, `node -e`, `python -m` blocked; workspace validation before execution; raw command path escape scan; UNC, traversal, drive root escapes; host secrets scrubbed; container isolation flags | 18 | 17 PASSED, 1 SKIPPED |
| `tests/workforce/test_forge_command_security.py` | Allowlist enforcement, dangerous shell operators, path traversal, real subprocess execution | 5 | PASSED |
| `tests/workforce/test_forge_security_p0.py` | Cross-process workspace locking, dirty git snapshot containment, verification states | 7 | PASSED |
| `tests/tools/test_n8n_ambiguous_outcomes_p0.py` | `outcome_unknown` state, timestamp fallback removal, exact approval & idempotency matching, pagination truncation handling, empty result vs nextCursor, duplicate retry prevention | 25 | PASSED |
| `tests/tools/test_n8n_concurrency.py` | 10-thread simultaneous claim race, atomic test-and-set conditional updates | 3 | PASSED |
| `tests/tools/test_n8n_security_p0.py` | SSRF rejection, path traversal, canonical payload hash binding, self-approval denial | 11 | PASSED |
| `tests/models/test_model_discovery.py` | Deterministic mocked discovery, REST fallback probes, unreachable providers, 401 & 403 auth errors, empty catalogs, 429 rate limits, 404 endpoint not found, unconfigured environments | 15 | PASSED |
| `tests/models/test_model_capability_policy.py` | DeepSeek R1 non-tool enforcement, confidence policies (`CONFIRMED_ONLY`, `DECLARED_OR_CONFIRMED`), Anthropic discovery | 5 | PASSED |
| `tests/models/test_model_selection_p0.py` | No emergency hardcoded fallbacks, fail-closed privacy, task normalization | 10 | PASSED |
| `tests/workforce/test_specialist_real_execution_e2e.py` | ToolRegistry audit, real Scout & Forge execution, model selection failure to FAILED state, model propagation | 6 | PASSED |
| `tests/workforce/test_scenarios_e2e.py` | Scenarios A, B, C, D deterministic end-to-end multi-agent execution | 4 | PASSED |
| `tests/workforce/test_atlas_orchestrator.py` | Atlas DAG planning, specialist step execution, team messaging | 3 | PASSED |
| `tests/workforce/test_workforce_e2e.py` | Full multi-agent workforce ERP audit DAG with deterministic fixtures | 1 | PASSED |
| Full 18-File Backend Suite | Comprehensive workforce, models, tools, sandbox, connectors regression | 129 | 128 PASSED, 1 SKIPPED |
| `frontend/src/components/Chat/VoiceMode.test.tsx` | VoiceMode actual speech synthesis invocations (`window.speechSynthesis.speak`), DOM emoji retention, all 11 conditions (flags, ZWJ, skin tones, keycaps, streaming, barge-in) | 15 | PASSED |
| Full Frontend Vitest Suite | Store, API, Auth, Telemetry, Capabilities, UI modals, VoiceMode, TTS | 148 passed (24 files) | PASSED |
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

# Jarvis V1 — n8n Integration & Automation Source Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Workflow Automation Architect  
**Scope:** `src/openjarvis/tools/n8n.py`, `src/openjarvis/server/`, `https://n8n.kavlien.store`

---

## 1. Executive Summary

This audit evaluates the integration between OpenJarvis and the user's remote n8n automation instance at `https://n8n.kavlien.store`.

The audit verified live connectivity to `https://n8n.kavlien.store`:
- Live health check at `/healthz`: **HTTP 200 `{"status": "ok"}`**
- Live authenticated session endpoint at `/rest/login` with `emailOrLdapLoginId`: **HTTP 200**
- Workflows retrieved: 2 active/inactive workflows:
  - `JSjICSu0CMbQOPLJ`: *AffiliatePilot health monitor*
  - `sfWTAbXd8CvVQc08`: *Instagram comment to DM (product link)*
- Assistant web UI at `/assistant`: **HTTP 200**

However, several architectural and implementation gaps were identified in `src/openjarvis/tools/n8n.py` and the server integration layer.

---

## 2. Detailed Findings

### Issue N8N-1: Inflexible Authentication in N8nTool
- **Source File:** `src/openjarvis/tools/n8n.py` (Lines 26-36, 100-118)
- **Root Cause:** `N8nTool` only supports `X-N8N-API-KEY` header authentication against `/api/v1/`. In community or cloud editions where an API key is not yet configured, attempts to query workflows fail with HTTP 401.
- **Integration Impact:** The user's instance had zero API keys configured in `/rest/api-keys`. Attempting to query `/api/v1/workflows` with an empty key fails.
- **Proposed Fix:** Support dual authentication in `N8nTool`:
  1. API Key mode (`X-N8N-API-KEY`) for standard `/api/v1/*` endpoints.
  2. Session/Cookie mode via `/rest/login` (using credentials configured in secure environment variables `OPENJARVIS_N8N_USER` and `OPENJARVIS_N8N_PASSWORD`) with automatic cookie caching and expiration renewal.

### Issue N8N-2: Missing Rate Limiting and Session Caching
- **Source File:** `src/openjarvis/tools/n8n.py`
- **Root Cause:** Probing `/rest/login` repeatedly without caching session cookies triggers HTTP 429 (Too Many Requests) from n8n / reverse proxy.
- **Integration Impact:** Frequent workflow polls or health checks will be throttled or blocked.
- **Proposed Fix:** Implement session token / cookie caching with a 6-day TTL (n8n default session is 7 days). Only re-authenticate if an HTTP 401/403 is received.

### Issue N8N-3: No Separation Between LLM Endpoints and Workflow Connectors
- **Source File:** `src/openjarvis/tools/n8n.py`
- **Root Cause:** Lack of typing distinction between an n8n workflow that triggers an external action (e.g. sending an email or posting to Instagram) vs. an n8n workflow that serves an AI inference model.
- **Integration Impact:** Potential risk of the system attempting to route LLM completions through an n8n workflow without verifying its input/output schema.
- **Proposed Fix:** Strictly classify n8n as an **Application Workflow Connector**. It is registered under `ConnectorRegistry` and `ToolRegistry`, NOT `EngineRegistry`, unless an explicit inference schema probe validates an OpenAI-compatible completion contract.

### Issue N8N-4: Approval Store Integration for Mutating Workflows
- **Source File:** `src/openjarvis/tools/n8n.py` (Lines 84-88)
- **Root Cause:** `execute_workflow` and `trigger_webhook` allow executing arbitrary remote automation workflows without verifying whether the action is sensitive or irreversible.
- **Integration Impact:** An autonomous agent could trigger external emails, payments, or destructive webhooks through n8n without user approval, violating Requirement 2.3.
- **Proposed Fix:** Gate `execute_workflow` and `trigger_webhook` through the `ApprovalStore`. Any workflow execution classified as mutating or high-tier must halt in `WAITING_APPROVAL` until approved.

### Issue N8N-5: Credentials Security
- **Source File:** System configuration and environment handling.
- **Root Cause:** Credentials passed in prompt or configuration could inadvertently leak into logs, documentation, or git commits.
- **Integration Impact:** Security vulnerability if credentials are stored in plain text source files.
- **Proposed Fix:** Store credentials strictly in `.env` / system environment variables (`OPENJARVIS_N8N_URL`, `OPENJARVIS_N8N_USER`, `OPENJARVIS_N8N_PASSWORD`, `OPENJARVIS_N8N_API_KEY`). Ensure all logs and error messages scrub passwords and authorization headers.

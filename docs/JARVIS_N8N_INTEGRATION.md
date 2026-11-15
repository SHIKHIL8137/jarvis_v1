# Jarvis n8n Workforce Integration & Connector Guide

## 1. Overview & Live Instance

OpenJarvis integrates with n8n to enable automated operations, webhook routing, and external workflow execution:
- **Live Endpoint**: `https://n8n.kavlien.store`
- **Role**: Application workflow connector (strictly separated from LLM inference providers).
- **Primary Agent**: **PULSE** (Operations & Productivity Specialist) uses `n8n` for routine triggers and health automation.

---

## 2. Dual Authentication Architecture

Modern n8n instances support two authentication modes:

### 2.1 API Key Authentication
When an API key is provided via `OPENJARVIS_N8N_API_KEY` or `credentials.toml`:
- Header: `X-N8N-API-KEY: <key>`
- Workflow Endpoint: `/api/v1/workflows`

### 2.2 Session Cookie Authentication
When credentials are provided via `OPENJARVIS_N8N_EMAIL` (or `OPENJARVIS_N8N_LOGIN_ID`) and `OPENJARVIS_N8N_PASSWORD`:
- Login Endpoint: `POST /rest/login`
- Payload: `{"emailOrLdapLoginId": "<login_id>", "password": "<password>"}`
- Cookie Extracted: `n8n-auth=<token>`
- Workflow Endpoint: `/rest/workflows`

### 2.3 6-Day In-Memory Cookie Caching
Rapid repeated logins against n8n trigger **HTTP 429 Too Many Requests**. To guarantee reliable operation:
- `N8nClient` caches the extracted `n8n-auth` session cookie in memory with a **6-day TTL** ($518,400\text{ seconds}$).
- All subsequent workflow listing and execution calls reuse the valid cookie.
- If n8n returns `HTTP 401 Unauthorized`, the cookie is automatically invalidated and refreshed once.

### 2.4 Custom User-Agent Header
Default Python urllib User-Agents (`Python-urllib/3.12`) are blocked by Cloudflare reverse proxies with HTTP 403 Forbidden. All requests in `N8nClient` emit a standard browser User-Agent header:
`"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OpenJarvis/1.0"`

---

## 3. Security Approval Gating

In accordance with Jarvis's security charter, **all mutating and execution actions against n8n are gated by the `ApprovalStore`**:

1. When `execute_workflow` or a mutating webhook is invoked without an approved `approval_id`:
   - The action is intercepted and queued in SQLite `approvals.db` with `tier="high"`.
   - The tool returns:
     `"Action required approval: executing workflow '<id>' is gated. Approval queued with ID: <id>."`
2. Once the user approves the action in the UI or via voice/chat:
   - The execution proceeds safely and delivers the payload to n8n.

---

## 4. REST API & UI Panel

- `GET /v1/n8n/status`: Returns online/offline status, latency in ms, base URL, and auth mode.
- `GET /v1/n8n/workflows`: Returns discovered workflow summaries (`id`, `name`, `active`, `tags`, `updatedAt`).
- `POST /v1/n8n/workflows/{workflow_id}/execute`: Triggers workflow with approval checking.
- `POST /v1/n8n/webhooks/{webhook_path}`: Triggers an n8n webhook slug.
- **Frontend Panel**: `N8nPanel.tsx` embedded directly into the Agent Workforce dashboard.

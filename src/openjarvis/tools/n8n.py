"""n8n Automation Tool — connects Jarvis to local or remote n8n instance for workflow discovery and execution."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.approval_store import (
    ApprovalStore,
    STATUS_APPROVED,
    TIER_HIGH,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OpenJarvis/1.0"
# Default live n8n instance endpoint
DEFAULT_N8N_URL = "https://n8n.kavlien.store"
# 6 days session cache TTL to avoid HTTP 429 Too Many Requests
SESSION_COOKIE_TTL_SECONDS = 6 * 24 * 3600


class N8nClient:
    """Robust client for n8n API supporting dual authentication (API Key & Session Login)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        email_or_login_id: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("OPENJARVIS_N8N_URL")
            or self._load_config_value("url")
            or DEFAULT_N8N_URL
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("OPENJARVIS_N8N_API_KEY")
            or self._load_config_value("api_key")
            or ""
        )
        self.email_or_login_id = (
            email_or_login_id
            or os.environ.get("OPENJARVIS_N8N_EMAIL")
            or os.environ.get("OPENJARVIS_N8N_LOGIN_ID")
            or self._load_config_value("email")
            or self._load_config_value("login_id")
            or ""
        )
        self.password = (
            password
            or os.environ.get("OPENJARVIS_N8N_PASSWORD")
            or self._load_config_value("password")
            or ""
        )

        self._session_cookie: Optional[str] = None
        self._session_cookie_expires_at: float = 0.0
        self._auth_lock = threading.RLock()

    def _load_config_value(self, key: str) -> Optional[str]:
        """Try loading configuration from ~/.openjarvis/config.toml or credentials.toml."""
        for filename in ("credentials.toml", "config.toml"):
            p = get_config_dir() / filename
            if p.is_file():
                try:
                    import tomllib
                    with open(p, "rb") as f:
                        data = tomllib.load(f)
                    val = data.get("n8n", {}).get(key)
                    if val:
                        return str(val)
                except Exception:
                    pass
        return None

    def _ensure_session(self) -> Optional[str]:
        """Authenticate via /rest/login using emailOrLdapLoginId with in-memory caching."""
        if not self.email_or_login_id or not self.password:
            return None

        with self._auth_lock:
            # Check cached cookie validity
            now = time.time()
            if self._session_cookie and now < self._session_cookie_expires_at:
                return self._session_cookie

            login_url = f"{self.base_url}/rest/login"
            payload = {
                "emailOrLdapLoginId": self.email_or_login_id,
                "password": self.password,
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                login_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": DEFAULT_USER_AGENT,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    set_cookie = resp.headers.get("Set-Cookie")
                    if set_cookie:
                        # Extract cookie string (e.g. n8n-auth=...)
                        cookie_part = set_cookie.split(";")[0].strip()
                        self._session_cookie = cookie_part
                        self._session_cookie_expires_at = now + SESSION_COOKIE_TTL_SECONDS
                        logger.info("Successfully established and cached n8n session cookie (TTL=6d)")
                        return self._session_cookie
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    logger.warning("n8n rate limited (/rest/login returned 429). Using existing cookie if available.")
                    if self._session_cookie:
                        return self._session_cookie
                logger.error("n8n session authentication failed with HTTP %d", exc.code)
            except Exception as exc:
                logger.error("n8n session authentication error: %s", exc)

            return None

    def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: float = 6.0,
        retry_auth: bool = True,
    ) -> Dict[str, Any]:
        """Perform HTTP request against n8n with API key or session cookie."""
        clean_ep = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        url = f"{self.base_url}{clean_ep}"
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }

        # 1. API key if configured
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        else:
            # 2. Session cookie if configured
            cookie = self._ensure_session()
            if cookie:
                headers["Cookie"] = cookie

        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {"status": "ok"}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry_auth and not self.api_key:
                logger.warning("n8n returned 401 Unauthorized; invalidating session cookie and retrying once.")
                with self._auth_lock:
                    self._session_cookie = None
                    self._session_cookie_expires_at = 0.0
                return self._make_request(endpoint, method=method, data=data, timeout=timeout, retry_auth=False)
            raw_err = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"n8n HTTP {exc.code}: {raw_err or exc.reason}")

    def check_health(self) -> Dict[str, Any]:
        """Check whether n8n instance is reachable and healthy."""
        t0 = time.time()
        url = f"{self.base_url}/healthz"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT}, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                elapsed_ms = round((time.time() - t0) * 1000, 1)
                return {
                    "online": resp.getcode() == 200,
                    "status_code": resp.getcode(),
                    "base_url": self.base_url,
                    "latency_ms": elapsed_ms,
                    "auth_mode": "api_key" if self.api_key else ("session" if self._session_cookie or (self.email_or_login_id and self.password) else "unauthenticated"),
                }
        except Exception as exc:
            elapsed_ms = round((time.time() - t0) * 1000, 1)
            return {
                "online": False,
                "status_code": 0,
                "base_url": self.base_url,
                "latency_ms": elapsed_ms,
                "error": str(exc),
                "auth_mode": "unknown",
            }

    def list_workflows(self) -> List[Dict[str, Any]]:
        """List workflows from n8n (supports both /rest/workflows and /api/v1/workflows)."""
        # Try /rest/workflows first (session auth endpoint)
        endpoint = "/rest/workflows" if not self.api_key else "/api/v1/workflows"
        try:
            res = self._make_request(endpoint)
        except Exception:
            # Fallback to alternate endpoint
            alt_endpoint = "/api/v1/workflows" if endpoint == "/rest/workflows" else "/rest/workflows"
            res = self._make_request(alt_endpoint)

        data = res.get("data", [])
        workflows: List[Dict[str, Any]] = []
        for wf in data:
            workflows.append({
                "id": str(wf.get("id")),
                "name": wf.get("name", "Unnamed Workflow"),
                "active": bool(wf.get("active", False)),
                "createdAt": wf.get("createdAt"),
                "updatedAt": wf.get("updatedAt"),
                "tags": [t.get("name") if isinstance(t, dict) else str(t) for t in wf.get("tags", [])],
            })
        return workflows

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Fetch complete definition for a workflow."""
        endpoint = f"/rest/workflows/{workflow_id}" if not self.api_key else f"/api/v1/workflows/{workflow_id}"
        try:
            res = self._make_request(endpoint)
            return res.get("data", res)
        except Exception:
            alt_endpoint = f"/api/v1/workflows/{workflow_id}" if "rest" in endpoint else f"/rest/workflows/{workflow_id}"
            res = self._make_request(alt_endpoint)
            return res.get("data", res)

    def execute_workflow(self, workflow_id: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run workflow execution."""
        endpoint = f"/rest/workflows/{workflow_id}/run" if not self.api_key else f"/api/v1/workflows/{workflow_id}/run"
        payload = {"data": data or {}}
        try:
            return self._make_request(endpoint, method="POST", data=payload)
        except Exception:
            alt_endpoint = f"/api/v1/workflows/{workflow_id}/run" if "rest" in endpoint else f"/rest/workflows/{workflow_id}/run"
            return self._make_request(alt_endpoint, method="POST", data=payload)

    def trigger_webhook(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Trigger an n8n webhook slug or endpoint."""
        clean_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{clean_path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        body = json.dumps(data or {}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"status": "ok"}


_CLIENT_INSTANCE: Optional[N8nClient] = None
_CLIENT_LOCK = threading.Lock()


def get_n8n_client() -> N8nClient:
    """Return shared thread-safe N8nClient singleton."""
    global _CLIENT_INSTANCE
    with _CLIENT_LOCK:
        if _CLIENT_INSTANCE is None:
            _CLIENT_INSTANCE = N8nClient()
        return _CLIENT_INSTANCE


@ToolRegistry.register("n8n")
class N8nTool(BaseTool):
    """Integrates Jarvis with n8n workflow automation with approval gating."""

    tool_id = "n8n"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        approval_store: Optional[ApprovalStore] = None,
    ) -> None:
        self.client = N8nClient(base_url=base_url, api_key=api_key)
        self._approval_store = approval_store or ApprovalStore()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="n8n",
            description=(
                "Interact with an n8n workflow instance to check health, "
                "list active workflows, inspect workflow details, trigger webhooks, or execute automated workflows. "
                "Mutating execution operations are strictly gated by human approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["health", "list_workflows", "get_workflow", "execute_workflow", "trigger_webhook"],
                        "description": "Operation to perform against n8n.",
                    },
                    "workflow_id": {
                        "type": "string",
                        "description": "Target workflow ID for execution or inspection.",
                    },
                    "webhook_path": {
                        "type": "string",
                        "description": "Target webhook slug or URL path for trigger_webhook.",
                    },
                    "payload": {
                        "type": "object",
                        "description": "Data payload to deliver to the workflow or webhook.",
                    },
                    "approval_id": {
                        "type": "string",
                        "description": "Approval ID confirming user authorization for sensitive execution.",
                    },
                },
                "required": ["action"],
            },
            category="automation",
            cost_estimate=0.0,
            latency_estimate=0.5,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "health")
        workflow_id = params.get("workflow_id", "")
        webhook_path = params.get("webhook_path", "")
        payload = params.get("payload") or {}
        approval_id = params.get("approval_id")

        try:
            if action == "health":
                health = self.client.check_health()
                return ToolResult(
                    tool_name="n8n",
                    content=json.dumps(health, indent=2),
                    success=health.get("online", False),
                )

            elif action == "list_workflows":
                workflows = self.client.list_workflows()
                return ToolResult(
                    tool_name="n8n",
                    content=json.dumps({"total": len(workflows), "workflows": workflows}, indent=2),
                    success=True,
                )

            elif action == "get_workflow":
                if not workflow_id:
                    return ToolResult(tool_name="n8n", content="workflow_id is required for get_workflow", success=False)
                wf = self.client.get_workflow(workflow_id)
                return ToolResult(tool_name="n8n", content=json.dumps(wf, indent=2), success=True)

            elif action == "execute_workflow":
                if not workflow_id:
                    return ToolResult(tool_name="n8n", content="workflow_id is required for execute_workflow", success=False)

                # Security Approval Gate: mutating workflow execution
                is_approved = self._check_approval(
                    action_type="n8n_execute_workflow",
                    desc=f"Execute n8n workflow '{workflow_id}' with payload {payload}",
                    payload={"workflow_id": workflow_id, "data": payload},
                    permission_key=f"n8n:workflow:execute:{workflow_id}",
                    approval_id=approval_id,
                )
                if not is_approved[0]:
                    return ToolResult(
                        tool_name="n8n",
                        content=(
                            f"Action required approval: executing workflow '{workflow_id}' is gated. "
                            f"Approval queued with ID: {is_approved[1]}. Please approve to execute."
                        ),
                        success=False,
                    )

                res = self.client.execute_workflow(workflow_id, data=payload)
                return ToolResult(
                    tool_name="n8n",
                    content=f"Workflow '{workflow_id}' executed: {json.dumps(res, indent=2)}",
                    success=True,
                )

            elif action == "trigger_webhook":
                if not webhook_path:
                    return ToolResult(tool_name="n8n", content="webhook_path is required for trigger_webhook", success=False)

                res = self.client.trigger_webhook(webhook_path, data=payload)
                return ToolResult(
                    tool_name="n8n",
                    content=f"Webhook '{webhook_path}' triggered: {json.dumps(res, indent=2)}",
                    success=True,
                )

            else:
                return ToolResult(tool_name="n8n", content=f"Unknown n8n action: '{action}'", success=False)

        except Exception as exc:
            logger.debug("n8n tool execution error: %s", exc)
            return ToolResult(
                tool_name="n8n",
                content=f"n8n operation '{action}' failed: {str(exc)}",
                success=False,
            )

    def _check_approval(
        self,
        action_type: str,
        desc: str,
        payload: Dict[str, Any],
        permission_key: str,
        approval_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verify action has human approval or queue pending approval."""
        if approval_id:
            action = self._approval_store.get_action(approval_id)
            if action and action.status == STATUS_APPROVED:
                return True, action.id

        existing = self._approval_store.get_action_by_permission_key(permission_key)
        if existing and existing.status == STATUS_APPROVED:
            return True, existing.id
        if existing:
            return False, existing.id

        action = self._approval_store.queue_action(
            action_type=action_type,
            description=desc,
            payload=payload,
            permission_key=permission_key,
            tier=TIER_HIGH,
        )
        return False, action.id

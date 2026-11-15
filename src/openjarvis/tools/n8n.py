"""n8n Automation Tool — connects Jarvis to local or remote n8n instance for workflow discovery and execution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
    STATUS_DENIED,
    STATUS_EXECUTED,
    STATUS_EXPIRED,
    STATUS_OUTCOME_UNKNOWN,
    STATUS_PENDING,
    TIER_HIGH,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OpenJarvis/1.0"
DEFAULT_N8N_URL = "https://n8n.kavlien.store"


def _validate_base_url(url: str) -> str:
    """Validate base URL against SSRF and unsupported protocols."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}': must be http or https")
    if not parsed.netloc:
        raise ValueError("Invalid n8n URL: missing network location/host")
    return url.rstrip("/")


def _sanitize_path(path: str) -> str:
    """Sanitize endpoint or webhook path against directory traversal."""
    unquoted = urllib.parse.unquote(path).strip()
    if ".." in unquoted or unquoted.startswith("//") or "\0" in unquoted or "\\" in unquoted:
        raise ValueError(f"Security boundary violation: invalid path traversal detected in '{path}'")
    clean = unquoted if unquoted.startswith("/") else f"/{unquoted}"
    return clean


def _canonical_payload_hash(payload: Any) -> str:
    """Generate deterministic SHA-256 hash of canonicalized JSON payload."""
    try:
        canon = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        canon = str(payload)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _parse_timestamp(val: Any) -> Optional[float]:
    """Parse ISO timestamp string or epoch (seconds/milliseconds) to float epoch seconds."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val / 1000.0 if val > 1e11 else float(val)
    if isinstance(val, str):
        try:
            from datetime import datetime
            return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
        except Exception:
            try:
                return float(val)
            except Exception:
                return None
    return None


class N8nExecutionLedger:
    """Thread-safe ledger tracking workflow and webhook dispatch attempts and reconciliation outcomes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}

    def record_dispatch(
        self,
        approval_id: str,
        target_type: str,
        target: str,
        payload_hash: str,
        idempotency_key: str,
    ) -> None:
        with self._lock:
            self._entries[approval_id] = {
                "approval_id": approval_id,
                "target_type": target_type,
                "target": target,
                "payload_hash": payload_hash,
                "idempotency_key": idempotency_key,
                "dispatched_at": time.time(),
                "status": "in_flight",
                "execution_id": None,
                "error": None,
            }

    def record_outcome(
        self,
        approval_id: str,
        status: str,
        execution_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            if approval_id in self._entries:
                self._entries[approval_id]["status"] = status
                if execution_id:
                    self._entries[approval_id]["execution_id"] = execution_id
                if error:
                    self._entries[approval_id]["error"] = error

    def get_entry(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._entries[approval_id]) if approval_id in self._entries else None


def classify_network_failure(exc: Exception) -> str:
    """Classify exception into:
    - 'pre_transmission': Definitively failed before request bytes were transmitted (DNS error, connection refused, SSL handshake).
    - 'outcome_unknown': Ambiguous failure where delivery may have occurred (timeout, connection reset while reading, HTTP 502/504).
    - 'post_transmission_error': Server returned an explicit error response (HTTP 4xx/5xx).
    """
    import socket
    import ssl

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (502, 504):
            return "outcome_unknown"
        return "post_transmission_error"

    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return "pre_transmission"
        if isinstance(reason, ConnectionRefusedError):
            return "pre_transmission"
        if isinstance(reason, ssl.SSLError):
            return "pre_transmission"
        reason_str = str(reason).lower()
        if any(term in reason_str for term in ("connection refused", "name or service not known", "getaddrinfo failed", "nodename nor servname provided")):
            return "pre_transmission"
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in reason_str or "timeout" in reason_str:
            return "outcome_unknown"
        if isinstance(reason, (ConnectionResetError, BrokenPipeError)) or "connection reset" in reason_str:
            return "outcome_unknown"

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "outcome_unknown"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return "outcome_unknown"

    return "outcome_unknown"  # Fail safe: when in doubt, treat as outcome_unknown


DEFAULT_SESSION_COOKIE_TTL_SECONDS = 6 * 24 * 3600


def _parse_cookie_expiry(set_cookie_header: str) -> float:
    """Parse real cookie expiry from Set-Cookie header rather than assuming fixed TTL."""
    now = time.time()
    parts = [p.strip() for p in set_cookie_header.split(";")]
    for p in parts[1:]:
        lower = p.lower()
        if lower.startswith("max-age="):
            try:
                sec = float(p.split("=", 1)[1].strip())
                return now + max(60.0, sec)
            except ValueError:
                pass
        elif lower.startswith("expires="):
            try:
                import email.utils

                dt = email.utils.parsedate_to_datetime(p.split("=", 1)[1].strip())
                return dt.timestamp()
            except Exception:
                pass
    # Fallback to default session TTL if neither directive was supplied
    return now + DEFAULT_SESSION_COOKIE_TTL_SECONDS


class N8nClient:
    """Robust client for n8n API supporting dual authentication (API Key & Session Login)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        email_or_login_id: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        raw_url = (
            base_url
            or os.environ.get("OPENJARVIS_N8N_URL")
            or self._load_config_value("url")
            or DEFAULT_N8N_URL
        )
        self.base_url = _validate_base_url(raw_url)
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
        """Authenticate via /rest/login using emailOrLdapLoginId with real cookie TTL caching."""
        if not self.email_or_login_id or not self.password:
            return None

        with self._auth_lock:
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
                        cookie_part = set_cookie.split(";")[0].strip()
                        self._session_cookie = cookie_part
                        self._session_cookie_expires_at = _parse_cookie_expiry(set_cookie)
                        logger.info("Successfully established n8n session cookie (expires_at=%.0f)", self._session_cookie_expires_at)
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
        headers_extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform sanitized HTTP request against n8n with API key or session cookie."""
        clean_ep = _sanitize_path(endpoint)
        url = f"{self.base_url}{clean_ep}"
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if headers_extra:
            headers.update(headers_extra)

        # API key if configured
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        else:
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
                return self._make_request(endpoint, method=method, data=data, timeout=timeout, retry_auth=False, headers_extra=headers_extra)
            raw_err = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"n8n HTTP {exc.code}: {raw_err or exc.reason}")

    def check_health(self) -> Dict[str, Any]:
        """Check whether n8n instance is reachable and distinguish authenticated API health."""
        t0 = time.time()
        url = f"{self.base_url}/healthz"
        basic_online = False
        status_code = 0
        api_authenticated = False
        auth_error: Optional[str] = None

        try:
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT}, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                status_code = resp.getcode()
                basic_online = (status_code == 200)
        except Exception as exc:
            status_code = 0
            auth_error = str(exc)

        # Distinguish authenticated API health
        has_creds = bool(self.api_key or (self.email_or_login_id and self.password) or self._session_cookie)
        if basic_online and has_creds:
            try:
                # Probe API endpoint to verify active authentication
                self.list_workflows()
                api_authenticated = True
            except Exception as exc:
                api_authenticated = False
                auth_error = str(exc)

        elapsed_ms = round((time.time() - t0) * 1000, 1)
        auth_mode = "api_key" if self.api_key else ("session" if has_creds else "unauthenticated")

        return {
            "online": basic_online,
            "api_authenticated": api_authenticated,
            "status_code": status_code,
            "base_url": self.base_url,
            "latency_ms": elapsed_ms,
            "auth_mode": auth_mode,
            "error": auth_error,
        }

    def list_workflows(self) -> List[Dict[str, Any]]:
        """List workflows from n8n (supports both /rest/workflows and /api/v1/workflows)."""
        endpoint = "/rest/workflows" if not self.api_key else "/api/v1/workflows"
        try:
            res = self._make_request(endpoint)
        except Exception:
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
        clean_id = _sanitize_path(workflow_id).lstrip("/")
        endpoint = f"/rest/workflows/{clean_id}" if not self.api_key else f"/api/v1/workflows/{clean_id}"
        try:
            res = self._make_request(endpoint)
            return res.get("data", res)
        except Exception:
            alt_endpoint = f"/api/v1/workflows/{clean_id}" if "rest" in endpoint else f"/rest/workflows/{clean_id}"
            res = self._make_request(alt_endpoint)
            return res.get("data", res)

    def execute_workflow(
        self,
        workflow_id: str,
        data: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run workflow execution with optional idempotency key binding."""
        clean_id = _sanitize_path(workflow_id).lstrip("/")
        endpoint = f"/rest/workflows/{clean_id}/run" if not self.api_key else f"/api/v1/workflows/{clean_id}/run"
        payload_data = dict(data or {})
        if idempotency_key:
            payload_data["_idempotency_key"] = idempotency_key
        payload = {"data": payload_data}
        headers_extra = {"X-Idempotency-Key": idempotency_key} if idempotency_key else None

        try:
            return self._make_request(endpoint, method="POST", data=payload, headers_extra=headers_extra)
        except Exception:
            alt_endpoint = f"/api/v1/workflows/{clean_id}/run" if "rest" in endpoint else f"/rest/workflows/{clean_id}/run"
            return self._make_request(alt_endpoint, method="POST", data=payload, headers_extra=headers_extra)

    def trigger_webhook(
        self,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Trigger an n8n webhook slug or endpoint with optional idempotency key."""
        clean_path = _sanitize_path(path)
        url = f"{self.base_url}{clean_path}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        payload_data = dict(data or {})
        if idempotency_key:
            payload_data["_idempotency_key"] = idempotency_key

        body = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"status": "ok"}

    def reconcile_workflow_execution(
        self,
        workflow_id: str,
        started_after: Optional[float] = None,
        approval_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query n8n execution list to determine if a workflow executed or is currently executing.

        Correlates against workflow_id, started_after timestamp, approval_id, and idempotency_key.
        """
        clean_id = _sanitize_path(workflow_id).lstrip("/")
        endpoint = f"/rest/executions?workflowId={clean_id}" if not self.api_key else f"/api/v1/executions?workflowId={clean_id}"
        try:
            res = self._make_request(endpoint)
        except Exception:
            alt_endpoint = f"/api/v1/executions?workflowId={clean_id}" if "rest" in endpoint else f"/rest/executions?workflowId={clean_id}"
            try:
                res = self._make_request(alt_endpoint)
            except Exception as exc:
                return {"reconciled": False, "found": False, "error": str(exc)}

        data = res.get("data", [])
        if not data and isinstance(res, list):
            data = res

        if not data:
            return {"reconciled": True, "found": False, "status": "not_executed"}

        matching_execution = None
        for item in data:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", ""))

            # Check idempotency / approval key in metadata or customData if present
            custom_data = item.get("customData") or item.get("metadata") or {}
            custom_str = json.dumps(custom_data) if isinstance(custom_data, (dict, list)) else str(custom_data)

            has_key_match = False
            if idempotency_key and (idempotency_key in custom_str or item_id == idempotency_key):
                has_key_match = True
            if approval_id and (approval_id in custom_str or item_id == approval_id):
                has_key_match = True

            # Check started_after timestamp
            exec_time = _parse_timestamp(item.get("startedAt") or item.get("createdAt"))
            time_matches = True
            if started_after is not None and exec_time is not None:
                # Execution started before our action (allow 2.0s clock skew)
                if exec_time < (started_after - 2.0):
                    time_matches = False

            if has_key_match:
                matching_execution = item
                break
            elif time_matches and matching_execution is None:
                matching_execution = item

        if not matching_execution:
            return {"reconciled": True, "found": False, "status": "not_executed"}

        exec_id = str(matching_execution.get("id", ""))
        raw_status = str(matching_execution.get("status", "unknown")).lower()
        finished = bool(matching_execution.get("finished", False))

        if raw_status in ("success", "finished") or (finished and raw_status not in ("error", "failed", "crashed")):
            norm_status = "success"
        elif raw_status in ("running", "waiting", "executing") or (not finished and raw_status not in ("error", "failed", "crashed")):
            norm_status = "running"
        elif raw_status in ("error", "failed", "crashed"):
            norm_status = "failed"
        else:
            norm_status = raw_status

        return {
            "reconciled": True,
            "found": True,
            "execution_id": exec_id,
            "status": norm_status,
            "finished": finished,
        }


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
    """Integrates Jarvis with n8n workflow automation with strict human approval gating."""

    tool_id = "n8n"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        approval_store: Optional[ApprovalStore] = None,
    ) -> None:
        self.client = N8nClient(base_url=base_url, api_key=api_key)
        self._approval_store = approval_store or ApprovalStore()
        self._ledger = N8nExecutionLedger()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="n8n",
            description=(
                "Interact with an n8n workflow instance to check health, "
                "list active workflows, inspect workflow details, trigger webhooks, or execute automated workflows. "
                "Mutating execution operations and sensitive webhook triggers are strictly gated by human approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["health", "list_workflows", "get_workflow", "execute_workflow", "trigger_webhook", "reconcile"],
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
                clean_id = _sanitize_path(workflow_id).lstrip("/")
                wf = self.client.get_workflow(clean_id)
                return ToolResult(tool_name="n8n", content=json.dumps(wf, indent=2), success=True)

            elif action == "execute_workflow":
                if not workflow_id:
                    return ToolResult(tool_name="n8n", content="workflow_id is required for execute_workflow", success=False)

                clean_id = _sanitize_path(workflow_id).lstrip("/")
                canon_hash = _canonical_payload_hash(payload)
                perm_key = f"n8n:workflow:{clean_id}:{canon_hash}"

                if approval_id:
                    claimed, action_obj, claim_msg = self._approval_store.claim_approval_for_execution(
                        approval_id, perm_key, "n8n_execute_workflow"
                    )
                else:
                    claimed, action_obj, claim_msg = self._approval_store.claim_by_permission_key(
                        perm_key, "n8n_execute_workflow"
                    )

                if not claimed:
                    if not approval_id and (not action_obj or claim_msg == "No active approved action for this permission key."):
                        new_act = self._approval_store.queue_action(
                            action_type="n8n_execute_workflow",
                            description=f"Execute n8n workflow '{clean_id}'",
                            payload={
                                "permission_key": perm_key,
                                "action_type": "n8n_execute_workflow",
                                "payload_hash": canon_hash,
                                "_requester": "user",
                            },
                            permission_key=perm_key,
                            tier=TIER_HIGH,
                        )
                        claim_msg = f"Approval queued with ID: {new_act.id}. Please approve to execute."

                    return ToolResult(
                        tool_name="n8n",
                        content=f"Action required approval: executing workflow '{clean_id}' is gated. {claim_msg}",
                        success=False,
                    )

                # Atomically claimed in STATUS_EXECUTING
                self._ledger.record_dispatch(
                    approval_id=action_obj.id,
                    target_type="workflow",
                    target=clean_id,
                    payload_hash=canon_hash,
                    idempotency_key=action_obj.id,
                )
                try:
                    res = self.client.execute_workflow(clean_id, data=payload, idempotency_key=action_obj.id)
                    self._approval_store.complete_approval_execution(action_obj.id, success=True)
                    self._ledger.record_outcome(
                        action_obj.id,
                        status=STATUS_EXECUTED,
                        execution_id=str(res.get("id", "")) if isinstance(res, dict) else None,
                    )
                    return ToolResult(
                        tool_name="n8n",
                        content=f"Workflow '{clean_id}' executed: {json.dumps(res, indent=2)}",
                        success=True,
                    )
                except Exception as exec_err:
                    failure_cat = classify_network_failure(exec_err)
                    self._ledger.record_outcome(action_obj.id, status=failure_cat, error=str(exec_err))
                    if failure_cat == "pre_transmission":
                        self._approval_store.complete_approval_execution(
                            action_obj.id, success=False, error=str(exec_err), pre_execution_failure=True
                        )
                    elif failure_cat == "outcome_unknown":
                        self._approval_store.complete_approval_execution(
                            action_obj.id, success=False, error=str(exec_err), outcome_unknown=True
                        )
                    else:
                        self._approval_store.complete_approval_execution(
                            action_obj.id, success=False, error=str(exec_err), pre_execution_failure=False
                        )
                    raise

            elif action == "trigger_webhook":
                if not webhook_path:
                    return ToolResult(tool_name="n8n", content="webhook_path is required for trigger_webhook", success=False)

                clean_path = _sanitize_path(webhook_path)
                canon_hash = _canonical_payload_hash(payload)
                perm_key = f"n8n:webhook:{clean_path.strip('/')}:{canon_hash}"

                if approval_id:
                    claimed, action_obj, claim_msg = self._approval_store.claim_approval_for_execution(
                        approval_id, perm_key, "n8n_trigger_webhook"
                    )
                else:
                    claimed, action_obj, claim_msg = self._approval_store.claim_by_permission_key(
                        perm_key, "n8n_trigger_webhook"
                    )

                if not claimed:
                    if not approval_id and (not action_obj or claim_msg == "No active approved action for this permission key."):
                        new_act = self._approval_store.queue_action(
                            action_type="n8n_trigger_webhook",
                            description=f"Trigger n8n webhook '{clean_path}'",
                            payload={
                                "permission_key": perm_key,
                                "action_type": "n8n_trigger_webhook",
                                "payload_hash": canon_hash,
                                "_requester": "user",
                            },
                            permission_key=perm_key,
                            tier=TIER_HIGH,
                        )
                        claim_msg = f"Approval queued with ID: {new_act.id}. Please approve to execute."

                    return ToolResult(
                        tool_name="n8n",
                        content=f"Action required approval: webhook '{clean_path}' is gated. {claim_msg}",
                        success=False,
                    )

                # Atomically claimed in STATUS_EXECUTING
                self._ledger.record_dispatch(
                    approval_id=action_obj.id,
                    target_type="webhook",
                    target=clean_path,
                    payload_hash=canon_hash,
                    idempotency_key=action_obj.id,
                )
                try:
                    res = self.client.trigger_webhook(clean_path, data=payload, idempotency_key=action_obj.id)
                    self._approval_store.complete_approval_execution(action_obj.id, success=True)
                    self._ledger.record_outcome(action_obj.id, status=STATUS_EXECUTED)
                    return ToolResult(
                        tool_name="n8n",
                        content=f"Webhook '{clean_path}' triggered: {json.dumps(res, indent=2)}",
                        success=True,
                    )
                except Exception as exec_err:
                    failure_cat = classify_network_failure(exec_err)
                    self._ledger.record_outcome(action_obj.id, status=failure_cat, error=str(exec_err))
                    if failure_cat == "pre_transmission":
                        self._approval_store.complete_approval_execution(
                            action_obj.id, success=False, error=str(exec_err), pre_execution_failure=True
                        )
                    elif failure_cat == "outcome_unknown":
                        self._approval_store.complete_approval_execution(
                            action_obj.id, success=False, error=str(exec_err), outcome_unknown=True
                        )
                    else:
                        self._approval_store.complete_approval_execution(
                            action_obj.id, success=False, error=str(exec_err), pre_execution_failure=False
                        )
                    raise

            elif action == "reconcile":
                if not approval_id:
                    return ToolResult(tool_name="n8n", content="approval_id is required for reconcile", success=False)
                act = self._approval_store.get_action(approval_id)
                if not act:
                    return ToolResult(tool_name="n8n", content=f"Approval ID '{approval_id}' not found", success=False)
                if act.status != STATUS_OUTCOME_UNKNOWN:
                    return ToolResult(
                        tool_name="n8n",
                        content=f"Approval ID '{approval_id}' has status '{act.status}'. Only '{STATUS_OUTCOME_UNKNOWN}' requires reconciliation.",
                        success=False,
                    )

                target_wf = workflow_id or (act.payload.get("workflow_id") if isinstance(act.payload, dict) else "")
                if not target_wf and act.permission_key.startswith("n8n:workflow:"):
                    parts = act.permission_key.split(":")
                    if len(parts) >= 3:
                        target_wf = parts[2]

                started_after_sec = _parse_timestamp(act.created_at) if act.created_at else None

                if target_wf:
                    rec_res = self.client.reconcile_workflow_execution(
                        target_wf,
                        started_after=started_after_sec,
                        approval_id=approval_id,
                        idempotency_key=approval_id,
                    )
                    if rec_res.get("found"):
                        st_name = str(rec_res.get("status", "")).lower()
                        if st_name in ("success", "finished"):
                            new_st = STATUS_EXECUTED
                        elif st_name in ("running", "waiting", "executing"):
                            new_st = STATUS_EXECUTING
                        else:
                            new_st = STATUS_FAILED
                    elif rec_res.get("reconciled") and not rec_res.get("found"):
                        new_st = STATUS_APPROVED  # Authoritatively confirmed never started; safe to retry
                    else:
                        return ToolResult(
                            tool_name="n8n",
                            content=f"Reconciliation query failed: {rec_res.get('error', 'Unknown error')}",
                            success=False,
                        )
                else:
                    new_st = STATUS_FAILED

                ok, msg = self._approval_store.reconcile_approval(approval_id, new_st)
                self._ledger.record_outcome(
                    approval_id,
                    status=new_st,
                    execution_id=rec_res.get("execution_id") if target_wf and rec_res.get("found") else None,
                )
                return ToolResult(
                    tool_name="n8n",
                    content=f"Reconciliation for approval '{approval_id}': {msg}",
                    success=ok,
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
    ) -> Tuple[bool, str]:
        """Verify action has human approval with atomic conditional claim and payload binding."""
        if approval_id:
            claimed, action_obj, msg = self._approval_store.claim_approval_for_execution(
                approval_id, permission_key, action_type
            )
            return claimed, msg

        claimed, action_obj, msg = self._approval_store.claim_by_permission_key(
            permission_key, action_type
        )
        if claimed and action_obj:
            return True, action_obj.id

        # Queue new high-tier pending action if none exists
        action = self._approval_store.queue_action(
            action_type=action_type,
            description=desc,
            payload={
                "permission_key": permission_key,
                "action_type": action_type,
                "payload_hash": _canonical_payload_hash(payload),
                "_requester": "user",
            },
            permission_key=permission_key,
            tier=TIER_HIGH,
        )
        return False, f"Approval queued with ID: {action.id}. Please approve to execute."

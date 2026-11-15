"""n8n Automation Tool — connects Jarvis to local or remote n8n instance for workflow discovery and execution."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


@ToolRegistry.register("n8n")
class N8nTool(BaseTool):
    """Integrates Jarvis with n8n workflow automation."""

    tool_id = "n8n"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("OPENJARVIS_N8N_URL", "http://localhost:5678")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENJARVIS_N8N_API_KEY", "")

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="n8n",
            description=(
                "Interact with an n8n workflow instance to check health, "
                "list active workflows, trigger webhooks, or execute automated workflows."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["health", "list_workflows", "execute_workflow", "trigger_webhook"],
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

        try:
            if action == "health":
                return self._check_health()
            elif action == "list_workflows":
                return self._list_workflows()
            elif action == "execute_workflow":
                return self._execute_workflow(workflow_id, payload)
            elif action == "trigger_webhook":
                return self._trigger_webhook(webhook_path, payload)
            else:
                return ToolResult(
                    success=False,
                    content=f"Unknown n8n action: '{action}'",
                )
        except Exception as exc:
            logger.debug("n8n tool execution error: %s", exc)
            return ToolResult(
                success=False,
                content=f"n8n operation '{action}' failed: {str(exc)}",
            )

    def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: float = 4.0,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key

        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"status": "ok"}

    def _check_health(self) -> ToolResult:
        try:
            url = f"{self.base_url}/healthz"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                status_code = resp.getcode()
                return ToolResult(
                    success=status_code == 200,
                    content=f"n8n is ONLINE at {self.base_url} (HTTP {status_code})",
                )
        except Exception as exc:
            return ToolResult(
                success=False,
                content=f"n8n is OFFLINE or unreachable at {self.base_url}: {str(exc)}",
            )

    def _list_workflows(self) -> ToolResult:
        try:
            res = self._make_request("/api/v1/workflows")
            workflows = res.get("data", [])
            summary = [
                {
                    "id": wf.get("id"),
                    "name": wf.get("name"),
                    "active": wf.get("active", False),
                }
                for wf in workflows
            ]
            return ToolResult(
                success=True,
                content=json.dumps({"total": len(summary), "workflows": summary}, indent=2),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                content=f"Could not list n8n workflows: {str(exc)}",
            )

    def _execute_workflow(self, workflow_id: str, payload: Dict[str, Any]) -> ToolResult:
        if not workflow_id:
            return ToolResult(success=False, content="workflow_id is required")

        try:
            res = self._make_request(
                f"/api/v1/workflows/{workflow_id}/run",
                method="POST",
                data={"data": payload},
            )
            return ToolResult(
                success=True,
                content=f"Workflow {workflow_id} triggered successfully: {json.dumps(res)}",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                content=f"Failed to execute workflow {workflow_id}: {str(exc)}",
            )

    def _trigger_webhook(self, webhook_path: str, payload: Dict[str, Any]) -> ToolResult:
        if not webhook_path:
            return ToolResult(success=False, content="webhook_path is required")

        try:
            clean_path = webhook_path if webhook_path.startswith("/") else f"/{webhook_path}"
            res = self._make_request(
                clean_path,
                method="POST",
                data=payload,
            )
            return ToolResult(
                success=True,
                content=f"Webhook triggered: {json.dumps(res)}",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                content=f"Failed to trigger webhook {webhook_path}: {str(exc)}",
            )

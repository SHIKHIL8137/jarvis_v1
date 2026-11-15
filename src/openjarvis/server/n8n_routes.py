"""FastAPI REST routes for n8n status, workflow discovery, execution, and webhook triggering."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.tools.n8n import N8nTool, get_n8n_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/n8n", tags=["n8n"])


class WorkflowExecuteRequest(BaseModel):
    data: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None


class WebhookTriggerRequest(BaseModel):
    data: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None


@router.get("/status")
async def get_n8n_status() -> Dict[str, Any]:
    """Retrieve n8n connectivity, health, and authentication status."""
    client = get_n8n_client()
    return client.check_health()


@router.get("/workflows")
async def list_n8n_workflows() -> Dict[str, Any]:
    """List all available workflows in the n8n instance."""
    client = get_n8n_client()
    try:
        workflows = client.list_workflows()
        return {"total": len(workflows), "workflows": workflows}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch workflows from n8n: {exc}")


@router.get("/workflows/{workflow_id}")
async def get_n8n_workflow_detail(workflow_id: str) -> Dict[str, Any]:
    """Fetch complete definition for a specific workflow."""
    client = get_n8n_client()
    try:
        wf = client.get_workflow(workflow_id)
        return {"workflow": wf}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch workflow '{workflow_id}': {exc}")


@router.post("/workflows/{workflow_id}/execute")
async def execute_n8n_workflow(workflow_id: str, req: WorkflowExecuteRequest) -> Dict[str, Any]:
    """Execute an n8n workflow with approval store gating."""
    tool = N8nTool()
    res = tool.execute(
        action="execute_workflow",
        workflow_id=workflow_id,
        payload=req.data or {},
        approval_id=req.approval_id,
    )
    if not res.success:
        if "approval" in res.content.lower():
            return {
                "status": "waiting_approval",
                "message": res.content,
                "workflow_id": workflow_id,
            }
        raise HTTPException(status_code=400, detail=res.content)

    return {
        "status": "executed",
        "workflow_id": workflow_id,
        "result": res.content,
    }


@router.post("/webhooks/{webhook_path:path}")
async def trigger_n8n_webhook(webhook_path: str, req: WebhookTriggerRequest) -> Dict[str, Any]:
    """Trigger an n8n webhook slug or URL through strict approval gate."""
    tool = N8nTool()
    res = tool.execute(
        action="trigger_webhook",
        webhook_path=webhook_path,
        payload=req.data or {},
        approval_id=req.approval_id,
    )
    if not res.success:
        if "approval" in res.content.lower():
            return {
                "status": "waiting_approval",
                "message": res.content,
                "webhook_path": webhook_path,
            }
        raise HTTPException(status_code=400, detail=res.content)

    return {
        "status": "triggered",
        "webhook_path": webhook_path,
        "result": res.content,
    }

"""REST endpoints for the proactive-agent approval queue."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from openjarvis.tools import folder_grants
from openjarvis.tools.approval_store import (
    STATUS_APPROVED,
    STATUS_DENIED,
    ApprovalStore,
    PendingAction,
)

try:
    from fastapi import APIRouter, HTTPException
except ImportError:
    raise ImportError("fastapi is required for approval routes")

logger = logging.getLogger(__name__)

router = APIRouter()

# Singleton that shares the same DB file as ProactiveAgent (WAL mode is safe)
_store: Optional[ApprovalStore] = None


def _get_store() -> ApprovalStore:
    global _store
    if _store is None:
        _store = ApprovalStore()
    return _store


def _serialize(action: PendingAction) -> Dict[str, Any]:
    return {
        "id": action.id,
        "action_type": action.action_type,
        "description": action.description,
        "payload": action.payload,
        "permission_key": action.permission_key,
        "tier": action.tier,
        "status": action.status,
        "created_at": action.created_at,
        "expires_at": action.expires_at,
    }


@router.get("/v1/approvals/pending")
async def list_pending_approvals() -> Dict[str, Any]:
    store = _get_store()
    store.expire_stale()
    actions = store.list_pending()
    return {"actions": [_serialize(a) for a in actions], "count": len(actions)}


@router.post("/v1/approvals/{action_id}/approve")
async def approve_action(action_id: str, remember: bool = False) -> Dict[str, Any]:
    """Approve; with ``remember=true`` also grant the action's folder permanently."""
    store = _get_store()
    action = store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    # Sensitive and irreversible actions require fresh explicit approval every time;
    # never allow remember=True to bypass high-tier operations.
    if remember and (action.tier == "high" or action.action_type in ("system_admin", "code_execute", "channel_send")):
        remember = False
        logger.info("Ignoring remember=True for sensitive action %s", action_id)

    granted = ""
    folder = (action.payload or {}).get("folder", "")
    if remember and (action.payload or {}).get("desktop"):
        from openjarvis.tools.desktop import set_control_enabled

        set_control_enabled(True)
        granted = "desktop-control"
    if remember and folder:
        try:
            granted = folder_grants.grant(folder)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    store.update_status(action_id, STATUS_APPROVED)
    logger.info("Action %s approved via UI (granted=%s)", action_id, granted or "-")
    return {"status": "approved", "id": action_id, "granted": granted}


@router.post("/v1/approvals/{action_id}/deny")
async def deny_action(action_id: str) -> Dict[str, Any]:
    store = _get_store()
    action = store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    store.update_status(action_id, STATUS_DENIED)
    logger.info("Action %s denied via UI", action_id)
    return {"status": "denied", "id": action_id}


@router.get("/v1/desktop-control")
async def get_desktop_control() -> Dict[str, Any]:
    from openjarvis.tools.desktop import control_enabled

    return {"enabled": control_enabled()}


@router.post("/v1/desktop-control")
async def set_desktop_control(body: Dict[str, Any]) -> Dict[str, Any]:
    from openjarvis.tools.desktop import control_enabled, set_control_enabled

    set_control_enabled(bool(body.get("enabled")))
    return {"enabled": control_enabled()}


@router.get("/v1/folder-grants")
async def list_folder_grants() -> Dict[str, Any]:
    return {"folders": folder_grants.list_grants()}


@router.post("/v1/folder-grants")
async def add_folder_grant(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return {"granted": folder_grants.grant(str(body.get("path", "")))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/v1/folder-grants")
async def revoke_folder_grant(path: str) -> Dict[str, Any]:
    if not folder_grants.revoke(path):
        raise HTTPException(status_code=404, detail="Folder not granted")
    return {"revoked": path}


__all__ = ["router"]

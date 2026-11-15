"""Security, Action Journal, Capabilities, and Model Router endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.core.capabilities import RuntimeCapabilityRegistry
from openjarvis.engine.router import ModelRouter
from openjarvis.security.action_journal import ActionJournal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["security", "capabilities", "models"])

_journal: Optional[ActionJournal] = None
_model_router: Optional[ModelRouter] = None


def get_action_journal() -> ActionJournal:
    global _journal
    if _journal is None:
        _journal = ActionJournal()
    return _journal


def get_model_router(request: Optional[Request] = None) -> ModelRouter:
    global _model_router
    if _model_router is None:
        engine = getattr(request.app.state, "engine", None) if request else None
        _model_router = ModelRouter(engine=engine)
    return _model_router


class ModelSwitchRequest(BaseModel):
    model: str


class UndoActionRequest(BaseModel):
    reason: Optional[str] = None


# --- Capabilities ---


@router.get("/v1/capabilities")
async def get_system_capabilities(request: Request) -> Dict[str, Any]:
    """Return runtime host hardware, active speech state, tool counts, and security policies."""
    try:
        return RuntimeCapabilityRegistry.get_capabilities(request.app.state)
    except Exception as exc:
        logger.error("Failed to retrieve capabilities: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# --- Action Journal & Undo ---


@router.get("/v1/security/actions")
async def list_security_actions(
    limit: int = 50,
    tool_name: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """List recent privileged actions recorded in the Action Journal."""
    journal = get_action_journal()
    try:
        actions = journal.list_actions(limit=limit, tool_name=tool_name, status=status)
        return {
            "actions": [a.to_dict() for a in actions],
            "count": len(actions),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/v1/security/actions/{action_id}")
async def get_security_action(action_id: str) -> Dict[str, Any]:
    """Retrieve details of a specific action record."""
    journal = get_action_journal()
    action = journal.get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action record not found")
    return action.to_dict()


@router.post("/v1/security/actions/{action_id}/undo")
async def undo_security_action(action_id: str) -> Dict[str, Any]:
    """Compensate or rollback an executed action (e.g. restore file snapshot)."""
    journal = get_action_journal()
    res = journal.undo_action(action_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Undo failed"))
    return res


# --- Model Routing ---


@router.get("/v1/models")
async def list_models(request: Request) -> Dict[str, Any]:
    """List available and currently active AI models."""
    router_inst = get_model_router(request)
    return router_inst.list_models()


@router.get("/v1/models/current")
async def get_current_model(request: Request) -> Dict[str, Any]:
    """Get the currently active model."""
    router_inst = get_model_router(request)
    return {"active_model": router_inst.active_model}


@router.post("/v1/models/switch")
async def switch_model(req: ModelSwitchRequest, request: Request) -> Dict[str, Any]:
    """Switch active model with low-VRAM safeguards."""
    router_inst = get_model_router(request)
    res = router_inst.switch_model(req.model)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to switch model"))
    return res

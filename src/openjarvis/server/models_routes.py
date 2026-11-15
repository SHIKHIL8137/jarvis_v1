"""FastAPI REST routes for Dynamic Model Intelligence, Hardware-Aware Allocation, and Model Policies."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from openjarvis.models.capabilities import ModelPolicy
from openjarvis.models.discovery import ModelDiscoveryEngine, get_model_discovery_engine
from openjarvis.models.selector import ModelSelectionEngine, get_model_selection_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/models", tags=["models"])


class PolicyUpdateRequest(BaseModel):
    policy: str


class ModelSelectRequest(BaseModel):
    task_type: str = "general"
    agent_id: Optional[str] = None
    requires_tools: bool = False
    requires_vision: bool = False
    requires_reasoning: bool = False
    max_cost_per_m: Optional[float] = None


class ModelSwitchRequest(BaseModel):
    model: str
    agent_id: Optional[str] = None


@router.get("/discovered")
async def list_discovered_models(
    refresh: bool = Query(default=False, description="Force re-discovery across providers"),
) -> Dict[str, Any]:
    """List all dynamically discovered local and cloud models with capability metadata."""
    engine = get_model_discovery_engine()
    if refresh:
        models = engine.discover_all()
    else:
        models = engine.get_all_models()
        if not models:
            models = engine.discover_all()

    local_count = sum(1 for m in models if m.is_local)
    cloud_count = sum(1 for m in models if not m.is_local)

    return {
        "total": len(models),
        "local_count": local_count,
        "cloud_count": cloud_count,
        "models": [m.to_dict() for m in models],
    }


@router.get("/providers")
async def list_providers_status() -> Dict[str, Any]:
    """List status and connectivity health of all local and cloud inference providers."""
    engine = get_model_discovery_engine()
    return {"providers": engine.list_providers()}


@router.get("/policy")
async def get_allocation_policy() -> Dict[str, Any]:
    """Get the active model allocation policy."""
    selector = get_model_selection_engine()
    return {
        "active_policy": selector.policy.value,
        "available_policies": [p.value for p in ModelPolicy],
        "vram_limit_mb": selector.vram_limit_mb,
        "max_concurrent_inferences": selector.max_concurrent_inferences,
    }


@router.post("/policy")
async def set_allocation_policy(req: PolicyUpdateRequest) -> Dict[str, Any]:
    """Update the active model allocation policy."""
    try:
        new_policy = ModelPolicy(req.policy.lower().strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid policy '{req.policy}'. Valid options: {[p.value for p in ModelPolicy]}",
        )

    selector = get_model_selection_engine()
    selector.policy = new_policy
    logger.info("Updated model allocation policy to %s", new_policy.value)
    return {"status": "ok", "active_policy": selector.policy.value}


@router.post("/select")
async def explain_model_selection(req: ModelSelectRequest) -> Dict[str, Any]:
    """Explain why a specific model is selected for a given task or specialist agent."""
    selector = get_model_selection_engine()
    res = selector.select_model(
        task_type=req.task_type,
        agent_id=req.agent_id,
        requires_tools=req.requires_tools,
        requires_vision=req.requires_vision,
        requires_reasoning=req.requires_reasoning,
        max_cost_per_m=req.max_cost_per_m,
    )
    return res.to_dict()


@router.post("/switch")
async def switch_model(req: ModelSwitchRequest, request: Request) -> Dict[str, Any]:
    """Manually switch active model or set specialist agent override."""
    clean_model = req.model.strip()
    if not clean_model:
        raise HTTPException(status_code=400, detail="Model name cannot be empty")

    if req.agent_id:
        from openjarvis.workforce.registry import get_workforce_registry
        reg = get_workforce_registry()
        manifest = reg.get_agent_manifest(req.agent_id)
        if not manifest:
            raise HTTPException(status_code=404, detail=f"Agent '{req.agent_id}' not found")
        manifest.model_preference = clean_model
        reg.store.save_manifest(manifest)
        reg.record_heartbeat(req.agent_id, active_model=clean_model)
        return {
            "status": "switched",
            "agent_id": req.agent_id,
            "active_model": clean_model,
        }

    # Global router switch
    router_inst = getattr(request.app.state, "router", None)
    if router_inst and hasattr(router_inst, "switch_model"):
        res = router_inst.switch_model(clean_model)
        return res

    return {"status": "switched", "active_model": clean_model}


@router.get("/hardware")
async def get_hardware_profile() -> Dict[str, Any]:
    """Retrieve host hardware constraints and active inference slots."""
    from openjarvis.core.config import detect_hardware
    selector = get_model_selection_engine()
    hw = detect_hardware()
    gpu_str = f"{hw.gpu.name} ({hw.gpu.vram_gb} GB)" if hw.gpu else "None detected (CPU mode)"
    cpu_str = f"{hw.cpu_brand} ({hw.cpu_count} cores)" if hw.cpu_brand else f"CPU ({hw.cpu_count} cores)"
    return {
        "vram_limit_mb": selector.vram_limit_mb,
        "max_concurrent_inferences": selector.max_concurrent_inferences,
        "active_inferences": selector._active_inferences,
        "gpu_detected": gpu_str,
        "cpu_profile": cpu_str,
    }

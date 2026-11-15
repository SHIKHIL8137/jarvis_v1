"""FastAPI REST routes for Agent Workforce management, status, heartbeats, and delegation."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.workforce.models import AgentRuntimeState
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry, get_workforce_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workforce", tags=["workforce"])


def _get_registry(request: Optional[Request] = None) -> AgentWorkforceRegistry:
    if request and hasattr(request.app.state, "workforce_registry") and request.app.state.workforce_registry:
        return request.app.state.workforce_registry
    return get_workforce_registry()


class HeartbeatRequest(BaseModel):
    current_task_id: Optional[str] = None
    active_model: Optional[str] = None
    error: Optional[str] = None


class TeamTaskRequest(BaseModel):
    goal: str
    priority: str = "normal"
    assigned_agent: Optional[str] = None


class QueryRequest(BaseModel):
    query: str


# --- Agent Registry & Summary Endpoints ---


@router.get("/agents")
async def list_workforce_agents(request: Request) -> Dict[str, Any]:
    """List all agents in the workforce with their live runtime state and heartbeat."""
    reg = _get_registry(request)
    return {"agents": reg.list_agents()}


@router.get("/agents/{agent_id}")
async def get_workforce_agent(agent_id: str, request: Request) -> Dict[str, Any]:
    """Retrieve full manifest and live runtime status of a specific agent."""
    reg = _get_registry(request)
    manifest = reg.get_agent_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found in workforce")

    st = reg.get_agent_state(agent_id)
    hb = reg.get_agent_heartbeat(agent_id)
    return {
        "manifest": manifest.to_dict(),
        "status": st.value,
        "heartbeat": hb.to_dict() if hb else None,
    }


@router.post("/agents/{agent_id}/enable")
async def enable_agent(agent_id: str, request: Request) -> Dict[str, Any]:
    """Enable an agent and transition it to IDLE/ONLINE."""
    reg = _get_registry(request)
    manifest = reg.get_agent_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Agent not found")
    manifest.enabled = True
    reg.store.save_manifest(manifest)
    reg.set_agent_state(agent_id, AgentRuntimeState.IDLE)
    return {"status": "enabled", "agent_id": agent_id}


@router.post("/agents/{agent_id}/disable")
async def disable_agent(agent_id: str, request: Request) -> Dict[str, Any]:
    """Disable an agent and transition it to OFFLINE."""
    reg = _get_registry(request)
    manifest = reg.get_agent_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Agent not found")
    manifest.enabled = False
    reg.store.save_manifest(manifest)
    reg.set_agent_state(agent_id, AgentRuntimeState.OFFLINE)
    return {"status": "disabled", "agent_id": agent_id}


@router.post("/agents/{agent_id}/heartbeat")
async def post_heartbeat(agent_id: str, req: HeartbeatRequest, request: Request) -> Dict[str, Any]:
    """Record heartbeat from an active specialist worker."""
    reg = _get_registry(request)
    reg.record_heartbeat(
        agent_id=agent_id,
        current_task_id=req.current_task_id,
        active_model=req.active_model,
        error=req.error,
    )
    return {"status": "ok", "agent_id": agent_id}


@router.get("/summary")
async def get_team_summary(request: Request) -> Dict[str, Any]:
    """Get aggregated presence summary: total registered, online, busy, idle, degraded, offline."""
    reg = _get_registry(request)
    summary = reg.get_team_summary()
    return summary.to_dict()


# --- Natural Language Status Query ---


@router.post("/query")
async def answer_status_query(req: QueryRequest, request: Request) -> Dict[str, Any]:
    """Answer natural language queries about agent presence and workload."""
    reg = _get_registry(request)
    answer = reg.answer_status_query(req.query)
    if not answer:
        answer = "I am ready to assist you, sir. The agent team is standing by."
    return {"query": req.query, "answer": answer}


@router.get("/announcement")
async def get_startup_announcement(request: Request) -> Dict[str, Any]:
    """Generate the natural language startup announcement text."""
    reg = _get_registry(request)
    return {"announcement": reg.generate_startup_announcement()}


# --- Multi-Agent Task Delegation ---


@router.post("/tasks")
async def delegate_team_task(req: TeamTaskRequest, request: Request) -> Dict[str, Any]:
    """Atlas decomposes request into multi-agent task DAG and initiates execution."""
    reg = _get_registry(request)
    engine = getattr(request.app.state, "engine", None)
    task_engine = getattr(request.app.state, "task_engine", None)

    orchestrator = AtlasOrchestrator(registry=reg, bus=getattr(request.app.state, "bus", None))
    steps = orchestrator.plan_task(req.goal, engine=engine)

    if task_engine:
        # Delegate to background task engine for persistent execution
        task = task_engine.create_task(
            title=f"Multi-Agent: {req.goal[:40]}",
            user_request=req.goal,
            agent_profile="general",
        )
        task_engine.start_task(task.id, plan_steps=steps)
        return {
            "status": "delegated",
            "task_id": task.id,
            "goal": req.goal,
            "steps": [s.to_dict() for s in steps],
        }

    return {
        "status": "planned",
        "goal": req.goal,
        "steps": [s.to_dict() for s in steps],
    }


@router.get("/tasks/{task_id}/timeline")
async def get_task_timeline(task_id: str, request: Request) -> Dict[str, Any]:
    """Retrieve message and report execution timeline for a task."""
    reg = _get_registry(request)
    messages = reg.store.list_messages(task_id)
    reports = reg.store.list_reports(task_id=task_id)
    return {
        "task_id": task_id,
        "messages": messages,
        "reports": [r.to_dict() for r in reports],
    }


@router.get("/reports")
async def list_reports(
    request: Request,
    task_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List structured specialist reports."""
    reg = _get_registry(request)
    reports = reg.store.list_reports(task_id=task_id, agent_id=agent_id)
    return {"reports": [r.to_dict() for r in reports]}

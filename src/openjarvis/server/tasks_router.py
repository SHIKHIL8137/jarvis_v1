"""REST API endpoints for persistent autonomous background tasks."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.tasks.engine import BackgroundTaskEngine, get_task_engine
from openjarvis.tasks.models import TaskBudget

logger = logging.getLogger(__name__)

tasks_router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    title: str
    user_request: str
    agent_profile: str = "general"
    max_execution_time_seconds: Optional[float] = 1800.0
    max_retries: Optional[int] = 3
    max_tool_calls: Optional[int] = 50
    context: Optional[Dict[str, Any]] = None


class SteerTaskRequest(BaseModel):
    instruction: str


def _get_engine(request: Request) -> BackgroundTaskEngine:
    engine = getattr(request.app.state, "task_engine", None)
    if engine is None:
        bus = getattr(request.app.state, "bus", None)
        engine = get_task_engine(bus=bus)
        request.app.state.task_engine = engine
    return engine


@tasks_router.post("")
async def create_background_task(req: CreateTaskRequest, request: Request):
    """Create a new persistent background task."""
    engine = _get_engine(request)
    budget = TaskBudget(
        max_execution_time_seconds=req.max_execution_time_seconds or 1800.0,
        max_retries=req.max_retries or 3,
        max_tool_calls=req.max_tool_calls or 50,
    )
    task = engine.create_task(
        title=req.title,
        user_request=req.user_request,
        agent_profile=req.agent_profile,
        budget=budget,
        context=req.context,
    )
    return task.to_dict()


@tasks_router.get("")
async def list_background_tasks(request: Request, status: Optional[str] = None, limit: int = 50):
    """List background tasks."""
    engine = _get_engine(request)
    tasks = engine.store.list_tasks(status=status, limit=limit)
    return {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)}


@tasks_router.get("/{task_id}")
async def get_background_task(task_id: str, request: Request):
    """Get full details of a background task."""
    engine = _get_engine(request)
    task = engine.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@tasks_router.post("/{task_id}/start")
async def start_background_task(task_id: str, request: Request):
    """Start execution of a background task."""
    engine = _get_engine(request)
    success = engine.start_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot start task in current state")
    return {"status": "started", "task_id": task_id}


@tasks_router.post("/{task_id}/pause")
async def pause_background_task(task_id: str, request: Request):
    """Pause an active background task."""
    engine = _get_engine(request)
    success = engine.pause_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot pause task")
    return {"status": "paused", "task_id": task_id}


@tasks_router.post("/{task_id}/resume")
async def resume_background_task(task_id: str, request: Request):
    """Resume a paused background task."""
    engine = _get_engine(request)
    success = engine.resume_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot resume task")
    return {"status": "resumed", "task_id": task_id}


@tasks_router.post("/{task_id}/cancel")
async def cancel_background_task(task_id: str, request: Request):
    """Cancel a background task."""
    engine = _get_engine(request)
    success = engine.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot cancel task")
    return {"status": "cancelled", "task_id": task_id}


@tasks_router.post("/{task_id}/steer")
async def steer_background_task(task_id: str, req: SteerTaskRequest, request: Request):
    """Provide real-time steering instructions to a background task."""
    engine = _get_engine(request)
    success = engine.steer_task(task_id, req.instruction)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "steered", "task_id": task_id, "instruction": req.instruction}


@tasks_router.post("/{task_id}/skip")
async def skip_background_task_step(task_id: str, request: Request):
    """Skip the current step of an active task."""
    engine = _get_engine(request)
    success = engine.skip_step(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot skip step")
    return {"status": "step_skipped", "task_id": task_id}


@tasks_router.get("/{task_id}/events")
async def get_task_events(task_id: str, request: Request):
    """Get operational audit events for a task."""
    engine = _get_engine(request)
    events = engine.store.get_events(task_id)
    return {"events": events, "count": len(events)}


@tasks_router.get("/{task_id}/artifacts")
async def get_task_artifacts(task_id: str, request: Request):
    """Get generated artifacts for a task."""
    engine = _get_engine(request)
    artifacts = engine.store.list_artifacts(task_id)
    return {"artifacts": artifacts, "count": len(artifacts)}

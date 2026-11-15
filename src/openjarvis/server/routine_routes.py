"""REST API endpoints for routines and quiet-hours management."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.routines.engine import RoutineEngine, get_routine_engine
from openjarvis.routines.models import RoutineType

logger = logging.getLogger(__name__)

routine_router = APIRouter(prefix="/v1/routines", tags=["routines"])


class CreateRoutineRequest(BaseModel):
    name: str
    description: str = ""
    routine_type: str = "daily"
    schedule_value: str = "09:00"
    action_type: str = "task"
    action_payload: Optional[Dict[str, Any]] = None
    timezone: str = "Asia/Kolkata"
    is_voice_announcement: bool = False


class UpdateRoutineRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_value: Optional[str] = None
    action_type: Optional[str] = None
    action_payload: Optional[Dict[str, Any]] = None
    timezone: Optional[str] = None
    is_voice_announcement: Optional[bool] = None
    enabled: Optional[bool] = None


class QuietHoursConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    timezone: Optional[str] = None


def _get_engine(request: Request) -> RoutineEngine:
    engine = getattr(request.app.state, "routine_engine", None)
    if engine is None:
        bus = getattr(request.app.state, "bus", None)
        engine = get_routine_engine(bus=bus)
        request.app.state.routine_engine = engine
    return engine


@routine_router.post("")
async def create_routine(req: CreateRoutineRequest, request: Request):
    """Create and schedule a new routine."""
    engine = _get_engine(request)
    try:
        rtype = RoutineType(req.routine_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid routine_type: {req.routine_type}")

    routine = engine.create_routine(
        name=req.name,
        description=req.description,
        routine_type=rtype,
        schedule_value=req.schedule_value,
        action_type=req.action_type,
        action_payload=req.action_payload,
        timezone=req.timezone,
        is_voice_announcement=req.is_voice_announcement,
    )
    return routine.to_dict()


@routine_router.get("")
async def list_routines(request: Request, enabled_only: bool = False):
    """List routines."""
    engine = _get_engine(request)
    routines = engine.store.list_routines(enabled_only=enabled_only)
    return {"routines": [r.to_dict() for r in routines], "count": len(routines)}


@routine_router.get("/quiet-hours")
async def get_quiet_hours(request: Request):
    """Get quiet hours settings and active status."""
    engine = _get_engine(request)
    return engine.quiet_hours.to_dict()


@routine_router.post("/quiet-hours")
async def update_quiet_hours(req: QuietHoursConfigRequest, request: Request):
    """Update quiet hours settings."""
    engine = _get_engine(request)
    if req.enabled is not None:
        engine.quiet_hours.enabled = req.enabled
    if req.start_time is not None:
        engine.quiet_hours.start_time_str = req.start_time
    if req.end_time is not None:
        engine.quiet_hours.end_time_str = req.end_time
    if req.timezone is not None:
        engine.quiet_hours.timezone_name = req.timezone
    return engine.quiet_hours.to_dict()


@routine_router.get("/{routine_id}")
async def get_routine(routine_id: str, request: Request):
    """Get a specific routine."""
    engine = _get_engine(request)
    routine = engine.store.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    return routine.to_dict()


@routine_router.put("/{routine_id}")
async def update_routine(routine_id: str, req: UpdateRoutineRequest, request: Request):
    """Update routine configuration."""
    engine = _get_engine(request)
    routine = engine.store.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")

    if req.name is not None:
        routine.name = req.name
    if req.description is not None:
        routine.description = req.description
    if req.schedule_value is not None:
        routine.schedule_value = req.schedule_value
    if req.action_type is not None:
        routine.action_type = req.action_type
    if req.action_payload is not None:
        routine.action_payload = req.action_payload
    if req.timezone is not None:
        routine.timezone = req.timezone
    if req.is_voice_announcement is not None:
        routine.is_voice_announcement = req.is_voice_announcement
    if req.enabled is not None:
        routine.enabled = req.enabled

    engine.store.save_routine(routine)
    return routine.to_dict()


@routine_router.post("/{routine_id}/pause")
async def pause_routine(routine_id: str, request: Request):
    """Pause a routine."""
    engine = _get_engine(request)
    routine = engine.store.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    routine.enabled = False
    engine.store.save_routine(routine)
    return {"status": "paused", "id": routine_id}


@routine_router.post("/{routine_id}/resume")
async def resume_routine(routine_id: str, request: Request):
    """Resume a paused routine."""
    engine = _get_engine(request)
    routine = engine.store.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    routine.enabled = True
    engine.store.save_routine(routine)
    return {"status": "resumed", "id": routine_id}


@routine_router.delete("/{routine_id}")
async def delete_routine(routine_id: str, request: Request):
    """Delete a routine."""
    engine = _get_engine(request)
    success = engine.store.delete_routine(routine_id)
    if not success:
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"status": "deleted", "id": routine_id}


@routine_router.post("/{routine_id}/run")
async def trigger_routine(routine_id: str, request: Request):
    """Manually trigger a routine execution."""
    engine = _get_engine(request)
    run_record = engine.trigger_routine(routine_id)
    if not run_record:
        raise HTTPException(status_code=404, detail="Routine not found")
    return run_record.to_dict()


@routine_router.get("/{routine_id}/runs")
async def get_routine_runs(routine_id: str, request: Request, limit: int = 50):
    """Get execution history for a routine."""
    engine = _get_engine(request)
    runs = engine.store.list_runs(routine_id=routine_id, limit=limit)
    return {"runs": [r.to_dict() for r in runs], "count": len(runs)}

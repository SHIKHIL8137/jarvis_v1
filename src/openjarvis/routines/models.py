"""Data models for unified routines, scheduling, and execution tracking."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RoutineType(str, Enum):
    ONE_TIME = "one_time"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    INTERVAL = "interval"
    STARTUP = "startup"
    INACTIVITY = "inactivity"
    CRON = "cron"


class RoutineRunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED_QUIET_HOURS = "skipped_quiet_hours"
    QUEUED = "queued"
    MISSED_CATCHUP = "missed_catchup"


@dataclass
class Routine:
    """A scheduled or triggered routine."""

    id: str
    name: str
    description: str = ""
    routine_type: RoutineType = RoutineType.DAILY
    schedule_value: str = "09:00"  # Time "09:00", interval in secs "3600", or cron "0 9 * * *"
    action_type: str = "task"  # task, speech, command, notification
    action_payload: Dict[str, Any] = field(default_factory=dict)
    timezone: str = "Asia/Kolkata"
    enabled: bool = True
    is_voice_announcement: bool = False
    created_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    run_count: int = 0
    fail_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["routine_type"] = self.routine_type.value if isinstance(self.routine_type, RoutineType) else str(self.routine_type)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Routine:
        rtype_val = data.get("routine_type", "daily")
        try:
            rtype = RoutineType(rtype_val)
        except ValueError:
            rtype = RoutineType.DAILY
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            routine_type=rtype,
            schedule_value=str(data.get("schedule_value", "09:00")),
            action_type=data.get("action_type", "task"),
            action_payload=data.get("action_payload") or {},
            timezone=data.get("timezone", "Asia/Kolkata"),
            enabled=bool(data.get("enabled", True)),
            is_voice_announcement=bool(data.get("is_voice_announcement", False)),
            created_at=data.get("created_at", time.time()),
            last_run=data.get("last_run"),
            next_run=data.get("next_run"),
            run_count=data.get("run_count", 0),
            fail_count=data.get("fail_count", 0),
        )


@dataclass
class RoutineRun:
    """An execution run record for a routine."""

    id: str
    routine_id: str
    status: RoutineRunStatus
    executed_at: float = field(default_factory=time.time)
    duration_seconds: float = 0.0
    result_summary: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, RoutineRunStatus) else str(self.status)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RoutineRun:
        status_val = data.get("status", "success")
        try:
            status = RoutineRunStatus(status_val)
        except ValueError:
            status = RoutineRunStatus.SUCCESS
        return cls(
            id=data.get("id", ""),
            routine_id=data.get("routine_id", ""),
            status=status,
            executed_at=data.get("executed_at", time.time()),
            duration_seconds=data.get("duration_seconds", 0.0),
            result_summary=data.get("result_summary", ""),
            error=data.get("error"),
        )

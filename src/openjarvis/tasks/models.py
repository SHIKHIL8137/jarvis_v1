"""Data models and state representations for background tasks."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    """Lifecycle states for persistent autonomous background tasks."""

    CREATED = "created"
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RETRYING = "retrying"
    PAUSED = "paused"
    BLOCKED = "blocked"
    RECOVERING = "recovering"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    """A single execution step in a task's plan."""

    step_id: str
    step_number: int
    title: str
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    tool_name: str = ""
    tool_params: Dict[str, Any] = field(default_factory=dict)
    tool_result: Optional[Dict[str, Any]] = None
    idempotency_key: str = ""
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    assigned_agent: str = "atlas"
    dependencies: List[str] = field(default_factory=list)
    expected_output: str = ""
    verification_criteria: str = ""
    parent_task_id: Optional[str] = None
    child_task_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, StepStatus) else str(self.status)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskStep:
        status_val = data.get("status", "pending")
        try:
            status = StepStatus(status_val)
        except ValueError:
            status = StepStatus.PENDING
        return cls(
            step_id=data.get("step_id", ""),
            step_number=data.get("step_number", 0),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=status,
            tool_name=data.get("tool_name", ""),
            tool_params=data.get("tool_params") or {},
            tool_result=data.get("tool_result"),
            idempotency_key=data.get("idempotency_key", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            assigned_agent=data.get("assigned_agent", "atlas"),
            dependencies=data.get("dependencies") or [],
            expected_output=data.get("expected_output", ""),
            verification_criteria=data.get("verification_criteria", ""),
            parent_task_id=data.get("parent_task_id"),
            child_task_id=data.get("child_task_id"),
        )


@dataclass
class TaskBudget:
    """Execution and resource limits for a background task."""

    max_execution_time_seconds: float = 1800.0  # 30 mins
    max_retries: int = 3
    max_tool_calls: int = 50
    max_tokens: int = 100_000
    current_tokens_used: int = 0
    current_tool_calls: int = 0
    current_retries: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskBudget:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TaskCheckpoint:
    """Persistent snapshot of task execution state."""

    checkpoint_id: str
    task_id: str
    step_index: int
    plan: List[Dict[str, Any]] = field(default_factory=list)
    completed_steps: List[Dict[str, Any]] = field(default_factory=list)
    pending_steps: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BackgroundTask:
    """A persistent background autonomous task."""

    id: str
    title: str
    user_request: str
    status: TaskStatus = TaskStatus.CREATED
    agent_profile: str = "general"  # research, coding, general, monitor
    plan: List[TaskStep] = field(default_factory=list)
    current_step_index: int = 0
    budget: TaskBudget = field(default_factory=TaskBudget)
    context: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    approval_required: bool = False
    verification_status: str = "unverified"  # unverified, verified, failed
    steering_instructions: List[str] = field(default_factory=list)
    parent_task_id: Optional[str] = None
    child_task_ids: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "user_request": self.user_request,
            "status": self.status.value if isinstance(self.status, TaskStatus) else str(self.status),
            "agent_profile": self.agent_profile,
            "plan": [step.to_dict() for step in self.plan],
            "current_step_index": self.current_step_index,
            "budget": self.budget.to_dict(),
            "context": self.context,
            "artifacts": self.artifacts,
            "approval_required": self.approval_required,
            "verification_status": self.verification_status,
            "steering_instructions": self.steering_instructions,
            "parent_task_id": self.parent_task_id,
            "child_task_ids": self.child_task_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BackgroundTask:
        status_val = data.get("status", "created")
        try:
            status = TaskStatus(status_val)
        except ValueError:
            status = TaskStatus.CREATED

        plan_raw = data.get("plan") or []
        plan = [TaskStep.from_dict(s) if isinstance(s, dict) else s for s in plan_raw]

        budget_raw = data.get("budget") or {}
        budget = TaskBudget.from_dict(budget_raw) if isinstance(budget_raw, dict) else TaskBudget()

        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            user_request=data.get("user_request", ""),
            status=status,
            agent_profile=data.get("agent_profile", "general"),
            plan=plan,
            current_step_index=data.get("current_step_index", 0),
            budget=budget,
            context=data.get("context") or {},
            artifacts=data.get("artifacts") or [],
            approval_required=bool(data.get("approval_required", False)),
            verification_status=data.get("verification_status", "unverified"),
            steering_instructions=data.get("steering_instructions") or [],
            parent_task_id=data.get("parent_task_id"),
            child_task_ids=data.get("child_task_ids") or [],
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
        )

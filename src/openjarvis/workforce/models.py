"""Data models, enums, and manifest structures for the Agent Workforce."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentRuntimeState(str, Enum):
    """11 discrete runtime states for workforce agents."""

    REGISTERED = "registered"
    INITIALIZING = "initializing"
    ONLINE = "online"
    IDLE = "idle"
    BUSY = "busy"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass
class AgentManifest:
    """Configurable definition manifest for an agent in the workforce.
    
    Agent identity is strictly independent of model identity. Required capabilities
    and execution constraints are defined here; models are dynamically allocated
    per-task by the ModelSelectionEngine.
    """

    id: str
    display_name: str
    role: str
    description: str
    system_instructions: str
    required_capabilities: List[str] = field(default_factory=list)
    model_preference: Optional[str] = None  # Optional user override only
    model_fallback: Optional[str] = None  # Optional user override only
    local_only: bool = False
    min_context_tokens: int = 4096
    allowed_tools: List[str] = field(default_factory=list)
    denied_tools: List[str] = field(default_factory=list)
    allowed_capabilities: List[str] = field(default_factory=list)
    denied_capabilities: List[str] = field(default_factory=list)
    max_execution_time_seconds: int = 300
    max_tool_calls: int = 25
    max_concurrent_tasks: int = 2
    resource_requirements: Dict[str, Any] = field(default_factory=lambda: {"vram_mb": 0, "cpu_cores": 1})
    health_check_policy: str = "standard"
    enabled: bool = True
    version: str = "1.0.0"
    avatar: str = "bot"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentManifest:
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class AgentHeartbeat:
    """Live heartbeat telemetric for tracking real runtime health and presence."""

    agent_id: str
    status: AgentRuntimeState = AgentRuntimeState.IDLE
    timestamp: float = field(default_factory=time.time)
    current_task_id: Optional[str] = None
    active_model: str = ""
    runtime_health: str = "healthy"  # healthy | degraded | failing
    last_error: Optional[str] = None
    available_tools: List[str] = field(default_factory=list)
    current_workload: int = 0  # Number of active assigned subtasks

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class AgentReport:
    """Structured report returned by a specialist upon task or step execution."""

    agent_id: str
    task_id: str
    step_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    work_performed: str = ""
    findings: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    files_inspected: List[str] = field(default_factory=list)
    files_changed: List[str] = field(default_factory=list)
    tests_executed: List[Dict[str, Any]] = field(default_factory=list)
    results: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    remaining_work: Optional[str] = None
    confidence: float = 1.0
    suggested_next_action: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentReport:
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class TeamSummary:
    """Aggregated team availability and workload overview."""

    total_registered: int = 0
    total_online: int = 0
    total_idle: int = 0
    total_busy: int = 0
    total_waiting_approval: int = 0
    total_degraded: int = 0
    total_offline: int = 0
    total_failed: int = 0
    active_tasks: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

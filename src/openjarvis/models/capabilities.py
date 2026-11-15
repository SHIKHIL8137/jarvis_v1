"""Standardized model capability definitions, metadata schemas, and policy enums."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskType(str, Enum):
    """Classification of tasks for capability-driven model routing."""

    GENERAL = "general"
    CHAT = "chat"
    PLANNING = "planning"
    CODING = "coding"
    RESEARCH = "research"
    SECURITY = "security"
    VERIFICATION = "verification"
    OPERATIONS = "operations"
    VISION = "vision"
    VOICE = "voice"


class ModelPolicy(str, Enum):
    """User-configured or system default model selection policy."""

    LOCAL_FIRST = "local-first"
    CLOUD_FIRST = "cloud-first"
    BALANCED = "balanced"
    LOWEST_COST = "lowest-cost"
    LOWEST_LATENCY = "lowest-latency"
    HIGHEST_CAPABILITY = "highest-capability"
    PRIVACY_FIRST = "privacy-first"


@dataclass
class ModelCapabilityInfo:
    """Standardized representation of an available model's capabilities and operational parameters."""

    model_id: str
    provider: str
    display_name: str = ""
    is_local: bool = True
    is_free: bool = True
    price_per_m_out: float = 0.0  # USD per million tokens
    price_per_m_in: float = 0.0
    context_length: int = 8192
    max_output_tokens: int = 4096
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_reasoning: bool = False
    supports_coding: bool = False
    supports_vision: bool = False
    supports_audio_in: bool = False
    supports_audio_out: bool = False
    supports_streaming: bool = True
    health_status: str = "healthy"  # "healthy" | "degraded" | "offline"
    latency_ms: float = 0.0
    vram_mb_required: int = 0
    parameter_size_b: float = 0.0
    quantization: str = ""
    privacy_classification: str = "local"  # "local" | "cloud-enterprise" | "cloud-public"
    confirmed_capabilities: List[str] = field(default_factory=list)
    provider_declared_capabilities: List[str] = field(default_factory=list)
    last_checked: float = field(default_factory=time.time)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelCapabilityInfo:
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class SelectionResult:
    """Outcome of intelligent model selection explaining why a model was chosen."""

    model_id: str
    provider: str
    task_type: str
    reason: str
    policy: str
    is_local: bool
    context_length: int
    score: float
    alternatives: List[str] = field(default_factory=list)
    candidate_scores: Dict[str, float] = field(default_factory=dict)
    hardware_notes: str = ""
    selected_at: float = field(default_factory=time.time)

    @property
    def model_name(self) -> str:
        return self.model_id

    @property
    def rationale(self) -> str:
        return self.reason

    @property
    def policy_used(self) -> str:
        return self.policy

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["model_name"] = self.model_id
        d["rationale"] = self.reason
        d["policy_used"] = self.policy
        return d

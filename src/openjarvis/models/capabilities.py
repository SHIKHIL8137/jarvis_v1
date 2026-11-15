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


class CapabilityProvenance(str, Enum):
    """Origin and confidence level of a model capability declaration."""

    CONFIRMED = "confirmed"  # Verified via live benchmark, test call, or runtime validation
    PROVIDER_DECLARED = "provider_declared"  # Declared directly by provider model endpoint/card
    INFERRED = "inferred"  # Inferred through architecture/naming heuristics (e.g. 'coder', 'r1')
    UNKNOWN = "unknown"  # Unverified or unsupported; must never be assumed supported


class CapabilityConfidencePolicy(str, Enum):
    """Enforceable confidence policies for verifying model capabilities."""

    CONFIRMED_ONLY = "confirmed_only"               # Only capabilities proven by runtime execution
    DECLARED_OR_CONFIRMED = "declared_or_confirmed" # Confirmed or provider-declared capabilities
    ALLOW_INFERRED = "allow_inferred"               # Allow inferred heuristics, but reject unknown


class NoCompatibleModelAvailable(Exception):
    """Raised when no discovered model satisfies task constraints, privacy policies, or hardware budgets."""

    def __init__(self, message: str = "No compatible model available", task_type: str = "general", policy: str = "balanced") -> None:
        super().__init__(message)
        self.message = message
        self.task_type = task_type
        self.policy = policy


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
    provenance: Dict[str, str] = field(default_factory=dict)
    last_checked: float = field(default_factory=time.time)
    error_message: Optional[str] = None

    def has_capability(
        self,
        cap: str,
        allow_inferred: bool = True,
        confidence_policy: CapabilityConfidencePolicy | str = CapabilityConfidencePolicy.ALLOW_INFERRED,
    ) -> bool:
        """Strict capability verification. Unknown capability provenance is never treated as confirmed."""
        norm_cap = cap.lower().strip()
        if norm_cap in ("tool_calling", "function_calling"):
            norm_cap = "tools"
        elif norm_cap in ("json_mode",):
            norm_cap = "structured_output"
        elif norm_cap in ("thinking",):
            norm_cap = "reasoning"
        elif norm_cap in ("code",):
            norm_cap = "coding"
        elif norm_cap in ("multimodal",):
            norm_cap = "vision"

        # If a fundamental capability flag is False, the model cannot have that capability
        if norm_cap == "tools" and not self.supports_tools:
            return False
        if norm_cap == "vision" and not self.supports_vision:
            return False
        if norm_cap == "coding" and not self.supports_coding:
            return False
        if norm_cap == "reasoning" and not self.supports_reasoning:
            return False
        if norm_cap == "structured_output" and not self.supports_structured_output:
            return False

        prov = self.provenance.get(norm_cap, "")
        if not prov:
            if norm_cap in [c.lower() for c in self.confirmed_capabilities]:
                prov = CapabilityProvenance.CONFIRMED.value
            elif norm_cap in [c.lower() for c in self.provider_declared_capabilities]:
                prov = CapabilityProvenance.PROVIDER_DECLARED.value
            else:
                prov = CapabilityProvenance.INFERRED.value

        if prov == CapabilityProvenance.UNKNOWN.value:
            return False

        policy = confidence_policy if isinstance(confidence_policy, CapabilityConfidencePolicy) else CapabilityConfidencePolicy(str(confidence_policy))

        # 1. Confirmed capabilities satisfy all confidence policies
        if norm_cap in [c.lower() for c in self.confirmed_capabilities] or prov == CapabilityProvenance.CONFIRMED.value:
            return True

        if policy == CapabilityConfidencePolicy.CONFIRMED_ONLY:
            return False

        # 2. Provider-declared capabilities satisfy DECLARED_OR_CONFIRMED and ALLOW_INFERRED
        if norm_cap in [c.lower() for c in self.provider_declared_capabilities] or prov == CapabilityProvenance.PROVIDER_DECLARED.value:
            return True

        if policy == CapabilityConfidencePolicy.DECLARED_OR_CONFIRMED:
            return False

        # 3. Inferred capabilities satisfy ALLOW_INFERRED only when allow_inferred is True
        if allow_inferred and policy == CapabilityConfidencePolicy.ALLOW_INFERRED and prov == CapabilityProvenance.INFERRED.value:
            if norm_cap == "tools":
                return self.supports_tools
            if norm_cap == "structured_output":
                return self.supports_structured_output
            if norm_cap == "reasoning":
                return self.supports_reasoning
            if norm_cap == "coding":
                return self.supports_coding
            if norm_cap == "vision":
                return self.supports_vision
            if norm_cap == "audio_in":
                return self.supports_audio_in
            if norm_cap == "audio_out":
                return self.supports_audio_out
            if norm_cap == "streaming":
                return self.supports_streaming

        return False

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
    error: Optional[str] = None
    is_compatible: bool = True

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


class NoCompatibleModelAvailableResult(SelectionResult):
    """Explicit SelectionResult indicating no model was compatible."""

    def __init__(self, message: str = "No compatible model available", task_type: str = "general", policy: str = "balanced") -> None:
        super().__init__(
            model_id="",
            provider="none",
            task_type=task_type,
            reason=message,
            policy=policy,
            is_local=False,
            context_length=0,
            score=0.0,
            hardware_notes="No candidate model satisfies requirements and constraints.",
            error="NoCompatibleModelAvailable",
            is_compatible=False,
        )

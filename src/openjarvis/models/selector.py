"""Intelligent Model Selection Engine — task-based capability scoring, hardware-aware allocation,
shared model pooling, fail-closed privacy, and dynamic fallback.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from openjarvis.core.config import JarvisConfig, detect_hardware, load_config
from openjarvis.models.capabilities import (
    CapabilityConfidencePolicy,
    CapabilityProvenance,
    ModelCapabilityInfo,
    ModelPolicy,
    NoCompatibleModelAvailable,
    NoCompatibleModelAvailableResult,
    SelectionResult,
    TaskType,
)
from openjarvis.models.discovery import ModelDiscoveryEngine, get_model_discovery_engine

logger = logging.getLogger(__name__)

# Canonical mapping from agent identifiers to normalized task categories
AGENT_TO_TASK_MAP: Dict[str, str] = {
    "atlas": TaskType.PLANNING.value,
    "forge": TaskType.CODING.value,
    "oracle": TaskType.RESEARCH.value,
    "sentinel": TaskType.SECURITY.value,
    "scout": TaskType.GENERAL.value,
    "vanguard": TaskType.VERIFICATION.value,
    "pulse": TaskType.OPERATIONS.value,
}


class ModelSelectionEngine:
    """Selects the optimal model dynamically per task or execution step, respecting
    hardware boundaries, capability provenance, and configured privacy policies."""

    _instance: Optional[ModelSelectionEngine] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        discovery_engine: Optional[ModelDiscoveryEngine] = None,
        config: Optional[JarvisConfig] = None,
        default_policy: ModelPolicy = ModelPolicy.LOCAL_FIRST,
    ) -> None:
        self.config = config or load_config()
        self.discovery = discovery_engine or get_model_discovery_engine(self.config)
        self.policy = default_policy
        self._lock = threading.RLock()
        self._active_local_inferences: int = 0
        self._currently_loaded_local_model: Optional[str] = None
        self._failure_history: List[Dict[str, Any]] = []

        # Hardware-aware resource constraints detected dynamically
        self._hardware = detect_hardware()
        gpu_vram = self._hardware.gpu.vram_gb if self._hardware.gpu else 0.0
        if gpu_vram > 0:
            self._vram_limit_mb: int = int(gpu_vram * 1024 * 0.85)
            if gpu_vram >= 16.0:
                self._max_concurrent_local: int = 4
            elif gpu_vram >= 8.0:
                self._max_concurrent_local: int = 3
            elif gpu_vram >= 4.0:
                self._max_concurrent_local: int = 2
            else:
                self._max_concurrent_local: int = 1
        else:
            # CPU mode
            self._vram_limit_mb: int = 0
            self._max_concurrent_local: int = 1

    @property
    def vram_limit_mb(self) -> int:
        return self._vram_limit_mb

    @vram_limit_mb.setter
    def vram_limit_mb(self, val: int) -> None:
        self._vram_limit_mb = val

    @property
    def _active_inferences(self) -> int:
        return self._active_local_inferences

    @_active_inferences.setter
    def _active_inferences(self, val: int) -> None:
        self._active_local_inferences = val

    @property
    def max_concurrent_inferences(self) -> int:
        return self._max_concurrent_local

    @max_concurrent_inferences.setter
    def max_concurrent_inferences(self, val: int) -> None:
        self._max_concurrent_local = val

    @classmethod
    def get_instance(
        cls,
        discovery_engine: Optional[ModelDiscoveryEngine] = None,
        config: Optional[JarvisConfig] = None,
    ) -> ModelSelectionEngine:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(discovery_engine=discovery_engine, config=config)
            return cls._instance

    def select_model(
        self,
        task_type: str | TaskType = TaskType.GENERAL,
        required_capabilities: Optional[List[str]] = None,
        local_only: bool = False,
        preferred_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        min_context_tokens: int = 4096,
        agent_id: Optional[str] = None,
        requires_tools: bool = False,
        requires_vision: bool = False,
        requires_reasoning: bool = False,
        max_cost_per_m: Optional[float] = None,
        confidence_policy: CapabilityConfidencePolicy | str = CapabilityConfidencePolicy.ALLOW_INFERRED,
        raise_on_none: bool = False,
    ) -> SelectionResult:
        """Dynamically select the optimal available model based on capability requirements,
        system hardware, and operational policy. Zero hardcoded emergency fallbacks."""
        with self._lock:
            # 1. Normalize task type: map agent IDs to standardized task types
            raw_task = task_type.value if isinstance(task_type, TaskType) else str(task_type).lower().strip()
            t_str = AGENT_TO_TASK_MAP.get(raw_task, raw_task)

            # Map agent_id to task type if task_type was general and agent_id provided
            if t_str == TaskType.GENERAL.value and agent_id:
                norm_agent = agent_id.lower().strip()
                t_str = AGENT_TO_TASK_MAP.get(norm_agent, t_str)

            # Collect required capabilities
            req_caps = set(required_capabilities or [])
            if requires_tools:
                req_caps.add("tools")
            if requires_vision:
                req_caps.add("vision")
            if requires_reasoning:
                req_caps.add("reasoning")

            # 2. Query discovered models
            all_models = (
                self.discovery.get_all_models()
                if hasattr(self.discovery, "get_all_models")
                else self.discovery.discover_all()
            )

            is_privacy_strict = local_only or (self.policy == ModelPolicy.PRIVACY_FIRST)

            if not all_models:
                msg = "No models currently discovered across configured providers."
                if raise_on_none:
                    raise NoCompatibleModelAvailable(msg, task_type=t_str, policy=self.policy.value)
                return NoCompatibleModelAvailableResult(message=msg, task_type=t_str, policy=self.policy.value)

            # 3. Filter eligible models with strict capability verification
            candidates = self._filter_candidates(
                all_models=all_models,
                task_type=t_str,
                required_capabilities=req_caps,
                local_only=is_privacy_strict,
                min_context_tokens=min_context_tokens,
                max_cost_per_m=max_cost_per_m,
                confidence_policy=confidence_policy,
            )

            # 4. If preferred model is specified, verify it is available, healthy, and meets constraints
            if preferred_model:
                for c in candidates:
                    if (
                        c.model_id.lower() == preferred_model.lower()
                        or c.display_name.lower() == preferred_model.lower()
                    ):
                        return SelectionResult(
                            model_id=c.model_id,
                            provider=c.provider,
                            task_type=t_str,
                            reason=f"User-preferred model '{preferred_model}' is verified available and meets task requirements",
                            policy=self.policy.value,
                            is_local=c.is_local,
                            context_length=c.context_length,
                            score=100.0,
                            alternatives=[m.model_id for m in candidates if m.model_id != c.model_id][:3],
                            hardware_notes=self._format_hardware_notes(c),
                        )

            # 5. Handle empty candidate list (fail-closed behavior)
            if not candidates:
                if is_privacy_strict:
                    # Privacy-first policy MUST fail closed. NEVER fall through to a cloud model.
                    msg = (
                        "Privacy-first policy enforced (fail-closed): no healthy local model satisfies "
                        f"the required constraints for task '{t_str}' (required capabilities: {sorted(list(req_caps))})."
                    )
                    logger.warning(msg)
                    if raise_on_none:
                        raise NoCompatibleModelAvailable(msg, task_type=t_str, policy=self.policy.value)
                    return NoCompatibleModelAvailableResult(message=msg, task_type=t_str, policy=self.policy.value)

                msg = (
                    f"No compatible model available satisfying required capabilities {sorted(list(req_caps))} "
                    f"and constraints for task '{t_str}'."
                )
                logger.warning(msg)
                if raise_on_none:
                    raise NoCompatibleModelAvailable(msg, task_type=t_str, policy=self.policy.value)
                return NoCompatibleModelAvailableResult(message=msg, task_type=t_str, policy=self.policy.value)

            # 6. Score candidate models
            scored: List[Tuple[float, ModelCapabilityInfo, str]] = []
            for c in candidates:
                score, reason = self._score_model(c, t_str, req_caps)
                scored.append((score, c, reason))

            scored.sort(key=lambda x: x[0], reverse=True)
            best_score, best_model, best_reason = scored[0]
            alternatives = [m.model_id for _, m, _ in scored[1:4]]

            # Update loaded local model tracking
            if best_model.is_local:
                self._currently_loaded_local_model = best_model.model_id

            logger.info(
                "ModelSelectionEngine: Allocated '%s' (%s) for task '%s' (score=%.1f): %s",
                best_model.model_id,
                best_model.provider,
                t_str,
                best_score,
                best_reason,
            )

            candidate_map = {m.model_id: round(s, 1) for s, m, _ in scored}
            return SelectionResult(
                model_id=best_model.model_id,
                provider=best_model.provider,
                task_type=t_str,
                reason=best_reason,
                policy=self.policy.value,
                is_local=best_model.is_local,
                context_length=best_model.context_length,
                score=round(best_score, 1),
                alternatives=alternatives,
                candidate_scores=candidate_map,
                hardware_notes=self._format_hardware_notes(best_model),
            )

    def _filter_candidates(
        self,
        all_models: List[ModelCapabilityInfo],
        task_type: str,
        required_capabilities: Set[str],
        local_only: bool,
        min_context_tokens: int,
        max_cost_per_m: Optional[float] = None,
        confidence_policy: CapabilityConfidencePolicy | str = CapabilityConfidencePolicy.ALLOW_INFERRED,
    ) -> List[ModelCapabilityInfo]:
        filtered: List[ModelCapabilityInfo] = []

        for m in all_models:
            # 1. Health check: model must be healthy and reachable
            if m.health_status != "healthy":
                continue

            # 2. Strict privacy / local boundary check
            if local_only and not m.is_local:
                continue

            # 3. Context length boundary
            if m.context_length < min_context_tokens:
                continue

            # 4. Cost constraint
            if max_cost_per_m is not None and not m.is_free:
                if m.price_per_m_out > max_cost_per_m:
                    continue

            # 5. Strict capability enforcement: every required capability must be supported
            # Unknown capability provenance is NEVER treated as confirmed support
            missing_cap = False
            for req in required_capabilities:
                if not m.has_capability(req, confidence_policy=confidence_policy):
                    missing_cap = True
                    break
            if missing_cap:
                continue

            # 6. Task specific mandatory capabilities
            if task_type == "vision" and not m.has_capability("vision", confidence_policy=confidence_policy):
                continue
            if task_type == "coding" and not (
                m.has_capability("coding", confidence_policy=confidence_policy)
                or m.has_capability("tools", confidence_policy=confidence_policy)
            ):
                continue

            # 7. Hardware boundary: reject local models exceeding GPU VRAM budget
            if m.is_local and self._vram_limit_mb > 0 and m.vram_mb_required > self._vram_limit_mb:
                continue

            # 8. Hardware concurrency gate: overflow to cloud when local slots are full (unless privacy-strict)
            if m.is_local and not local_only and self._active_local_inferences >= self._max_concurrent_local:
                continue

            filtered.append(m)

        return filtered

    def _score_model(
        self,
        model: ModelCapabilityInfo,
        task_type: str,
        required_capabilities: Set[str],
    ) -> Tuple[float, str]:
        score = 50.0  # Base score
        reasons = []

        # Policy weighting
        if self.policy == ModelPolicy.LOCAL_FIRST:
            if model.is_local:
                score += 35.0
                reasons.append("Local-first policy bonus (+35)")
        elif self.policy == ModelPolicy.CLOUD_FIRST:
            if not model.is_local:
                score += 30.0
                reasons.append("Cloud-first policy bonus (+30)")
        elif self.policy == ModelPolicy.PRIVACY_FIRST:
            if model.is_local:
                score += 50.0
                reasons.append("Privacy-first strict local bonus (+50)")
        elif self.policy == ModelPolicy.LOWEST_COST:
            if model.is_free:
                score += 40.0
                reasons.append("Zero-cost local/free bonus (+40)")

        # Shared model pool bonus (avoids local model swapping)
        if model.is_local and self._currently_loaded_local_model == model.model_id:
            score += 15.0
            reasons.append("Currently loaded in local memory (+15)")

        # Capability matching
        if task_type == "coding":
            if model.supports_coding:
                score += 30.0
                reasons.append("Specialized coding capability (+30)")
            if model.supports_tools:
                score += 15.0
                reasons.append("Tool-calling verified (+15)")

        elif task_type in ("planning", "reasoning", "security"):
            if model.supports_reasoning:
                score += 25.0
                reasons.append("High reasoning depth (+25)")
            if model.supports_structured_output:
                score += 15.0
                reasons.append("Structured JSON output (+15)")

        elif task_type == "research":
            if model.context_length >= 32768:
                score += 20.0
                reasons.append("Long-context synthesis (+20)")
            if model.supports_reasoning:
                score += 15.0

        elif task_type == "verification":
            if model.supports_structured_output:
                score += 20.0
            if model.supports_reasoning:
                score += 20.0

        # Latency penalty
        if model.latency_ms > 500:
            penalty = min(20.0, (model.latency_ms - 500) / 100)
            score -= penalty

        # Recent failure penalty
        if any(f.get("model_id") == model.model_id for f in self._failure_history[-10:]):
            score -= 40.0
            reasons.append("Recent failure penalty (-40)")

        reason_str = "; ".join(reasons) if reasons else "Eligible model meeting baseline criteria"
        return score, reason_str

    def record_model_failure(self, model_id: str, error: str = "", task_type: str = "general") -> None:
        """Record model failure and apply runtime scoring penalty."""
        with self._lock:
            self._failure_history.append({
                "model_id": model_id,
                "error": error,
                "task_type": task_type,
                "timestamp": time.time(),
            })

    def handle_model_failure(
        self,
        failed_model_id: str,
        error: str,
        task_type: str,
        context: Optional[Dict[str, Any]] = None,
        raise_on_none: bool = False,
    ) -> SelectionResult:
        """Handle runtime failure of a model by logging the failure, degrading its health,
        and dynamically allocating an alternative candidate. Zero hardcoded fallbacks."""
        with self._lock:
            logger.warning("Model '%s' failed during task '%s': %s", failed_model_id, task_type, error)
            self._failure_history.append({
                "model_id": failed_model_id,
                "error": error,
                "task_type": task_type,
                "timestamp": time.time(),
            })

            # Temporarily degrade failed model
            failed_info = self.discovery.get_model_info(failed_model_id)
            if failed_info:
                failed_info.health_status = "degraded"
                failed_info.error_message = error

            # Re-select alternative excluding the failed model
            all_models = (
                self.discovery.get_all_models()
                if hasattr(self.discovery, "get_all_models")
                else self.discovery.discover_all()
            )
            is_privacy_strict = (self.policy == ModelPolicy.PRIVACY_FIRST)
            eligible = [
                m for m in all_models
                if m.model_id != failed_model_id
                and m.health_status == "healthy"
                and (not is_privacy_strict or m.is_local)
            ]

            if eligible:
                scored = []
                for c in eligible:
                    score, reason = self._score_model(c, task_type, set())
                    scored.append((score, c, reason))
                scored.sort(key=lambda x: x[0], reverse=True)
                best_score, best_model, _ = scored[0]

                return SelectionResult(
                    model_id=best_model.model_id,
                    provider=best_model.provider,
                    task_type=task_type,
                    reason=f"Dynamic runtime fallback after '{failed_model_id}' failed with error: {error}",
                    policy=self.policy.value,
                    is_local=best_model.is_local,
                    context_length=best_model.context_length,
                    score=best_score,
                    alternatives=[m.model_id for _, m, _ in scored[1:3]],
                    hardware_notes=f"Safely rerouted following failure of {failed_model_id}",
                )

            # No alternative healthy models available: return explicit failure, never invent model ID
            msg = f"No compatible alternative healthy model available after failure of '{failed_model_id}'."
            if raise_on_none:
                raise NoCompatibleModelAvailable(msg, task_type=task_type, policy=self.policy.value)
            return NoCompatibleModelAvailableResult(message=msg, task_type=task_type, policy=self.policy.value)

    def _format_hardware_notes(self, model: ModelCapabilityInfo) -> str:
        if model.is_local:
            gpu_name = self._hardware.gpu.name if self._hardware.gpu else "Integrated/CPU"
            vram_gb = self._hardware.gpu.vram_gb if self._hardware.gpu else 0.0
            return (
                f"Local inference on {gpu_name} ({vram_gb} GB VRAM). "
                f"Estimated model memory: {model.vram_mb_required} MB. "
                f"Hardware budget: {self._vram_limit_mb} MB."
            )
        return "Cloud API inference. Zero local GPU VRAM utilized."


def get_model_selection_engine(
    discovery_engine: Optional[ModelDiscoveryEngine] = None,
    config: Optional[JarvisConfig] = None,
) -> ModelSelectionEngine:
    return ModelSelectionEngine.get_instance(discovery_engine=discovery_engine, config=config)

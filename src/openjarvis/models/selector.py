"""Intelligent Model Selection Engine — task-based capability scoring, hardware-aware allocation,
shared model pooling, and dynamic fallback."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.core.config import JarvisConfig, detect_hardware, load_config
from openjarvis.models.capabilities import (
    ModelCapabilityInfo,
    ModelPolicy,
    SelectionResult,
    TaskType,
)
from openjarvis.models.discovery import ModelDiscoveryEngine, get_model_discovery_engine

logger = logging.getLogger(__name__)


class ModelSelectionEngine:
    """Selects the optimal model dynamically per task or execution step, respecting
    hardware boundaries (NVIDIA GTX 1650 4 GB VRAM) and configured privacy policies."""

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
        self._max_concurrent_local: int = 2  # GTX 1650 constraint
        self._currently_loaded_local_model: Optional[str] = None
        self._failure_history: List[Dict[str, Any]] = []
        self._hardware = detect_hardware()

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
        task_type: str | TaskType,
        required_capabilities: Optional[List[str]] = None,
        local_only: bool = False,
        preferred_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        min_context_tokens: int = 4096,
        agent_id: Optional[str] = None,
    ) -> SelectionResult:
        """Dynamically select the optimal available model based on capability requirements,
        system hardware, and operational policy."""
        with self._lock:
            # 1. Normalize task type
            t_str = task_type.value if isinstance(task_type, TaskType) else str(task_type).lower()
            req_caps = set(required_capabilities or [])

            # 2. Query available models
            all_models = (
                self.discovery.get_all_models()
                if hasattr(self.discovery, "get_all_models")
                else self.discovery.discover_all()
            )
            if not all_models:
                # Emergency fallback if discovery returned empty
                return SelectionResult(
                    model_id="qwen3:4b-instruct",
                    provider="local-emergency",
                    task_type=t_str,
                    reason="No models currently discovered; using default local candidate",
                    policy=self.policy.value,
                    is_local=True,
                    context_length=8192,
                    score=0.0,
                    hardware_notes="Discovery returned empty list",
                )

            # 3. Filter eligible models
            candidates = self._filter_candidates(
                all_models=all_models,
                task_type=t_str,
                required_capabilities=req_caps,
                local_only=local_only or (self.policy == ModelPolicy.PRIVACY_FIRST),
                min_context_tokens=min_context_tokens,
            )

            # 4. If preferred model is specified, check if it is available and capable
            if preferred_model:
                for c in candidates:
                    if c.model_id.lower() == preferred_model.lower() or c.display_name.lower() == preferred_model.lower():
                        return SelectionResult(
                            model_id=c.model_id,
                            provider=c.provider,
                            task_type=t_str,
                            reason=f"User-preferred model '{preferred_model}' is available and meets task requirements",
                            policy=self.policy.value,
                            is_local=c.is_local,
                            context_length=c.context_length,
                            score=100.0,
                            alternatives=[m.model_id for m in candidates if m.model_id != c.model_id][:3],
                            hardware_notes=self._format_hardware_notes(c),
                        )

            # 5. If no candidates match strict constraints, fall back gracefully
            if not candidates:
                if local_only or self.policy == ModelPolicy.PRIVACY_FIRST:
                    # Look for any healthy local model
                    local_candidates = [m for m in all_models if m.is_local and m.health_status == "healthy"]
                    if local_candidates:
                        best_local = local_candidates[0]
                        return SelectionResult(
                            model_id=best_local.model_id,
                            provider=best_local.provider,
                            task_type=t_str,
                            reason=f"Selected best available local model under strict privacy policy (capabilities partial)",
                            policy=self.policy.value,
                            is_local=True,
                            context_length=best_local.context_length,
                            score=40.0,
                            hardware_notes=self._format_hardware_notes(best_local),
                        )

                # Fall back to best general healthy model
                healthy = [m for m in all_models if m.health_status == "healthy"]
                if healthy:
                    candidates = healthy

            # 6. Score candidates
            scored = []
            for c in candidates:
                score, reason = self._score_model(c, t_str, req_caps)
                scored.append((score, c, reason))

            scored.sort(key=lambda x: x[0], reverse=True)
            if not scored:
                fallback = all_models[0]
                return SelectionResult(
                    model_id=fallback.model_id,
                    provider=fallback.provider,
                    task_type=t_str,
                    reason="Fallback to best available model",
                    policy=self.policy.value,
                    is_local=fallback.is_local,
                    context_length=fallback.context_length,
                    score=20.0,
                    hardware_notes="No candidate met strict criteria",
                )

            best_score, best_model, best_reason = scored[0]
            alternatives = [m.model_id for _, m, _ in scored[1:4]]

            # Update loaded model tracking
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
    ) -> List[ModelCapabilityInfo]:
        filtered: List[ModelCapabilityInfo] = []

        for m in all_models:
            if m.health_status != "healthy":
                continue

            if local_only and not m.is_local:
                continue

            if m.context_length < min_context_tokens:
                continue

            # Task specific mandatory filters
            if task_type == "vision" and not m.supports_vision:
                continue

            # Hardware boundary: reject local models exceeding 4 GB VRAM budget on GTX 1650
            if m.is_local and m.vram_mb_required > 3800:
                continue

            # Hardware concurrency gate: overflow to cloud when local slots are full (unless local_only requested)
            if m.is_local and not local_only and self._active_inferences >= self.max_concurrent_inferences:
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
    ) -> SelectionResult:
        """Handle runtime failure of a model by logging the failure, degrading its health,
        and dynamically allocating an alternative candidate."""
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
            all_models = self.discovery.discover_all()
            eligible = [m for m in all_models if m.model_id != failed_model_id and m.health_status == "healthy"]

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

            # Fallback to local default if nothing else eligible
            return SelectionResult(
                model_id="qwen3:4b-instruct",
                provider="vllm",
                task_type=task_type,
                reason=f"Emergency fallback to local default after {failed_model_id} failure",
                policy=self.policy.value,
                is_local=True,
                context_length=8192,
                score=10.0,
                hardware_notes="No alternative healthy models available",
            )

    def _format_hardware_notes(self, model: ModelCapabilityInfo) -> str:
        if model.is_local:
            gpu_name = self._hardware.gpu.name if self._hardware.gpu else "Integrated/CPU"
            vram_gb = self._hardware.gpu.vram_gb if self._hardware.gpu else 0.0
            return f"Local inference on {gpu_name} ({vram_gb} GB VRAM). Estimated model memory: {model.vram_mb_required} MB."
        return "Cloud API inference. Zero local GPU VRAM utilized."


def get_model_selection_engine(
    discovery_engine: Optional[ModelDiscoveryEngine] = None,
    config: Optional[JarvisConfig] = None,
) -> ModelSelectionEngine:
    return ModelSelectionEngine.get_instance(discovery_engine=discovery_engine, config=config)

"""Phase 9.1: Comprehensive P0 Paid-Model Fallback Security Test Suite.

Validates all 10 required test scenarios to guarantee that Jarvis NEVER
automatically switches to a paid model under any fallback, retry, policy,
or failure scenario unless explicitly authorized:

1. CLOUD_FIRST with a free model failure and a paid alternative.
2. CLOUD_FIRST with only paid fallback models.
3. Balanced policy with paid models disabled.
4. Explicit paid authorization.
5. Local-only fallback.
6. Agent-level fallback.
7. Background task retry.
8. Provider outage and rate-limit fallback.
9. Multiple consecutive model failures.
10. Model switching after a task has already started.

All tests assert the actual selected provider and model ID, not just return status.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from openjarvis.core.config import JarvisConfig
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
from openjarvis.models.discovery import ModelDiscoveryEngine
from openjarvis.models.selector import ModelSelectionEngine


def _make_model(
    model_id: str,
    provider: str,
    is_free: bool = True,
    is_local: bool = False,
    capabilities: Optional[List[str]] = None,
    health: str = "healthy",
    context_length: int = 8192,
) -> ModelCapabilityInfo:
    caps = capabilities or ["general"]
    has_tools = "tools" in caps
    has_coding = "coding" in caps
    has_vision = "vision" in caps
    has_reasoning = "reasoning" in caps
    has_structured = "structured_output" in caps
    prov = {c: CapabilityProvenance.CONFIRMED.value for c in caps}

    return ModelCapabilityInfo(
        model_id=model_id,
        display_name=model_id,
        provider=provider,
        is_free=is_free,
        is_local=is_local,
        health_status=health,
        context_length=context_length,
        supports_tools=has_tools,
        supports_coding=has_coding,
        supports_vision=has_vision,
        supports_reasoning=has_reasoning,
        supports_structured_output=has_structured,
        confirmed_capabilities=caps,
        provenance=prov,
        vram_mb_required=1500 if is_local else 0,
        price_per_m_in=0.0 if is_free else 5.0,
        price_per_m_out=0.0 if is_free else 15.0,
    )


class MockDiscoveryEngine:
    def __init__(self, models: List[ModelCapabilityInfo]) -> None:
        self.models = {m.model_id: m for m in models}

    def get_all_models(self) -> List[ModelCapabilityInfo]:
        return list(self.models.values())

    def discover_all(self) -> List[ModelCapabilityInfo]:
        return list(self.models.values())

    def get_model_info(self, model_id: str) -> Optional[ModelCapabilityInfo]:
        return self.models.get(model_id)


# ---------------------------------------------------------------------------
# Scenario 1: CLOUD_FIRST with free model failure and paid alternative
# ---------------------------------------------------------------------------
def test_scenario_1_cloud_first_free_model_failure_with_paid_alternative():
    """Under CLOUD_FIRST, when a free cloud model fails, fallback selects another free model, NEVER a paid model."""
    m_free_1 = _make_model("free-cloud-primary", provider="openrouter", is_free=True, is_local=False)
    m_free_2 = _make_model("free-cloud-secondary", provider="openrouter", is_free=True, is_local=False)
    m_paid = _make_model("paid-cloud-fallback", provider="openai", is_free=False, is_local=False)

    discovery = MockDiscoveryEngine([m_free_1, m_free_2, m_paid])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)
    selector.allow_paid_models = False

    res = selector.handle_model_failure(
        failed_model_id="free-cloud-primary",
        error="500 Internal Server Error",
        task_type="general",
        allow_paid=False,
    )

    assert res.is_compatible is True
    # Must select the free alternative
    assert res.model_id == "free-cloud-secondary"
    assert res.provider == "openrouter"
    # Must NOT select the paid provider
    assert res.model_id != "paid-cloud-fallback"
    assert res.provider != "openai"


# ---------------------------------------------------------------------------
# Scenario 2: CLOUD_FIRST with only paid fallback models
# ---------------------------------------------------------------------------
def test_scenario_2_cloud_first_with_only_paid_fallback_models():
    """Under CLOUD_FIRST, when only paid models remain as fallbacks, Jarvis fails closed rather than leaking cost."""
    m_free_1 = _make_model("free-cloud-primary", provider="openrouter", is_free=True, is_local=False)
    m_paid_1 = _make_model("paid-gpt4", provider="openai", is_free=False, is_local=False)
    m_paid_2 = _make_model("paid-claude", provider="anthropic", is_free=False, is_local=False)

    discovery = MockDiscoveryEngine([m_free_1, m_paid_1, m_paid_2])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)
    selector.allow_paid_models = False

    res = selector.handle_model_failure(
        failed_model_id="free-cloud-primary",
        error="Connection reset by peer",
        task_type="general",
        allow_paid=False,
    )

    # Must fail closed
    assert res.is_compatible is False
    assert isinstance(res, NoCompatibleModelAvailableResult)
    # Paid models must NEVER be authorized
    assert getattr(res, "provider", None) not in ("openai", "anthropic")
    assert getattr(res, "model_id", None) not in ("paid-gpt4", "paid-claude")


# ---------------------------------------------------------------------------
# Scenario 3: Balanced policy with paid models disabled
# ---------------------------------------------------------------------------
def test_scenario_3_balanced_policy_with_paid_models_disabled():
    """Under balanced policy, when local model fails, fallback selects free cloud, never paid cloud."""
    m_local = _make_model("qwen3:4b", provider="ollama", is_free=True, is_local=True)
    m_cloud_free = _make_model("llama-3-free", provider="openrouter", is_free=True, is_local=False)
    m_cloud_paid = _make_model("gpt-4o", provider="openai", is_free=False, is_local=False)

    discovery = MockDiscoveryEngine([m_local, m_cloud_free, m_cloud_paid])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.LOCAL_FIRST)
    selector.allow_paid_models = False

    res = selector.handle_model_failure(
        failed_model_id="qwen3:4b",
        error="CUDA out of memory",
        task_type="general",
    )

    assert res.is_compatible is True
    assert res.model_id == "llama-3-free"
    assert res.provider == "openrouter"
    assert res.provider != "openai"


# ---------------------------------------------------------------------------
# Scenario 4: Explicit paid authorization
# ---------------------------------------------------------------------------
def test_scenario_4_explicit_paid_authorization():
    """When allow_paid=True is explicitly supplied, a paid model may be selected if no free models exist."""
    m_free_1 = _make_model("failing-model", provider="openrouter", is_free=True, is_local=False)
    m_paid_1 = _make_model("paid-gpt4", provider="openai", is_free=False, is_local=False)

    discovery = MockDiscoveryEngine([m_free_1, m_paid_1])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)

    res = selector.handle_model_failure(
        failed_model_id="failing-model",
        error="Rate limit exhausted",
        task_type="general",
        allow_paid=True,
    )

    assert res.is_compatible is True
    assert res.model_id == "paid-gpt4"
    assert res.provider == "openai"


# ---------------------------------------------------------------------------
# Scenario 5: Local-only fallback
# ---------------------------------------------------------------------------
def test_scenario_5_local_only_fallback():
    """Under PRIVACY_FIRST or local_only, fallback is strictly confined to local models."""
    m_loc_1 = _make_model("ollama-qwen-1", provider="ollama", is_free=True, is_local=True)
    m_loc_2 = _make_model("ollama-qwen-2", provider="ollama", is_free=True, is_local=True)
    m_rem_free = _make_model("openrouter-free", provider="openrouter", is_free=True, is_local=False)
    m_rem_paid = _make_model("openai-paid", provider="openai", is_free=False, is_local=False)

    discovery = MockDiscoveryEngine([m_loc_1, m_loc_2, m_rem_free, m_rem_paid])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.PRIVACY_FIRST)

    res = selector.handle_model_failure(
        failed_model_id="ollama-qwen-1",
        error="Process crashed",
        task_type="general",
        context={"local_only": True},
    )

    assert res.is_compatible is True
    assert res.model_id == "ollama-qwen-2"
    assert res.provider == "ollama"
    assert res.is_local is True

    # If local-2 also fails, system MUST fail closed without network leakage
    res2 = selector.handle_model_failure(
        failed_model_id="ollama-qwen-2",
        error="Process crashed",
        task_type="general",
        context={"local_only": True},
    )
    assert res2.is_compatible is False


# ---------------------------------------------------------------------------
# Scenario 6: Agent-level fallback
# ---------------------------------------------------------------------------
def test_scenario_6_agent_level_fallback():
    """Agent-level fallback preserves specialist capability constraints without paid bypass."""
    m_code_1 = _make_model("deepseek-coder-1", provider="ollama", is_free=True, capabilities=["coding", "tools"])
    m_code_2 = _make_model("qwen-coder-free", provider="openrouter", is_free=True, capabilities=["coding", "tools"])
    m_code_paid = _make_model("claude-code-paid", provider="anthropic", is_free=False, capabilities=["coding", "tools"])
    m_general = _make_model("general-free", provider="openrouter", is_free=True, capabilities=["general"])

    discovery = MockDiscoveryEngine([m_code_1, m_code_2, m_code_paid, m_general])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)
    selector.allow_paid_models = False

    res = selector.handle_model_failure(
        failed_model_id="deepseek-coder-1",
        error="Syntax generation error",
        task_type="coding",
        context={"agent_id": "forge", "required_capabilities": ["coding"]},
    )

    assert res.is_compatible is True
    assert res.model_id == "qwen-coder-free"
    assert res.provider == "openrouter"
    # Neither paid coding model nor general model was selected
    assert res.model_id != "claude-code-paid"
    assert res.model_id != "general-free"


# ---------------------------------------------------------------------------
# Scenario 7: Background task retry
# ---------------------------------------------------------------------------
def test_scenario_7_background_task_retry():
    """Background task retry preserves zero-cost restriction across retry attempts."""
    m_free_primary = _make_model("task-model-1", provider="openrouter", is_free=True)
    m_free_retry = _make_model("task-model-2", provider="openrouter", is_free=True)
    m_paid_retry = _make_model("task-model-paid", provider="openai", is_free=False)

    discovery = MockDiscoveryEngine([m_free_primary, m_free_retry, m_paid_retry])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)
    selector.allow_paid_models = False

    res = selector.handle_model_failure(
        failed_model_id="task-model-1",
        error="Step execution timeout",
        task_type="general",
        context={"task_id": "task_bg_42", "allow_paid": False},
    )

    assert res.is_compatible is True
    assert res.model_id == "task-model-2"
    assert res.provider == "openrouter"
    assert res.provider != "openai"


# ---------------------------------------------------------------------------
# Scenario 8: Provider outage and rate-limit fallback
# ---------------------------------------------------------------------------
def test_scenario_8_provider_outage_and_rate_limit_fallback():
    """Provider rate limit (429) triggers penalty and reroutes to another free provider, not paid."""
    m_or_1 = _make_model("openrouter-1", provider="openrouter", is_free=True)
    m_nim_1 = _make_model("nim-free-1", provider="nim", is_free=True)
    m_openai_paid = _make_model("gpt-4o", provider="openai", is_free=False)

    discovery = MockDiscoveryEngine([m_or_1, m_nim_1, m_openai_paid])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)
    selector.allow_paid_models = False

    res = selector.handle_model_failure(
        failed_model_id="openrouter-1",
        error="429 Too Many Requests (Rate limit)",
        task_type="general",
    )

    assert res.is_compatible is True
    assert res.model_id == "nim-free-1"
    assert res.provider == "nim"
    assert res.provider != "openai"


# ---------------------------------------------------------------------------
# Scenario 9: Multiple consecutive model failures
# ---------------------------------------------------------------------------
def test_scenario_9_multiple_consecutive_model_failures():
    """Successive failures through free candidates safely fails closed when only paid models remain."""
    m_a = _make_model("free-candidate-a", provider="ollama", is_free=True, is_local=True)
    m_b = _make_model("free-candidate-b", provider="openrouter", is_free=True, is_local=False)
    m_c = _make_model("paid-candidate-c", provider="openai", is_free=False, is_local=False)

    discovery = MockDiscoveryEngine([m_a, m_b, m_c])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.LOCAL_FIRST)
    selector.allow_paid_models = False

    # Failure 1: A fails -> reroutes to B (free)
    res1 = selector.handle_model_failure("free-candidate-a", error="Server error", task_type="general")
    assert res1.is_compatible is True
    assert res1.model_id == "free-candidate-b"
    assert res1.provider == "openrouter"

    # Failure 2: B also fails -> only C (paid) remains -> MUST fail closed!
    res2 = selector.handle_model_failure("free-candidate-b", error="Server error", task_type="general")
    assert res2.is_compatible is False
    assert isinstance(res2, NoCompatibleModelAvailableResult)
    assert getattr(res2, "provider", None) != "openai"
    assert getattr(res2, "model_id", None) != "paid-candidate-c"


# ---------------------------------------------------------------------------
# Scenario 10: Model switching after task has already started
# ---------------------------------------------------------------------------
def test_scenario_10_model_switching_after_task_already_started():
    """Switching models mid-task preserves task context and zero-cost policy."""
    m_initial = _make_model("model-stage-1", provider="openrouter", is_free=True)
    m_next = _make_model("model-stage-2", provider="openrouter", is_free=True)
    m_paid = _make_model("model-stage-paid", provider="openai", is_free=False)

    discovery = MockDiscoveryEngine([m_initial, m_next, m_paid])
    selector = ModelSelectionEngine(discovery_engine=discovery, default_policy=ModelPolicy.CLOUD_FIRST)
    selector.allow_paid_models = False

    # Initial selection
    initial_sel = selector.select_model(task_type="general", preferred_model="model-stage-1")
    assert initial_sel.model_id == "model-stage-1"
    assert initial_sel.provider == "openrouter"

    # Mid-task switch due to degradation or step failure
    switch_res = selector.handle_model_failure(
        failed_model_id="model-stage-1",
        error="Context window threshold approached",
        task_type="general",
        context={"task_id": "active_workflow_99", "completed_steps": 3},
    )

    assert switch_res.is_compatible is True
    assert switch_res.model_id == "model-stage-2"
    assert switch_res.provider == "openrouter"
    assert switch_res.provider != "openai"

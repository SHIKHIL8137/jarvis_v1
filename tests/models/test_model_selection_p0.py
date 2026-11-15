"""Regression tests for P0 Model Selection Correctness requirements."""

import pytest
from unittest.mock import MagicMock

from openjarvis.models.capabilities import (
    CapabilityProvenance,
    ModelCapabilityInfo,
    ModelPolicy,
    NoCompatibleModelAvailable,
    NoCompatibleModelAvailableResult,
    SelectionResult,
    TaskType,
)
from openjarvis.models.selector import (
    AGENT_TO_TASK_MAP,
    ModelSelectionEngine,
)


def _make_mock_discovery(models):
    disc = MagicMock()
    disc.get_all_models.return_value = list(models)
    disc.discover_all.return_value = list(models)
    disc.get_model_info.side_effect = lambda mid: next((m for m in models if m.model_id == mid), None)
    return disc


def test_req1_and_req2_no_hardcoded_emergency_fallback_and_returns_explicit_result():
    """Req 1 & 2: When no models exist, return explicit NoCompatibleModelAvailableResult without inventing qwen3:4b-instruct."""
    disc = _make_mock_discovery([])
    selector = ModelSelectionEngine(discovery_engine=disc, default_policy=ModelPolicy.BALANCED)

    result = selector.select_model(task_type="coding")

    assert isinstance(result, NoCompatibleModelAvailableResult)
    assert result.model_id == ""
    assert result.is_compatible is False
    assert result.error == "NoCompatibleModelAvailable"
    assert "qwen3:4b-instruct" not in result.model_id


def test_req2_raises_exception_when_requested():
    """Req 2: When raise_on_none=True, raise NoCompatibleModelAvailable exception."""
    disc = _make_mock_discovery([])
    selector = ModelSelectionEngine(discovery_engine=disc, default_policy=ModelPolicy.BALANCED)

    with pytest.raises(NoCompatibleModelAvailable):
        selector.select_model(task_type="coding", raise_on_none=True)


def test_req3_never_invents_model_id_or_reports_undiscovered_model():
    """Req 3: Only discovered models can ever be returned."""
    real_model = ModelCapabilityInfo(
        model_id="local-deepseek-7b",
        provider="ollama",
        is_local=True,
        health_status="healthy",
        supports_coding=True,
    )
    disc = _make_mock_discovery([real_model])
    selector = ModelSelectionEngine(discovery_engine=disc)

    res = selector.select_model(task_type="coding")
    assert res.model_id == "local-deepseek-7b"
    assert res.is_compatible is True


def test_req4_candidate_filtering_enforces_every_required_capability():
    """Req 4: Candidate filtering requires every single required capability."""
    model_tool_only = ModelCapabilityInfo(
        model_id="tool-only-model",
        provider="ollama",
        is_local=True,
        health_status="healthy",
        supports_tools=True,
        supports_vision=False,
    )
    disc = _make_mock_discovery([model_tool_only])
    selector = ModelSelectionEngine(discovery_engine=disc)

    # Requires both tools AND vision
    res = selector.select_model(task_type="general", required_capabilities=["tools", "vision"])
    assert isinstance(res, NoCompatibleModelAvailableResult)
    assert res.model_id == ""


def test_req5_and_req6_provenance_and_unknown_capability_never_confirmed():
    """Req 5 & 6: Distinguish confirmed, provider-declared, inferred, and unknown.
    Unknown capability provenance must never be treated as confirmed support."""
    model_unknown = ModelCapabilityInfo(
        model_id="experimental-model",
        provider="test",
        is_local=True,
        health_status="healthy",
        supports_coding=True,
        provenance={"coding": CapabilityProvenance.UNKNOWN.value},
    )
    assert not model_unknown.has_capability("coding")

    disc = _make_mock_discovery([model_unknown])
    selector = ModelSelectionEngine(discovery_engine=disc)
    res = selector.select_model(task_type="general", required_capabilities=["coding"])
    assert isinstance(res, NoCompatibleModelAvailableResult)


def test_req7_privacy_first_fails_closed_never_cloud_fallthrough():
    """Req 7: Privacy-first policy MUST fail closed and never fall through to a cloud model."""
    cloud_model = ModelCapabilityInfo(
        model_id="gpt-4o",
        provider="openai",
        is_local=False,
        health_status="healthy",
        supports_coding=True,
        supports_tools=True,
    )
    disc = _make_mock_discovery([cloud_model])
    selector = ModelSelectionEngine(discovery_engine=disc, default_policy=ModelPolicy.PRIVACY_FIRST)

    res = selector.select_model(task_type="coding")
    assert isinstance(res, NoCompatibleModelAvailableResult)
    assert res.model_id == ""
    assert "privacy-first" in res.reason.lower()


def test_req8_signature_mismatch_arguments_handled_cleanly():
    """Req 8: selector accepts requires_tools, requires_vision, requires_reasoning, max_cost_per_m without TypeError."""
    model = ModelCapabilityInfo(
        model_id="smart-local-coder",
        provider="ollama",
        is_local=True,
        health_status="healthy",
        supports_coding=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_vision=True,
    )
    disc = _make_mock_discovery([model])
    selector = ModelSelectionEngine(discovery_engine=disc)

    res = selector.select_model(
        task_type="coding",
        agent_id="forge",
        requires_tools=True,
        requires_vision=True,
        requires_reasoning=True,
        max_cost_per_m=0.0,
    )
    assert res.model_id == "smart-local-coder"


def test_req9_task_type_normalization_from_agent_id():
    """Req 9: Agent IDs passed as task categories are mapped cleanly to standard task types."""
    assert AGENT_TO_TASK_MAP["forge"] == TaskType.CODING.value
    assert AGENT_TO_TASK_MAP["oracle"] == TaskType.RESEARCH.value
    assert AGENT_TO_TASK_MAP["sentinel"] == TaskType.SECURITY.value
    assert AGENT_TO_TASK_MAP["vanguard"] == TaskType.VERIFICATION.value

    model = ModelCapabilityInfo(
        model_id="specialist-model",
        provider="local",
        is_local=True,
        health_status="healthy",
        supports_coding=True,
    )
    disc = _make_mock_discovery([model])
    selector = ModelSelectionEngine(discovery_engine=disc)

    res = selector.select_model(task_type="forge")
    assert res.task_type == TaskType.CODING.value


def test_req10_handle_model_failure_never_returns_hardcoded_model():
    """Req 10: Runtime model failure rerouting returns NoCompatibleModelAvailableResult if no alternative exists."""
    failed_model = ModelCapabilityInfo(
        model_id="flaky-model",
        provider="vllm",
        is_local=True,
        health_status="healthy",
    )
    disc = _make_mock_discovery([failed_model])
    selector = ModelSelectionEngine(discovery_engine=disc)

    fallback = selector.handle_model_failure(failed_model_id="flaky-model", error="CUDA OOM", task_type="coding")
    assert isinstance(fallback, NoCompatibleModelAvailableResult)
    assert fallback.model_id == ""
    assert "qwen3:4b-instruct" not in fallback.model_id

"""Tests for model capability verification, confidence policies, and cloud discovery paths.

Verifies:
- Separation of confirmed, provider-declared, inferred, and unknown capabilities.
- DeepSeek R1 and known non-tool models are NOT marked as tool-capable.
- Unknown cloud models are not assumed tool-capable.
- Configurable CapabilityConfidencePolicy enforcement:
  * CONFIRMED_ONLY
  * DECLARED_OR_CONFIRMED
  * ALLOW_INFERRED
- Anthropic live discovery path and error handling (auth failure, rate limit, unsupported endpoint).
- Provider connectivity reported separately from model availability.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.models.capabilities import (
    CapabilityConfidencePolicy,
    CapabilityProvenance,
    ModelCapabilityInfo,
)
from openjarvis.models.discovery import (
    KNOWN_NON_TOOL_MODELS,
    KNOWN_TOOL_MODELS,
    ModelDiscoveryEngine,
)
from openjarvis.models.selector import ModelSelectionEngine


def test_deepseek_r1_not_marked_as_tool_capable():
    """DeepSeek R1 / deepseek-reasoner must NOT be marked as tool capable."""
    discovery = ModelDiscoveryEngine()
    cap = discovery._analyze_model_capability("deepseek-reasoner", provider="deepseek", is_local=False)

    assert cap.supports_tools is False
    assert cap.has_capability("tools") is False
    assert cap.supports_reasoning is True
    assert cap.has_capability("reasoning") is True


def test_known_tool_models_and_unknown_cloud_models():
    """Only allowlisted models are tool-capable; unknown cloud models are UNKNOWN and not tool-capable."""
    discovery = ModelDiscoveryEngine()

    # Known tool model (e.g. gpt-4o)
    cap_tool = discovery._analyze_model_capability("gpt-4o", provider="openai", is_local=False)
    assert cap_tool.supports_tools is True
    assert cap_tool.has_capability("tools") is True
    assert cap_tool.provenance["tools"] == CapabilityProvenance.PROVIDER_DECLARED.value

    # Unknown cloud model (e.g. obscure-custom-cloud-model)
    cap_unknown = discovery._analyze_model_capability("obscure-custom-cloud-model", provider="openrouter", is_local=False)
    assert cap_unknown.supports_tools is False
    assert cap_unknown.has_capability("tools") is False
    assert cap_unknown.provenance["tools"] == CapabilityProvenance.UNKNOWN.value


def test_confidence_policy_enforcement():
    """Enforce CONFIRMED_ONLY, DECLARED_OR_CONFIRMED, and ALLOW_INFERRED thresholds."""
    cap = ModelCapabilityInfo(
        model_id="test-hybrid-model",
        provider="custom",
        is_local=False,
        supports_tools=True,
        supports_coding=True,
        supports_reasoning=True,
        confirmed_capabilities=["streaming"],
        provider_declared_capabilities=["tools", "structured_output"],
        provenance={
            "streaming": CapabilityProvenance.CONFIRMED.value,
            "tools": CapabilityProvenance.PROVIDER_DECLARED.value,
            "structured_output": CapabilityProvenance.PROVIDER_DECLARED.value,
            "coding": CapabilityProvenance.INFERRED.value,
            "reasoning": CapabilityProvenance.UNKNOWN.value,
        },
    )

    # 1. CONFIRMED_ONLY
    assert cap.has_capability("streaming", confidence_policy=CapabilityConfidencePolicy.CONFIRMED_ONLY) is True
    assert cap.has_capability("tools", confidence_policy=CapabilityConfidencePolicy.CONFIRMED_ONLY) is False
    assert cap.has_capability("coding", confidence_policy=CapabilityConfidencePolicy.CONFIRMED_ONLY) is False
    assert cap.has_capability("reasoning", confidence_policy=CapabilityConfidencePolicy.CONFIRMED_ONLY) is False

    # 2. DECLARED_OR_CONFIRMED
    assert cap.has_capability("streaming", confidence_policy=CapabilityConfidencePolicy.DECLARED_OR_CONFIRMED) is True
    assert cap.has_capability("tools", confidence_policy=CapabilityConfidencePolicy.DECLARED_OR_CONFIRMED) is True
    assert cap.has_capability("coding", confidence_policy=CapabilityConfidencePolicy.DECLARED_OR_CONFIRMED) is False
    assert cap.has_capability("reasoning", confidence_policy=CapabilityConfidencePolicy.DECLARED_OR_CONFIRMED) is False

    # 3. ALLOW_INFERRED
    assert cap.has_capability("streaming", confidence_policy=CapabilityConfidencePolicy.ALLOW_INFERRED) is True
    assert cap.has_capability("tools", confidence_policy=CapabilityConfidencePolicy.ALLOW_INFERRED) is True
    assert cap.has_capability("coding", confidence_policy=CapabilityConfidencePolicy.ALLOW_INFERRED) is True
    # Unknown is NEVER allowed even under ALLOW_INFERRED
    assert cap.has_capability("reasoning", confidence_policy=CapabilityConfidencePolicy.ALLOW_INFERRED) is False


def test_anthropic_discovery_path_and_connectivity():
    """Verify Anthropic live discovery path and reporting of connectivity separately from model count."""
    discovery = ModelDiscoveryEngine()

    mock_client = MagicMock()
    mock_model = MagicMock()
    mock_model.id = "claude-3-5-sonnet-20241022"
    mock_client.models.list.return_value = MagicMock(data=[mock_model])

    with patch("openjarvis.engine.cloud.CloudEngine") as mock_engine_cls:
        engine_inst = MagicMock()
        engine_inst._openai_client = None
        engine_inst._anthropic_client = mock_client
        engine_inst._google_client = None
        engine_inst._deepseek_client = None
        engine_inst._openrouter_client = None
        mock_engine_cls.return_value = engine_inst

        models = discovery._probe_cloud_engines()

        # Connectivity reported separately
        health = discovery._provider_health["anthropic"]
        assert health["status"] == "healthy"
        assert health["connected"] is True
        assert health["model_count"] == 1
        assert health["latency_ms"] >= 0.0

        anthropic_models = [m for m in models if m.provider == "anthropic"]
        assert len(anthropic_models) == 1
        assert anthropic_models[0].model_id == "claude-3-5-sonnet-20241022"
        assert anthropic_models[0].supports_tools is True


def test_cloud_error_handling_rate_limit_and_auth_error():
    """Distinguish auth_error (connected=False) from rate_limited (connected=True)."""
    discovery = ModelDiscoveryEngine()

    # 1. Test Auth Error
    mock_client_auth = MagicMock()
    mock_client_auth.models.list.side_effect = Exception("401 Unauthorized: Invalid API key")

    with patch("openjarvis.engine.cloud.CloudEngine") as mock_engine_cls:
        inst = MagicMock()
        inst._openai_client = mock_client_auth
        inst._anthropic_client = None
        inst._google_client = None
        inst._deepseek_client = None
        inst._openrouter_client = None
        mock_engine_cls.return_value = inst

        discovery._probe_cloud_engines()
        h_openai = discovery._provider_health["openai"]
        assert h_openai["status"] == "auth_error"
        assert h_openai["connected"] is False
        assert h_openai["model_count"] == 0

    # 2. Test Rate Limit (Host reachable, connected=True)
    mock_client_rate = MagicMock()
    mock_client_rate.models.list.side_effect = Exception("429 Rate limit exceeded")

    with patch("openjarvis.engine.cloud.CloudEngine") as mock_engine_cls:
        inst = MagicMock()
        inst._openai_client = mock_client_rate
        inst._anthropic_client = None
        inst._google_client = None
        inst._deepseek_client = None
        inst._openrouter_client = None
        mock_engine_cls.return_value = inst

        discovery._probe_cloud_engines()
        h_openai2 = discovery._provider_health["openai"]
        assert h_openai2["status"] == "rate_limited"
        assert h_openai2["connected"] is True
        assert h_openai2["model_count"] == 0

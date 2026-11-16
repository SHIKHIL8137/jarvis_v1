"""Tests for hardware-aware dynamic model selection, scoring, policies, and failure recovery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.models.capabilities import (
    ModelCapabilityInfo,
    ModelPolicy,
)
from openjarvis.models.discovery import ModelDiscoveryEngine
from openjarvis.models.selector import ModelSelectionEngine


@pytest.fixture
def sample_catalog():
    return [
        # Local small model
        ModelCapabilityInfo(
            model_id="qwen3:4b-instruct",
            provider="vllm",
            display_name="Qwen 3 4B Instruct",
            is_local=True,
            is_free=True,
            supports_tools=True,
            supports_structured_output=True,
            supports_coding=True,
            context_length=32768,
            parameter_size_b=4.0,
            vram_mb_required=3200,
            price_per_m_out=0.0,
        ),
        # Local reasoning model
        ModelCapabilityInfo(
            model_id="deepseek-r1:1.5b",
            provider="ollama",
            display_name="DeepSeek R1 1.5B",
            is_local=True,
            is_free=True,
            supports_reasoning=True,
            supports_tools=False,
            context_length=8192,
            parameter_size_b=1.5,
            vram_mb_required=1500,
            price_per_m_out=0.0,
        ),
        # Cloud high-end model
        ModelCapabilityInfo(
            model_id="claude-3-5-sonnet",
            provider="anthropic",
            display_name="Claude 3.5 Sonnet",
            is_local=False,
            is_free=False,
            supports_tools=True,
            supports_structured_output=True,
            supports_coding=True,
            supports_reasoning=True,
            supports_vision=True,
            context_length=200000,
            price_per_m_out=15.0,
        ),
        # Cloud fast model
        ModelCapabilityInfo(
            model_id="gpt-4o-mini",
            provider="openai",
            display_name="GPT-4o Mini",
            is_local=False,
            is_free=False,
            supports_tools=True,
            supports_structured_output=True,
            supports_coding=True,
            context_length=128000,
            price_per_m_out=0.6,
        ),
        # Cloud free model
        ModelCapabilityInfo(
            model_id="qwen-2.5-coder:free",
            provider="openrouter",
            display_name="Qwen 2.5 Coder Free",
            is_local=False,
            is_free=True,
            supports_tools=True,
            supports_structured_output=True,
            supports_coding=True,
            context_length=65536,
            price_per_m_out=0.0,
        ),
    ]


def test_selection_local_first(sample_catalog):
    discovery = MagicMock(spec=ModelDiscoveryEngine)
    discovery.get_all_models.return_value = sample_catalog
    discovery.discover_all.return_value = sample_catalog

    selector = ModelSelectionEngine(
        discovery_engine=discovery,
        default_policy=ModelPolicy.LOCAL_FIRST,
    )
    selector.vram_limit_mb = 4096

    res = selector.select_model(task_type="coding", agent_id="forge")
    assert res.is_local is True
    assert res.model_name == "qwen3:4b-instruct"
    assert res.score > 0.0


def test_selection_privacy_first_never_cloud(sample_catalog):
    discovery = MagicMock(spec=ModelDiscoveryEngine)
    discovery.get_all_models.return_value = sample_catalog
    discovery.discover_all.return_value = sample_catalog

    selector = ModelSelectionEngine(
        discovery_engine=discovery,
        default_policy=ModelPolicy.PRIVACY_FIRST,
    )

    # Even for research or heavy tasks, privacy-first never picks cloud
    res = selector.select_model(task_type="research", agent_id="oracle")
    assert res.is_local is True


def test_selection_cloud_first(sample_catalog):
    discovery = MagicMock(spec=ModelDiscoveryEngine)
    discovery.get_all_models.return_value = sample_catalog
    discovery.discover_all.return_value = sample_catalog

    selector = ModelSelectionEngine(
        discovery_engine=discovery,
        default_policy=ModelPolicy.CLOUD_FIRST,
    )

    # Cloud-first selects free cloud model
    res = selector.select_model(task_type="coding", agent_id="forge")
    assert res.is_local is False
    assert res.model_id == "qwen-2.5-coder:free"


def test_selection_failure_fallback_and_penalty(sample_catalog):
    discovery = MagicMock(spec=ModelDiscoveryEngine)
    discovery.get_all_models.return_value = sample_catalog
    discovery.discover_all.return_value = sample_catalog

    selector = ModelSelectionEngine(
        discovery_engine=discovery,
        default_policy=ModelPolicy.CLOUD_FIRST,
    )

    # Select initial model
    res1 = selector.select_model(task_type="coding")
    first_choice = res1.model_name

    # Record failure on first choice
    selector.record_model_failure(first_choice, "Inference connection timed out")

    # Select again: penalty should reduce score of first choice
    res2 = selector.select_model(task_type="coding")
    assert res2.candidate_scores[first_choice] < res1.candidate_scores[first_choice]


def test_hardware_concurrency_gate(sample_catalog):
    discovery = MagicMock(spec=ModelDiscoveryEngine)
    discovery.get_all_models.return_value = sample_catalog
    discovery.discover_all.return_value = sample_catalog

    selector = ModelSelectionEngine(
        discovery_engine=discovery,
        default_policy=ModelPolicy.LOCAL_FIRST,
    )
    selector.max_concurrent_inferences = 2

    # Simulate 2 active inferences on local GPU
    selector._active_inferences = 2

    # Should overflow to cloud because GPU capacity is reached
    res = selector.select_model(task_type="coding")
    assert res.is_local is False

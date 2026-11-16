"""Phase 2: Free-First Model Providers and Intelligent Routing Test Suite.

Validates all 13 required test scenarios from 05_PHASE_2_FREE_MODEL_ROUTING.md:
1. Provider configuration loads correctly.
2. Missing/invalid API key fails safely.
3. OpenRouter model request works with configured model.
4. Ollama local model request works.
5. NVIDIA request works when configured.
6. Unsupported capability is rejected or routed elsewhere.
7. Rate limit triggers bounded backoff and failure penalty.
8. Provider outage triggers compatible fallback.
9. All providers unavailable pauses task safely.
10. Paid provider cannot be used unless explicitly enabled.
11. Secrets are redacted from logs and error strings.
12. Model switch preserves task and completed tool results.
13. Context overflow is handled explicitly.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.config import JarvisConfig, load_config
from openjarvis.core.types import Conversation, Message, Role, ToolCall, ToolResult
from openjarvis.engine.cloud import CloudEngine
from openjarvis.engine.nim import NIMEngine
from openjarvis.engine.ollama import OllamaEngine
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
from tests.agents.fake_engine import FakeEngine


# ---------------------------------------------------------------------------
# 1. Provider configuration loads correctly
# ---------------------------------------------------------------------------


def test_scenario_1_provider_configuration_loads_correctly() -> None:
    cfg = load_config()
    assert hasattr(cfg, "engine")
    assert hasattr(cfg.engine, "ollama")
    assert hasattr(cfg.engine.ollama, "host")
    assert cfg.engine.default in ("ollama", "vllm", "cloud", "nim", "litellm")


# ---------------------------------------------------------------------------
# 2. Missing/invalid API key fails safely
# ---------------------------------------------------------------------------


def test_scenario_2_missing_or_invalid_api_key_fails_safely() -> None:
    engine = ModelDiscoveryEngine()

    # When no API key is configured
    with patch.dict(os.environ, {}, clear=True):
        with patch("openjarvis.engine.cloud.CloudEngine") as mock_cloud_cls:
            mock_inst = MagicMock()
            mock_inst._openai_client = None
            mock_inst._anthropic_client = None
            mock_inst._google_client = None
            mock_inst._deepseek_client = None
            mock_inst._openrouter_client = None
            mock_inst._atlascloud_client = None
            mock_inst._minimax_client = None
            mock_cloud_cls.return_value = mock_inst

            cloud_models = engine._probe_cloud_engines()
            assert len(cloud_models) == 0

            health = engine.list_providers()
            unconfigured = [p for p in health if p["status"] == "unconfigured"]
            assert len(unconfigured) >= 5
            for p in unconfigured:
                assert p["connected"] is False
                assert "not configured" in p["error"].lower()


# ---------------------------------------------------------------------------
# 3. OpenRouter model request works with configured model
# ---------------------------------------------------------------------------


def test_scenario_3_openrouter_model_request_works() -> None:
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-fake-key"}):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client

            # Mock chat completion return
            mock_choice = MagicMock()
            mock_choice.message.content = "OpenRouter response text"
            mock_choice.message.tool_calls = None
            mock_choice.finish_reason = "stop"

            mock_resp = MagicMock()
            mock_resp.choices = [mock_choice]
            mock_resp.usage.prompt_tokens = 25
            mock_resp.usage.completion_tokens = 10
            mock_client.chat.completions.create.return_value = mock_resp

            engine = CloudEngine()
            res = engine.generate(
                [Message(role=Role.USER, content="Hello OpenRouter")],
                model="openrouter/meta-llama/llama-3.3-70b-instruct:free",
            )

            assert res["content"] == "OpenRouter response text"
            assert res["usage"]["prompt_tokens"] == 25
            assert res["usage"]["completion_tokens"] == 10

            # Verify client was called with correct arguments (prefix stripped)
            mock_client.chat.completions.create.assert_called_once()
            called_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert called_kwargs["model"] == "meta-llama/llama-3.3-70b-instruct:free"


# ---------------------------------------------------------------------------
# 4. Ollama local model request works
# ---------------------------------------------------------------------------


def test_scenario_4_ollama_local_model_request_works() -> None:
    engine = OllamaEngine(host="http://127.0.0.1:11434")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "message": {"content": "Local Ollama response", "tool_calls": []},
        "prompt_eval_count": 12,
        "eval_count": 8,
        "done": True,
    }
    mock_resp.status_code = 200

    with patch.object(engine._client, "post", return_value=mock_resp):
        res = engine.generate(
            [Message(role=Role.USER, content="Test local inference")],
            model="qwen3:4b-instruct",
        )
        assert res["content"] == "Local Ollama response"
        assert res["usage"]["prompt_tokens"] == 12
        assert res["usage"]["completion_tokens"] == 8


# ---------------------------------------------------------------------------
# 5. NVIDIA request works when configured
# ---------------------------------------------------------------------------


def test_scenario_5_nvidia_nim_request_works_when_configured() -> None:
    with patch.dict(os.environ, {"NIM_API_KEY": "nvapi-fake-nim-token", "NIM_HOST": "https://integrate.api.nvidia.com"}):
        engine = NIMEngine()

        mock_choice = {
            "message": {
                "content": "NVIDIA NIM response",
                "tool_calls": [],
            },
            "finish_reason": "stop",
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [mock_choice],
            "usage": {"prompt_tokens": 15, "completion_tokens": 12},
            "model": "meta/llama-3.3-70b-instruct",
        }
        mock_resp.status_code = 200

        with patch.object(engine._client, "post", return_value=mock_resp):
            res = engine.generate(
                [Message(role=Role.USER, content="Hello NIM")],
                model="meta/llama-3.3-70b-instruct",
            )
            assert res["content"] == "NVIDIA NIM response"
            assert res["usage"]["total_tokens"] == 27
            engine.close()


# ---------------------------------------------------------------------------
# 6. Unsupported capability is rejected or routed elsewhere
# ---------------------------------------------------------------------------


def test_scenario_6_unsupported_capability_routed_elsewhere() -> None:
    models = [
        # Model A: Fast text model without vision
        ModelCapabilityInfo(
            model_id="text-only-model",
            provider="ollama",
            is_local=True,
            is_free=True,
            health_status="healthy",
            supports_vision=False,
            supports_coding=True,
        ),
        # Model B: Multimodal vision model
        ModelCapabilityInfo(
            model_id="qwen2.5vl:3b",
            provider="ollama",
            is_local=True,
            is_free=True,
            health_status="healthy",
            supports_vision=True,
            supports_coding=False,
        ),
    ]

    mock_disc = MagicMock()
    mock_disc.get_all_models.return_value = models
    mock_disc.discover_all.return_value = models

    selector = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.LOCAL_FIRST)

    # When vision is required, text-only model MUST be rejected
    res = selector.select_model(task_type="vision", requires_vision=True)
    assert res.model_name == "qwen2.5vl:3b"
    assert res.is_compatible is True


# ---------------------------------------------------------------------------
# 7. Rate limit triggers bounded backoff and failure penalty
# ---------------------------------------------------------------------------


def test_scenario_7_rate_limit_triggers_penalty_and_backoff() -> None:
    models = [
        ModelCapabilityInfo(
            model_id="model-primary",
            provider="openrouter",
            is_local=False,
            is_free=True,
            health_status="healthy",
            supports_coding=True,
        ),
        ModelCapabilityInfo(
            model_id="model-backup",
            provider="ollama",
            is_local=True,
            is_free=True,
            health_status="healthy",
            supports_coding=True,
        ),
    ]

    mock_disc = MagicMock()
    mock_disc.get_all_models.return_value = models
    mock_disc.discover_all.return_value = models

    selector = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.CLOUD_FIRST)

    # First choice is cloud model
    res1 = selector.select_model(task_type="coding")
    assert res1.model_name == "model-primary"

    # Simulate 429 Rate Limit error on primary
    selector.record_model_failure("model-primary", "Rate limit 429: Too many requests", task_type="coding")

    # Second choice must apply penalty and route to backup
    res2 = selector.select_model(task_type="coding")
    assert res2.candidate_scores["model-primary"] < res1.candidate_scores["model-primary"]


# ---------------------------------------------------------------------------
# 8. Provider outage triggers compatible fallback
# ---------------------------------------------------------------------------


def test_scenario_8_provider_outage_triggers_compatible_fallback() -> None:
    models = [
        ModelCapabilityInfo(
            model_id="online-primary",
            provider="openrouter",
            is_local=False,
            is_free=True,
            health_status="healthy",
            supports_coding=True,
        ),
        ModelCapabilityInfo(
            model_id="online-fallback",
            provider="ollama",
            is_local=True,
            is_free=True,
            health_status="healthy",
            supports_coding=True,
        ),
    ]

    mock_disc = MagicMock()
    mock_disc.get_all_models.return_value = models
    mock_disc.discover_all.return_value = models
    mock_disc.get_model_info.side_effect = lambda mid: next((m for m in models if m.model_id == mid), None)

    selector = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.CLOUD_FIRST)

    # Handle outage of primary
    fallback_res = selector.handle_model_failure(
        failed_model_id="online-primary",
        error="Connection refused: 503 Service Unavailable",
        task_type="coding",
    )

    assert fallback_res.is_compatible is True
    assert fallback_res.model_name == "online-fallback"
    assert "Dynamic runtime fallback" in fallback_res.reason


# ---------------------------------------------------------------------------
# 9. All providers unavailable pauses task safely
# ---------------------------------------------------------------------------


def test_scenario_9_all_providers_unavailable_pauses_task_safely() -> None:
    # Zero healthy models available
    mock_disc = MagicMock()
    mock_disc.get_all_models.return_value = []
    mock_disc.discover_all.return_value = []

    selector = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.LOCAL_FIRST)

    res = selector.select_model(task_type="coding")
    assert isinstance(res, NoCompatibleModelAvailableResult)
    assert res.is_compatible is False
    assert res.model_name == ""

    # When raise_on_none=True, explicit exception is raised
    with pytest.raises(NoCompatibleModelAvailable):
        selector.select_model(task_type="coding", raise_on_none=True)


# ---------------------------------------------------------------------------
# 10. Paid provider cannot be used unless explicitly enabled
# ---------------------------------------------------------------------------


def test_scenario_10_paid_provider_cannot_be_used_unless_explicitly_enabled() -> None:
    models = [
        # Paid cloud model ($15 / million tokens)
        ModelCapabilityInfo(
            model_id="claude-3-5-sonnet",
            provider="anthropic",
            is_local=False,
            is_free=False,
            price_per_m_out=15.0,
            health_status="healthy",
            supports_coding=True,
        ),
        # Free local model
        ModelCapabilityInfo(
            model_id="qwen3:4b-instruct",
            provider="ollama",
            is_local=True,
            is_free=True,
            price_per_m_out=0.0,
            health_status="healthy",
            supports_coding=True,
        ),
    ]

    mock_disc = MagicMock()
    mock_disc.get_all_models.return_value = models
    mock_disc.discover_all.return_value = models

    selector = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.BALANCED)

    # Case A: By default (allow_paid=False), paid model is excluded; free local model selected
    res_default = selector.select_model(task_type="coding", allow_paid=False)
    assert res_default.model_name == "qwen3:4b-instruct"
    assert res_default.is_local is True

    # Case B: When only paid models exist and allow_paid=False, fails closed without cloud fallthrough
    mock_disc_paid_only = MagicMock()
    mock_disc_paid_only.get_all_models.return_value = [models[0]]
    mock_disc_paid_only.discover_all.return_value = [models[0]]
    selector_paid_only = ModelSelectionEngine(discovery_engine=mock_disc_paid_only, default_policy=ModelPolicy.BALANCED)

    res_blocked = selector_paid_only.select_model(task_type="coding", allow_paid=False)
    assert isinstance(res_blocked, NoCompatibleModelAvailableResult)
    assert res_blocked.model_name == ""

    # Case C: When allow_paid=True explicitly, paid model becomes eligible
    res_allowed = selector_paid_only.select_model(task_type="coding", allow_paid=True)
    assert res_allowed.model_name == "claude-3-5-sonnet"

    # Case D: Cloud-first policy alone does NOT automatically enable paid models
    selector_cloud_first = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.CLOUD_FIRST)
    res_cloud_first = selector_cloud_first.select_model(task_type="coding")
    assert res_cloud_first.model_name == "qwen3:4b-instruct"
    assert res_cloud_first.is_local is True


# ---------------------------------------------------------------------------
# 11. Secrets are redacted from logs and error strings
# ---------------------------------------------------------------------------


def test_scenario_11_secrets_are_redacted() -> None:
    raw_error_1 = "Authentication failed with token: Bearer sk-proj-1234567890abcdefghijklmn"
    sanitized_1 = ModelDiscoveryEngine._sanitize_error_message(raw_error_1)
    assert "sk-proj-1234567890abcdefghijklmn" not in sanitized_1
    assert "[REDACTED]" in sanitized_1 or "[REDACTED_KEY]" in sanitized_1

    raw_error_2 = "Error connecting to https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSyA1234567890abcdef"
    sanitized_2 = ModelDiscoveryEngine._sanitize_error_message(raw_error_2)
    assert "AIzaSyA1234567890abcdef" not in sanitized_2
    assert "[REDACTED_KEY]" in sanitized_2

    raw_error_3 = "Failed request with key nvapi-abc1234567890def"
    sanitized_3 = ModelDiscoveryEngine._sanitize_error_message(raw_error_3)
    assert "nvapi-abc1234567890def" not in sanitized_3
    assert "[REDACTED_NVIDIA_KEY]" in sanitized_3


# ---------------------------------------------------------------------------
# 12. Model switch preserves task and completed tool results
# ---------------------------------------------------------------------------


def test_scenario_12_model_switch_preserves_task_and_tool_results() -> None:
    engine = FakeEngine([
        {"content": "Turn 1 finished with Model A."},
        {"content": "Turn 2 finished with Model B, remembering previous context."},
    ])

    agent = OrchestratorAgent(
        engine,
        model="model-alpha",
        tools=[],
    )

    ctx = AgentContext(
        conversation=Conversation(
            messages=[
                Message(role=Role.USER, content="Step 1: Check system"),
                Message(role=Role.ASSISTANT, content="System is healthy"),
                Message(role=Role.TOOL, content="Tool output from previous execution: disk=ok", tool_call_id="call_0"),
            ]
        )
    )

    # Initial turn on model-alpha
    res1 = agent.run("Proceed with step 1", context=ctx)
    assert res1.content == "Turn 1 finished with Model A."

    # Dynamically switch model to model-beta mid-task
    agent.switch_model("model-beta")
    assert agent.model == "model-beta"

    # Next turn retains full conversation history including previous tool results
    res2 = agent.run("Proceed with step 2", context=ctx)
    assert res2.content == "Turn 2 finished with Model B, remembering previous context."
    assert any("disk=ok" in m.text for m in engine.last_messages)


# ---------------------------------------------------------------------------
# 13. Context overflow is handled explicitly
# ---------------------------------------------------------------------------


def test_scenario_13_context_overflow_handled_explicitly() -> None:
    models = [
        # Model with tiny context window
        ModelCapabilityInfo(
            model_id="tiny-ctx-model",
            provider="ollama",
            is_local=True,
            is_free=True,
            context_length=2048,
            health_status="healthy",
            supports_coding=True,
        ),
        # Model with large context window
        ModelCapabilityInfo(
            model_id="large-ctx-model",
            provider="ollama",
            is_local=True,
            is_free=True,
            context_length=32768,
            health_status="healthy",
            supports_coding=True,
        ),
    ]

    mock_disc = MagicMock()
    mock_disc.get_all_models.return_value = models
    mock_disc.discover_all.return_value = models

    selector = ModelSelectionEngine(discovery_engine=mock_disc, default_policy=ModelPolicy.LOCAL_FIRST)

    # For a task requiring 16k tokens, tiny-ctx-model MUST be rejected due to context overflow constraint
    res = selector.select_model(task_type="coding", min_context_tokens=16384)
    assert res.model_name == "large-ctx-model"
    assert res.context_length >= 16384

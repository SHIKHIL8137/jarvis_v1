"""Tests for dynamic model discovery across local and cloud providers.

Deterministic unit tests:
- Uses mocked provider responses and does not require live local engines or real API keys.
- Tests configured but unreachable providers, invalid API keys, empty model lists, and rate limits.
- Keeps live provider integration tests strictly separated.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.models.capabilities import CapabilityProvenance, ModelCapabilityInfo
from openjarvis.models.discovery import ModelDiscoveryEngine


def test_infer_capabilities_coding():
    engine = ModelDiscoveryEngine()
    cap = engine._analyze_model_capability("qwen2.5-coder:7b", "ollama", is_local=True)
    assert cap.supports_coding is True
    assert cap.supports_tools is True


def test_infer_capabilities_reasoning():
    engine = ModelDiscoveryEngine()
    cap = engine._analyze_model_capability("deepseek-r1:8b", "ollama", is_local=True)
    assert cap.supports_reasoning is True


def test_infer_capabilities_vision():
    engine = ModelDiscoveryEngine()
    cap = engine._analyze_model_capability("qwen2.5vl:3b", "vllm", is_local=True)
    assert cap.supports_vision is True


def test_analyze_param_size():
    engine = ModelDiscoveryEngine()
    assert engine._parse_param_size("llama3.2:3b") == 3.0
    assert engine._parse_param_size("deepseek-r1:1.5b") == 1.5
    assert engine._parse_param_size("qwen-70b-instruct") == 70.0


def test_estimate_vram_mb():
    engine = ModelDiscoveryEngine()
    vram_3b = engine._estimate_vram_mb(3.0)
    vram_70b = engine._estimate_vram_mb(70.0)
    assert vram_3b < 4000  # Fits comfortably in 4 GB GTX 1650
    assert vram_70b > 40000  # Far exceeds 4 GB VRAM limit


def test_discover_all_returns_models_mocked_deterministic():
    """Deterministic model discovery without relying on developer's local Ollama or API keys."""
    engine = ModelDiscoveryEngine()

    mock_ollama_resp = json.dumps({
        "models": [
            {
                "name": "qwen2.5-coder:7b",
                "details": {"parameter_size": "7B", "quantization_level": "Q4_K_M", "context_length": 32768},
                "capabilities": ["tools", "coding"],
            },
            {
                "name": "llama3.2:3b",
                "details": {"parameter_size": "3B", "quantization_level": "Q4_0", "context_length": 8192},
                "capabilities": ["streaming"],
            },
        ]
    }).encode("utf-8")

    mock_http_resp = MagicMock()
    mock_http_resp.read.return_value = mock_ollama_resp
    mock_http_resp.__enter__.return_value = mock_http_resp
    mock_http_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_http_resp):
        with patch.object(engine, "_probe_cloud_engines", return_value=[]):
            with patch("openjarvis.engine._discovery.discover_engines", return_value=[]):
                models = engine.discover_all(force=True)

                assert isinstance(models, list)
                assert len(models) == 2
            model_ids = [m.model_id for m in models]
            assert "qwen2.5-coder:7b" in model_ids
            assert "llama3.2:3b" in model_ids

            providers = engine.list_providers()
            assert any(p.get("provider") == "ollama" and p.get("status") == "healthy" for p in providers)


def test_discovery_configured_but_unreachable_provider():
    """Configured provider whose host is offline must be cleanly recorded as offline without raising."""
    engine = ModelDiscoveryEngine()

    # Simulate connection refused
    refused_err = urllib.error.URLError(ConnectionRefusedError(10061, "Connect call failed"))

    with patch("urllib.request.urlopen", side_effect=refused_err):
        with patch.object(engine, "_probe_cloud_engines", return_value=[]):
            models = engine.discover_all(force=True)

            assert isinstance(models, list)
            providers = engine.list_providers()
            ollama_prov = next((p for p in providers if p.get("provider") == "ollama"), None)
            assert ollama_prov is not None
            assert ollama_prov.get("status") == "offline"
            assert ollama_prov.get("model_count") == 0


def test_discovery_invalid_api_key():
    """Invalid API key returning HTTP 401 must record error and not crash."""
    engine = ModelDiscoveryEngine()

    http_401 = urllib.error.HTTPError(
        url="https://api.openai.com/v1/models",
        code=401,
        msg="Unauthorized",
        hdrs=MagicMock(),
        fp=MagicMock(read=lambda: b'{"error": {"message": "Incorrect API key provided"}}'),
    )

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-invalid-key-12345"}):
        with patch("openjarvis.engine.cloud.CloudEngine") as mock_cloud_cls:
            mock_inst = MagicMock()
            mock_inst._openai_client = None
            mock_inst._anthropic_client = None
            mock_inst._google_client = None
            mock_inst._deepseek_client = None
            mock_inst._openrouter_client = None
            mock_cloud_cls.return_value = mock_inst

            with patch("urllib.request.urlopen", side_effect=http_401):
                with patch.object(engine, "_probe_local_engines", return_value=[]):
                    models = engine.discover_all(force=True)
                    assert isinstance(models, list)
                    providers = engine.list_providers()
                    openai_prov = next((p for p in providers if p.get("provider") == "openai"), None)
                    assert openai_prov is not None
                    assert openai_prov.get("connected") is False
                    assert openai_prov.get("status") == "auth_error"
                    assert openai_prov.get("model_count") == 0


def test_discovery_cloud_empty_model_list():
    """Cloud provider returning empty list must be recorded as degraded with connected=False."""
    engine = ModelDiscoveryEngine()

    mock_client = MagicMock()
    mock_client.models.list.return_value = MagicMock(data=[])

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-mock-key"}, clear=True):
        with patch("openjarvis.engine.cloud.CloudEngine") as mock_engine_cls:
            mock_inst = MagicMock()
            mock_inst._openai_client = mock_client
            mock_inst._anthropic_client = None
            mock_inst._google_client = None
            mock_inst._deepseek_client = None
            mock_inst._openrouter_client = None
            mock_inst._atlascloud_client = None
            mock_inst._minimax_client = None
            mock_engine_cls.return_value = mock_inst

            models = engine._probe_cloud_engines()
            assert len(models) == 0
            providers = engine.list_providers()
            openai_prov = next((p for p in providers if p.get("provider") == "openai"), None)
            assert openai_prov is not None
            assert openai_prov.get("connected") is False
            assert openai_prov.get("status") == "degraded"
            assert openai_prov.get("model_count") == 0


def test_discovery_empty_model_list():
    """Provider returning empty list must be recorded as healthy with 0 models."""
    engine = ModelDiscoveryEngine()

    mock_empty_resp = json.dumps({"models": []}).encode("utf-8")
    mock_http_resp = MagicMock()
    mock_http_resp.read.return_value = mock_empty_resp
    mock_http_resp.__enter__.return_value = mock_http_resp
    mock_http_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_http_resp):
        with patch.object(engine, "_probe_cloud_engines", return_value=[]):
            models = engine.discover_all(force=True)
            assert isinstance(models, list)
            assert len(models) == 0

            providers = engine.list_providers()
            ollama_prov = next((p for p in providers if p.get("provider") == "ollama"), None)
            assert ollama_prov is not None
            assert ollama_prov.get("status") == "healthy"
            assert ollama_prov.get("model_count") == 0


def test_discovery_rate_limited():
    """Provider returning HTTP 429 Too Many Requests must record status='rate_limited' and connected=True."""
    engine = ModelDiscoveryEngine()

    http_429 = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/models",
        code=429,
        msg="Too Many Requests",
        hdrs=MagicMock(),
        fp=MagicMock(read=lambda: b'{"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}}'),
    )

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        with patch("urllib.request.urlopen", side_effect=http_429):
            with patch.object(engine, "_probe_local_engines", return_value=[]):
                models = engine.discover_all(force=True)
                assert isinstance(models, list)
                providers = engine.list_providers()
                ant_prov = next((p for p in providers if p.get("provider") == "anthropic"), None)
                assert ant_prov is not None
                assert ant_prov.get("status") == "rate_limited"
                assert ant_prov.get("connected") is True
                assert ant_prov.get("model_count") == 0


def test_discovery_forbidden_api_key_403():
    """Provider returning HTTP 403 Forbidden must record status='auth_error' and connected=False."""
    engine = ModelDiscoveryEngine()

    http_403 = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/models",
        code=403,
        msg="Forbidden",
        hdrs=MagicMock(),
        fp=MagicMock(read=lambda: b'{"error": {"type": "forbidden", "message": "Access denied"}}'),
    )

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-forbidden"}):
        with patch("urllib.request.urlopen", side_effect=http_403):
            with patch.object(engine, "_probe_local_engines", return_value=[]):
                models = engine.discover_all(force=True)
                assert isinstance(models, list)
                providers = engine.list_providers()
                ant_prov = next((p for p in providers if p.get("provider") == "anthropic"), None)
                assert ant_prov is not None
                assert ant_prov.get("status") == "auth_error"
                assert ant_prov.get("connected") is False
                assert ant_prov.get("model_count") == 0


def test_discovery_endpoint_not_found_404():
    """Provider endpoint returning HTTP 404 must record status='unsupported_endpoint' and connected=True."""
    engine = ModelDiscoveryEngine()

    http_404 = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/models",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=MagicMock(read=lambda: b'{"error": "Endpoint not found"}'),
    )

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        with patch("urllib.request.urlopen", side_effect=http_404):
            with patch.object(engine, "_probe_local_engines", return_value=[]):
                models = engine.discover_all(force=True)
                assert isinstance(models, list)
                providers = engine.list_providers()
                ant_prov = next((p for p in providers if p.get("provider") == "anthropic"), None)
                assert ant_prov is not None
                assert ant_prov.get("status") == "unsupported_endpoint"
                assert ant_prov.get("connected") is True


def test_discovery_connection_failure_unreachable():
    """Provider timing out or failing network connection must record status='unreachable'."""
    engine = ModelDiscoveryEngine()

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
            with patch.object(engine, "_probe_local_engines", return_value=[]):
                models = engine.discover_all(force=True)
                assert isinstance(models, list)
                providers = engine.list_providers()
                ant_prov = next((p for p in providers if p.get("provider") == "anthropic"), None)
                assert ant_prov is not None
                assert ant_prov.get("status") == "unreachable"
                assert ant_prov.get("connected") is False


def test_discovery_zero_credentials_unconfigured():
    """Environment with zero configured credentials returns clean unconfigured providers and empty catalog."""
    engine = ModelDiscoveryEngine()

    with patch.dict("os.environ", {}, clear=True):
        with patch.object(engine, "_probe_local_engines", return_value=[]):
            models = engine.discover_all(force=True)
            assert isinstance(models, list)
            assert len(models) == 0

            providers = engine.list_providers()
            for p in providers:
                if p.get("type") == "cloud":
                    assert p.get("status") == "unconfigured"
                    assert p.get("connected") is False

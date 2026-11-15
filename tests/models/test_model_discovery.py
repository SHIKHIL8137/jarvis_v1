"""Tests for dynamic model discovery across local and cloud providers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.models.capabilities import ModelCapabilityInfo
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


def test_discover_all_returns_models():
    engine = ModelDiscoveryEngine()
    models = engine.discover_all()
    assert isinstance(models, list)
    assert len(models) > 0
    # Provider health should be populated
    providers = engine.list_providers()
    assert isinstance(providers, list)
    assert len(providers) > 0

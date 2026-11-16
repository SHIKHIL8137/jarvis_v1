"""Unit tests for Windows TTS backend."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import openjarvis.speech  # noqa: F401
from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.windows_tts import WindowsTTSBackend


def test_windows_tts_is_registered():
    if not TTSRegistry.contains("windows_tts"):
        TTSRegistry.register_value("windows_tts", WindowsTTSBackend)
    assert TTSRegistry.contains("windows_tts")
    assert TTSRegistry.get("windows_tts") is WindowsTTSBackend


def test_windows_tts_health():
    backend = WindowsTTSBackend()
    # On Windows, health should be True if SAPI5 is available
    if sys.platform == "win32":
        assert backend.health() is True
    else:
        assert backend.health() is False


def test_windows_tts_available_voices():
    backend = WindowsTTSBackend()
    if sys.platform == "win32":
        voices = backend.available_voices()
        assert isinstance(voices, list)

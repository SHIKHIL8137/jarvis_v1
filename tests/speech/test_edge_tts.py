"""Unit tests for Edge TTS backend."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openjarvis.speech  # noqa: F401
from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.edge_tts_backend import EdgeTTSBackend


def test_edge_tts_is_registered():
    if not TTSRegistry.contains("edge_tts"):
        TTSRegistry.register_value("edge_tts", EdgeTTSBackend)
    assert TTSRegistry.contains("edge_tts")
    backend_cls = TTSRegistry.get("edge_tts")
    assert backend_cls is EdgeTTSBackend


def test_edge_tts_health():
    backend = EdgeTTSBackend()
    assert backend.health() is True


def test_edge_tts_available_voices():
    backend = EdgeTTSBackend()
    voices = backend.available_voices()
    assert len(voices) > 0
    assert "en-GB-RyanNeural" in voices
    assert "en-GB-ThomasNeural" in voices
    assert "en-US-GuyNeural" in voices


@pytest.mark.asyncio
async def test_edge_tts_synthesize_stream_mock():
    backend = EdgeTTSBackend()

    mock_comm = MagicMock()

    async def mock_stream():
        yield {"type": "audio", "data": b"chunk_1"}
        yield {"type": "audio", "data": b"chunk_2"}

    mock_comm.stream = mock_stream

    with patch("edge_tts.Communicate", return_value=mock_comm):
        chunks = []
        async for chunk in backend.synthesize_stream("Hello from Jarvis", voice_id="en-GB-RyanNeural"):
            chunks.append(chunk)
        assert chunks == [(b"chunk_1", 24000), (b"chunk_2", 24000)]


def test_edge_tts_synthesize_sync_mock():
    backend = EdgeTTSBackend()

    mock_comm = MagicMock()

    async def mock_stream():
        yield {"type": "audio", "data": b"sync_audio_payload"}

    mock_comm.stream = mock_stream

    with patch("edge_tts.Communicate", return_value=mock_comm):
        result = backend.synthesize("Test sync", voice_id="en-GB-RyanNeural")
        assert result.audio == b"sync_audio_payload"

"""Unit tests for Advanced Voice Assistant capabilities (Milestone 1):
- EchoGuard self-echo prevention and barge-in interruption
- LanguageManager language consistency and voice selection
- VoiceSessionManager state machine and instant acknowledgment
- Speech routes for audio devices and voice control
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from openjarvis.speech.echo_guard import EchoGuard, INTERRUPT_KEYWORDS
from openjarvis.speech.language import LanguageManager
from openjarvis.speech.session_manager import VoiceSessionManager, VoiceState


class TestEchoGuard:
    def test_playback_lifecycle(self):
        cb = MagicMock()
        guard = EchoGuard(guard_interval_seconds=0.2, interruption_callback=cb)
        assert guard.is_playing is False
        assert guard.is_in_guard_interval() is False

        guard.start_playback(text="Hello world, I am Jarvis.")
        assert guard.is_playing is True
        assert guard.is_in_guard_interval() is True

        guard.stop_playback()
        assert guard.is_playing is False
        # Within guard interval
        assert guard.is_in_guard_interval() is True

        # Wait for guard interval to expire
        time.sleep(0.25)
        assert guard.is_in_guard_interval() is False

    def test_barge_in_interruption(self):
        cb = MagicMock()
        guard = EchoGuard(guard_interval_seconds=0.2, interruption_callback=cb)
        guard.start_playback(text="I am explaining the database schema.")

        # User says "stop"
        interrupted = guard.check_barge_in("Jarvis, please stop")
        assert interrupted is True
        assert guard.is_playing is False
        cb.assert_called_once()

    def test_self_echo_filtered(self):
        guard = EchoGuard(guard_interval_seconds=0.2)
        guard.start_playback(text="I am explaining the database schema in detail.")

        # Microphone picks up exact echo from speaker
        interrupted = guard.check_barge_in("explaining the database schema in detail")
        assert interrupted is False
        assert guard.is_playing is True

    def test_frame_suppression(self):
        guard = EchoGuard(guard_interval_seconds=0.2)
        # Idle, not in guard interval -> do not suppress
        assert guard.should_suppress_frame(600.0, silence_threshold=500.0) is False

        # Playing -> low amplitude is suppressed
        guard.start_playback()
        assert guard.should_suppress_frame(600.0, silence_threshold=500.0) is True
        # High amplitude barge-in speech passes through
        assert guard.should_suppress_frame(1500.0, silence_threshold=500.0) is False

        guard.stop_playback()
        # In guard interval -> suppressed
        assert guard.should_suppress_frame(600.0, silence_threshold=500.0) is True


class TestLanguageManager:
    def test_script_detection(self):
        lm = LanguageManager()
        # Hindi (Devanagari)
        assert lm.detect_language("नमस्ते आप कैसे हैं") == "hi"
        # Japanese
        assert lm.detect_language("こんにちは世界") == "ja"
        # Chinese
        assert lm.detect_language("你好世界") == "zh"
        # English
        assert lm.detect_language("Hello how are you doing today?") == "en"

    def test_tts_voice_mapping(self):
        lm = LanguageManager()
        assert lm.get_tts_voice("en") == "bm_george"
        assert lm.get_tts_voice("hi") == "hf_alpha"
        assert lm.get_tts_voice("es") == "ef_dora"
        assert lm.get_tts_voice("fr") == "ff_siwis"

    def test_language_directive_injection(self):
        lm = LanguageManager()
        # English -> empty directive
        assert lm.format_language_directive("en") == ""
        # Hindi directive
        directive = lm.format_language_directive("hi")
        assert "Hindi" in directive
        assert "Preserve code snippets" in directive


class TestVoiceSessionManager:
    def test_state_transitions(self):
        transitions = []
        mgr = VoiceSessionManager(state_change_callback=lambda s: transitions.append(s))
        assert mgr.state == VoiceState.IDLE

        mgr._set_state(VoiceState.ACTIVE_LISTENING)
        assert mgr.state == VoiceState.ACTIVE_LISTENING
        assert VoiceState.ACTIVE_LISTENING in transitions

    def test_instant_acknowledgment(self):
        mgr = VoiceSessionManager()
        # Complex research task
        ack = mgr.generate_instant_ack("Jarvis, research the latest AI benchmarks and compare them")
        assert ack is not None
        assert "research" in ack.lower()

        # Analysis task
        ack2 = mgr.generate_instant_ack("Can you analyze this algorithm?")
        assert ack2 is not None
        assert "analyzing" in ack2.lower()

        # Simple greeting
        assert mgr.generate_instant_ack("Hello there") is None

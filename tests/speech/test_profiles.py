"""Unit tests for voice profiles and profile management."""

from __future__ import annotations

from openjarvis.core.config import JarvisConfig, SpeechConfig
from openjarvis.speech.profiles import (
    VoiceProfile,
    VoiceProfileManager,
    get_voice_profile_manager,
)


def test_standard_profiles_exist():
    mgr = get_voice_profile_manager()
    profiles = mgr.list_profiles()
    ids = {p.id for p in profiles}
    assert "classic_jarvis" in ids
    assert "professional" in ids
    assert "friendly" in ids
    assert "concise" in ids


def test_classic_jarvis_profile_parameters():
    mgr = get_voice_profile_manager()
    profile = mgr.get_profile("classic_jarvis")
    assert profile is not None
    assert profile.backend == "edge_tts"
    assert "RyanNeural" in profile.voice_id
    assert profile.speed < 1.0  # Measured, deliberate pacing


def test_fallback_on_unknown_profile():
    mgr = get_voice_profile_manager()
    fallback = mgr.get_profile("non_existent_profile")
    assert fallback is not None
    assert fallback.id == "classic_jarvis"


def test_apply_profile_to_config():
    mgr = get_voice_profile_manager()
    cfg = JarvisConfig(speech=SpeechConfig())

    mgr.apply_profile("professional", cfg)
    assert cfg.speech.voice_profile == "professional"
    assert "ThomasNeural" in cfg.speech.voice_id
    assert cfg.speech.tts_backend == "edge_tts"
    assert cfg.speech.voice_speed == 1.0


def test_register_custom_profile():
    mgr = VoiceProfileManager()
    custom = VoiceProfile(
        profile_id="custom_agent",
        name="Custom Agent",
        description="Testing custom profile registration",
        backend="windows_tts",
        voice_id="Microsoft David Desktop",
        speed=1.05,
        pitch="+2Hz",
    )
    mgr.register_profile(custom)
    retrieved = mgr.get_profile("custom_agent")
    assert retrieved.id == "custom_agent"
    assert retrieved.pitch == "+2Hz"

"""Configurable Voice Profiles for the Jarvis Assistant.

Provides authentic, distinct voice personalities:
1. Classic Jarvis: Calm, sophisticated, cinematic British cadence (RyanNeural / bm_george)
2. Professional: Crisp, neutral, articulate delivery (ThomasNeural / bm_lewis)
3. Friendly: Warm, engaging, natural conversational tone (GuyNeural / am_adam)
4. Concise: High-efficiency rapid delivery with abbreviated pauses (RyanNeural / bm_george)
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VoiceProfile:
    """Distinct acoustic and prosodic configuration for assistant speech."""

    profile_id: str
    name: str
    description: str
    backend: str
    voice_id: str
    speed: float = 1.0
    pitch: str = "+0Hz"
    volume: float = 1.0
    pause_duration: float = 0.25
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Built-in calibrated voice profiles
BUILTIN_PROFILES: Dict[str, VoiceProfile] = {
    "classic_jarvis": VoiceProfile(
        profile_id="classic_jarvis",
        name="Classic Jarvis",
        description="Calm, sophisticated, cinematic British assistant voice inspired by JARVIS.",
        backend="edge_tts",
        voice_id="en-GB-RyanNeural",
        speed=0.96,
        pitch="-1Hz",
        volume=1.0,
        pause_duration=0.30,
        metadata={
            "personality": "cinematic_assistant",
            "accent": "British English",
            "gender": "male",
            "fallback_voice": {
                "kokoro": "bm_george",
                "openai_tts": "onyx",
                "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
                "windows_tts": "Microsoft David Desktop",
            },
        },
    ),
    "professional": VoiceProfile(
        profile_id="professional",
        name="Professional",
        description="Clear, neutral, articulate delivery for technical briefings and engineering tasks.",
        backend="edge_tts",
        voice_id="en-GB-ThomasNeural",
        speed=1.00,
        pitch="+0Hz",
        volume=1.0,
        pause_duration=0.22,
        metadata={
            "personality": "executive_briefing",
            "accent": "British English",
            "gender": "male",
            "fallback_voice": {
                "kokoro": "bm_lewis",
                "openai_tts": "echo",
                "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
                "windows_tts": "Microsoft David Desktop",
            },
        },
    ),
    "friendly": VoiceProfile(
        profile_id="friendly",
        name="Friendly",
        description="Warm, engaging, natural conversational tone for casual interactions.",
        backend="edge_tts",
        voice_id="en-US-GuyNeural",
        speed=1.02,
        pitch="+1Hz",
        volume=1.0,
        pause_duration=0.18,
        metadata={
            "personality": "warm_conversational",
            "accent": "American English",
            "gender": "male",
            "fallback_voice": {
                "kokoro": "am_adam",
                "openai_tts": "alloy",
                "elevenlabs": "21m00Tcm4TlvDq8ikWAM",
                "windows_tts": "Microsoft Zira Desktop",
            },
        },
    ),
    "concise": VoiceProfile(
        profile_id="concise",
        name="Concise",
        description="Fast, crisp delivery with minimal pauses for high-speed workflows.",
        backend="edge_tts",
        voice_id="en-GB-RyanNeural",
        speed=1.15,
        pitch="+0Hz",
        volume=1.0,
        pause_duration=0.12,
        metadata={
            "personality": "high_efficiency",
            "accent": "British English",
            "gender": "male",
            "fallback_voice": {
                "kokoro": "bm_george",
                "openai_tts": "onyx",
                "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
                "windows_tts": "Microsoft David Desktop",
            },
        },
    ),
}


class VoiceProfileManager:
    """Manages active and registered voice profiles."""

    def __init__(self) -> None:
        self._profiles: Dict[str, VoiceProfile] = {k: copy.deepcopy(v) for k, v in BUILTIN_PROFILES.items()}

    def get_profile(self, profile_id: str) -> Optional[VoiceProfile]:
        """Return the voice profile with *profile_id*, or None if not found."""
        return self._profiles.get(profile_id)

    def list_profiles(self) -> List[VoiceProfile]:
        """Return all available voice profiles."""
        return list(self._profiles.values())

    def register_profile(self, profile: VoiceProfile) -> None:
        """Register or overwrite a voice profile."""
        self._profiles[profile.profile_id] = profile

    def resolve_voice_for_backend(self, profile_id: str, backend_id: str) -> str:
        """Resolve the voice ID for a given backend under this profile."""
        prof = self.get_profile(profile_id)
        if not prof:
            prof = self._profiles["classic_jarvis"]

        if prof.backend == backend_id and prof.voice_id:
            return prof.voice_id

        # Check fallback mapping for this profile
        fallbacks = prof.metadata.get("fallback_voice", {})
        if backend_id in fallbacks:
            return fallbacks[backend_id]

        from openjarvis.speech._tts_discovery import default_voice_for
        return default_voice_for(backend_id)


_GLOBAL_PROFILE_MANAGER = VoiceProfileManager()


def get_voice_profile_manager() -> VoiceProfileManager:
    """Return the global VoiceProfileManager singleton."""
    return _GLOBAL_PROFILE_MANAGER


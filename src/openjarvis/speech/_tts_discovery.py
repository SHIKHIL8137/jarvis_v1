"""Auto-discover available text-to-speech backends.

Mirrors ``speech/_discovery.py``, which does the same for speech-to-text. The
selection rules used to live in ``cli/_voice_chat.py``; they moved here so the
API server can reuse them instead of importing from the CLI package.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from openjarvis.core.config import JarvisConfig

logger = logging.getLogger(__name__)

# Priority order: local & zero-key neural first, then cloud, then OS native fallback.
TTS_BACKEND_ORDER = (
    "edge_tts",
    "kokoro",
    "openai_tts",
    "cartesia",
    "elevenlabs",
    "windows_tts",
)

# Voice IDs are backend-specific and NOT portable. ``speech.voice_id`` applies
# only to ``speech.tts_backend``; if synthesis falls back to another backend we
# use that backend's own default rather than passing an unrecognized ID through.
BACKEND_DEFAULT_VOICE = {
    "edge_tts": "en-GB-RyanNeural",  # Calm, intelligent, sophisticated British male (classic JARVIS)
    "kokoro": "bm_george",  # British male
    "openai_tts": "onyx",  # deepest OpenAI preset
    "cartesia": "",  # no safe static default; let Cartesia choose
    "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",  # "George", British male
    "windows_tts": "Microsoft David Desktop",  # Windows SAPI5 offline fallback
}


def default_voice_for(backend_id: str) -> str:
    """Return the fallback voice ID for *backend_id*, or an empty string."""
    return BACKEND_DEFAULT_VOICE.get(backend_id, "")


def get_tts_backend(
    preferred: str = "",
    *,
    attempted: Optional[set[str]] = None,
) -> Optional[Any]:
    """Return the first healthy TTS backend, preferring *preferred*.

    ``attempted`` lets a caller carry state across calls so a backend that
    already failed is not retried; it is mutated in place.
    """
    # Import triggers built-in backend registration only when voice output is
    # actually requested.
    import openjarvis.speech  # noqa: F401
    from openjarvis.core.registry import TTSRegistry

    seen = attempted if attempted is not None else set()

    for key in dict.fromkeys((preferred, *TTS_BACKEND_ORDER)):
        if not key or key in seen:
            continue
        seen.add(key)
        if not TTSRegistry.contains(key):
            continue
        try:
            candidate = TTSRegistry.get(key)()
            if candidate.health():
                return candidate
        except Exception:
            logger.debug("TTS backend %s unavailable", key, exc_info=True)
            continue
    return None


def voice_preferences(config: "JarvisConfig") -> tuple[str, str, float]:
    """Resolve ``(tts_backend, voice_id, speed)`` from *config*."""
    speech = getattr(config, "speech", None)
    profile_id = getattr(speech, "voice_profile", "classic_jarvis") or "classic_jarvis"
    from openjarvis.speech.profiles import get_voice_profile_manager

    profile = get_voice_profile_manager().get_profile(profile_id)
    default_backend = profile.backend if profile else "edge_tts"
    default_voice = profile.voice_id if profile else "en-GB-RyanNeural"
    default_speed = profile.speed if profile else 0.96

    backend = getattr(speech, "tts_backend", "") or default_backend
    voice_id = getattr(speech, "voice_id", "") or default_voice
    speed = float(getattr(speech, "voice_speed", 0.0) or default_speed)
    return backend, voice_id, speed


__all__ = [
    "BACKEND_DEFAULT_VOICE",
    "TTS_BACKEND_ORDER",
    "default_voice_for",
    "get_tts_backend",
    "voice_preferences",
]

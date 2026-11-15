"""Speech subsystem — speech-to-text and text-to-speech backends."""

import importlib

# Optional STT backends — each registers itself via @SpeechRegistry.register()
for _mod in ("faster_whisper", "openai_whisper", "deepgram"):
    try:
        importlib.import_module(f".{_mod}", __name__)
    except ImportError:
        pass

# Optional TTS backends — each registers itself via @TTSRegistry.register()
for _mod in ("cartesia_tts", "elevenlabs_tts", "kokoro_tts", "openai_tts"):
    try:
        importlib.import_module(f".{_mod}", __name__)
    except ImportError:
        pass

from openjarvis.speech.echo_guard import EchoGuard, get_echo_guard
from openjarvis.speech.language import LanguageManager, get_language_manager
from openjarvis.speech.session_manager import (
    VoiceSessionManager,
    VoiceState,
    get_voice_session_manager,
)
from openjarvis.speech.voice_io import list_audio_devices, play_wav, record_until_silence

__all__ = [
    "EchoGuard",
    "LanguageManager",
    "VoiceSessionManager",
    "VoiceState",
    "get_echo_guard",
    "get_language_manager",
    "get_voice_session_manager",
    "list_audio_devices",
    "play_wav",
    "record_until_silence",
]

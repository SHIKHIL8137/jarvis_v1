"""Speech subsystem — speech-to-text and text-to-speech backends."""

import importlib

# Optional STT backends — each registers itself via @SpeechRegistry.register()
for _mod in ("faster_whisper", "openai_whisper", "deepgram"):
    try:
        importlib.import_module(f".{_mod}", __name__)
    except ImportError:
        pass

# Optional TTS backends — each registers itself via @TTSRegistry.register()
for _mod in ("cartesia_tts", "edge_tts_backend", "elevenlabs_tts", "kokoro_tts", "openai_tts", "windows_tts"):
    try:
        importlib.import_module(f".{_mod}", __name__)
    except ImportError:
        pass

from openjarvis.speech.echo_guard import EchoGuard, get_echo_guard
from openjarvis.speech.language import LanguageManager, get_language_manager
from openjarvis.speech.profiles import (
    VoiceProfile,
    VoiceProfileManager,
    get_voice_profile_manager,
)
from openjarvis.speech.pronunciation import (
    PronunciationNormalizer,
    get_pronunciation_normalizer,
    normalize_for_speech,
)
from openjarvis.speech.segmenter import segment_sentences, stream_sentences
from openjarvis.speech.session_manager import (
    VoiceSessionManager,
    VoiceState,
    get_voice_session_manager,
)
from openjarvis.speech.voice_io import list_audio_devices, play_wav, record_until_silence

__all__ = [
    "EchoGuard",
    "LanguageManager",
    "PronunciationNormalizer",
    "VoiceProfile",
    "VoiceProfileManager",
    "VoiceSessionManager",
    "VoiceState",
    "get_echo_guard",
    "get_language_manager",
    "get_pronunciation_normalizer",
    "get_voice_profile_manager",
    "get_voice_session_manager",
    "list_audio_devices",
    "normalize_for_speech",
    "play_wav",
    "record_until_silence",
    "segment_sentences",
    "stream_sentences",
]

"""Centralized language detection, voice mapping, and language consistency management."""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ISO-639-1 / Whisper language codes to Kokoro voice ID mappings
_LANG_TO_KOKORO_VOICE: Dict[str, str] = {
    "en": "bm_george",
    "hi": "hf_alpha",
    "es": "ef_dora",
    "fr": "ff_siwis",
    "it": "if_sara",
    "ja": "jf_tebura",
    "zh": "zf_xiaobei",
    "pt": "pf_dora",
    "de": "bm_george",  # fallback to British male
}

# Language names for human display and system prompts
LANGUAGE_NAMES: Dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
    "pt": "Portuguese",
    "de": "German",
    "ru": "Russian",
    "ar": "Arabic",
}

# Script heuristics for instant detection on text
_SCRIPT_PATTERNS: list[Tuple[re.Pattern, str]] = [
    (re.compile(r"[\u3040-\u309F\u30A0-\u30FF]"), "ja"),  # Hiragana/Katakana (Japanese) - check before CJK
    (re.compile(r"[\u0900-\u097F]"), "hi"),  # Devanagari (Hindi, Marathi, Sanskrit)
    (re.compile(r"[\u4E00-\u9FFF]"), "zh"),  # CJK Unified Ideographs (Chinese)
    (re.compile(r"[\u0400-\u04FF]"), "ru"),  # Cyrillic
    (re.compile(r"[\u0600-\u06FF]"), "ar"),  # Arabic
]


class LanguageManager:
    """Manages active conversation language, language consistency, and TTS voice selection."""

    def __init__(self, default_language: str = "en") -> None:
        self._default_language = default_language
        self._active_language = default_language

    @property
    def active_language(self) -> str:
        return self._active_language

    def detect_language(self, text: str, whisper_detected: Optional[str] = None) -> str:
        """Detect language from text using script analysis and Whisper metadata.

        Preserves the user's spoken language without unintended translation.
        """
        if not text:
            return self._active_language

        # If whisper provided high-confidence detection for non-English, check it
        if whisper_detected and whisper_detected != "en" and whisper_detected in LANGUAGE_NAMES:
            self._active_language = whisper_detected
            return whisper_detected

        # Check script patterns
        for pattern, lang_code in _SCRIPT_PATTERNS:
            if pattern.search(text):
                self._active_language = lang_code
                return lang_code

        # Check for Spanish/French/German diacritics
        if re.search(r"[áéíóúüñ¿¡]", text, re.IGNORECASE):
            self._active_language = "es"
            return "es"
        if re.search(r"[àâçéèêëîïôûùüÿœæ]", text, re.IGNORECASE):
            self._active_language = "fr"
            return "fr"
        if re.search(r"[äöüß]", text, re.IGNORECASE):
            self._active_language = "de"
            return "de"

        # Check for English / Latin ASCII
        if re.search(r"[A-Za-z]", text):
            self._active_language = "en"
            return "en"

        return self._active_language

    def get_tts_voice(self, language: Optional[str] = None, backend: str = "kokoro") -> str:
        """Return the best TTS voice for the active or given language."""
        lang = language or self._active_language
        if backend == "kokoro":
            return _LANG_TO_KOKORO_VOICE.get(lang, "bm_george")
        elif backend == "openai_tts":
            return "onyx"
        return ""

    def format_language_directive(self, language: Optional[str] = None) -> str:
        """Generate a system prompt instruction to keep the assistant's response
        in the user's spoken language while preserving technical code terms."""
        lang = language or self._active_language
        if lang == "en":
            return ""

        lang_name = LANGUAGE_NAMES.get(lang, lang.upper())
        return (
            f"\n\n[Language Directive]\n"
            f"The user is speaking in {lang_name} ({lang}). Respond in natural {lang_name}. "
            f"Do not translate unless explicitly requested. "
            f"Preserve code snippets, technical commands, file paths, and function names in English."
        )


_GLOBAL_LANGUAGE_MANAGER = LanguageManager()


def get_language_manager() -> LanguageManager:
    return _GLOBAL_LANGUAGE_MANAGER

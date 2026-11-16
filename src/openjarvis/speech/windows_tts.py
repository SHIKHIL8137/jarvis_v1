"""Windows native SAPI5 text-to-speech backend — zero-network, zero-download offline fallback."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from typing import List, Optional

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.pronunciation import normalize_for_speech
from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)


@TTSRegistry.register("windows_tts")
class WindowsTTSBackend(TTSBackend):
    """Native Windows SAPI5 voice synthesis for offline environments."""

    backend_id = "windows_tts"

    def __init__(self, voice_id: str = "") -> None:
        self.default_voice = voice_id

    def available_voices(self) -> List[str]:
        """Return list of available Windows SAPI voice names."""
        if sys.platform != "win32":
            return []
        try:
            import win32com.client

            voice = win32com.client.Dispatch("SAPI.SpVoice")
            tokens = voice.GetVoices()
            return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
        except Exception as exc:
            logger.debug("Failed to enumerate Windows SAPI voices: %s", exc)
            return []

    def health(self) -> bool:
        """Check if SAPI5 is usable on this platform."""
        if sys.platform != "win32":
            return False
        try:
            import win32com.client

            win32com.client.Dispatch("SAPI.SpVoice")
            return True
        except Exception:
            return False

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
        normalize: bool = True,
    ) -> TTSResult:
        """Synthesize text using Windows SAPI5 into WAV audio."""
        if sys.platform != "win32":
            raise RuntimeError("WindowsTTSBackend is only supported on Windows.")

        import win32com.client

        normalized = normalize_for_speech(text) if normalize else text.strip()
        if not normalized:
            return TTSResult(audio=b"", format="wav", voice_id=voice_id)

        target_voice = voice_id or self.default_voice
        sp_voice = win32com.client.Dispatch("SAPI.SpVoice")

        # Select specific voice if specified
        if target_voice:
            tokens = sp_voice.GetVoices()
            for i in range(tokens.Count):
                desc = tokens.Item(i).GetDescription()
                if target_voice.lower() in desc.lower():
                    sp_voice.Voice = tokens.Item(i)
                    break

        # SAPI Rate ranges from -10 to +10. Map 0.5-2.0 to -10 to +10.
        sapi_rate = int(max(-10, min(10, (speed - 1.0) * 10)))
        sp_voice.Rate = sapi_rate

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name

        try:
            sp_stream = win32com.client.Dispatch("SAPI.SpFileStream")
            sp_stream.Open(wav_path, 3)  # 3 = SSFMCreateForWrite
            sp_voice.AudioOutputStream = sp_stream
            sp_voice.Speak(normalized)
            sp_stream.Close()
            sp_voice.AudioOutputStream = None

            with open(wav_path, "rb") as f:
                audio_bytes = f.read()
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

        # Estimate duration
        duration = round(len(audio_bytes) / 32000.0, 2)
        return TTSResult(
            audio=audio_bytes,
            format="wav",
            duration_seconds=duration,
            voice_id=target_voice or "default",
            sample_rate=16000,
            metadata={"backend": "windows_tts", "rate": sapi_rate},
        )

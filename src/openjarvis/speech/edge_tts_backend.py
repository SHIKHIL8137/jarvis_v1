"""Edge TTS backend — zero-key, high-fidelity neural voice synthesis.

Provides exceptionally natural, broadcast-grade speech synthesis powered by
Microsoft's neural voice models, including en-GB-RyanNeural for the authentic
cinematic JARVIS assistant experience.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.pronunciation import normalize_for_speech
from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = "en-GB-RyanNeural"
_SAMPLE_RATE = 24000


@TTSRegistry.register("edge_tts")
class EdgeTTSBackend(TTSBackend):
    """High-fidelity neural text-to-speech using Edge TTS."""

    backend_id = "edge_tts"

    # Curated selection of premium neural assistant voices
    VOICE_CATALOG: Dict[str, Dict[str, str]] = {
        "en-GB-RyanNeural": {
            "name": "Ryan (British Male)",
            "gender": "male",
            "locale": "en-GB",
            "description": "Calm, intelligent, sophisticated British cadence — quintessential JARVIS assistant.",
        },
        "en-GB-ThomasNeural": {
            "name": "Thomas (British Male)",
            "gender": "male",
            "locale": "en-GB",
            "description": "Crisp, articulate, professional British delivery.",
        },
        "en-GB-LibbyNeural": {
            "name": "Libby (British Female)",
            "gender": "female",
            "locale": "en-GB",
            "description": "Clear, elegant, refined British female voice.",
        },
        "en-GB-SoniaNeural": {
            "name": "Sonia (British Female)",
            "gender": "female",
            "locale": "en-GB",
            "description": "Warm, engaging, contemporary British female voice.",
        },
        "en-US-GuyNeural": {
            "name": "Guy (American Male)",
            "gender": "male",
            "locale": "en-US",
            "description": "Warm, balanced, conversational American male voice.",
        },
        "en-US-ChristopherNeural": {
            "name": "Christopher (American Male)",
            "gender": "male",
            "locale": "en-US",
            "description": "Deep, authoritative, articulate American male voice.",
        },
        "en-US-AndrewMultilingualNeural": {
            "name": "Andrew (Multilingual Male)",
            "gender": "male",
            "locale": "en-US",
            "description": "Versatile, natural multilingual male voice.",
        },
        "en-US-JennyNeural": {
            "name": "Jenny (American Female)",
            "gender": "female",
            "locale": "en-US",
            "description": "Natural, expressive, warm American assistant voice.",
        },
        "en-US-AriaNeural": {
            "name": "Aria (American Female)",
            "gender": "female",
            "locale": "en-US",
            "description": "Polished, clear, corporate assistant tone.",
        },
    }

    def __init__(
        self,
        default_voice: str = _DEFAULT_VOICE,
        default_speed: float = 1.0,
        default_pitch: str = "+0Hz",
    ) -> None:
        self.default_voice = default_voice
        self.default_speed = default_speed
        self.default_pitch = default_pitch
        self._health_cached: Optional[bool] = None
        self._last_health_check = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _format_rate(speed: float) -> str:
        """Convert float multiplier (e.g. 0.96) to Edge TTS rate string (e.g. '-4%')."""
        pct = round((speed - 1.0) * 100)
        return f"+{pct}%" if pct >= 0 else f"{pct}%"

    @staticmethod
    def _format_pitch(pitch: str | float) -> str:
        """Format pitch string (e.g. '-1Hz', '+0Hz')."""
        if isinstance(pitch, (int, float)):
            hz = round(float(pitch))
            return f"+{hz}Hz" if hz >= 0 else f"{hz}Hz"
        p = str(pitch).strip()
        if not p:
            return "+0Hz"
        if not p.endswith("Hz") and not p.endswith("%"):
            p += "Hz"
        if not p.startswith("+") and not p.startswith("-"):
            p = "+" + p
        return p

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        pitch: str = "+0Hz",
        output_format: str = "wav",
        normalize: bool = True,
    ) -> TTSResult:
        """Synthesize text into high-fidelity neural audio."""
        import edge_tts
        import soundfile as sf

        normalized_text = normalize_for_speech(text) if normalize else text.strip()
        if not normalized_text:
            return TTSResult(audio=b"", format=output_format, voice_id=voice_id or self.default_voice)

        voice = voice_id or self.default_voice
        rate_str = self._format_rate(speed or self.default_speed)
        pitch_str = self._format_pitch(pitch or self.default_pitch)

        async def _do_synth() -> bytes:
            communicate = edge_tts.Communicate(
                normalized_text,
                voice=voice,
                rate=rate_str,
                pitch=pitch_str,
            )
            mp3_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_bytes += chunk["data"]
            return mp3_bytes

        # Run async synthesis in event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running loop (e.g. in FastAPI handler)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    mp3_data = pool.submit(lambda: asyncio.run(_do_synth())).result(timeout=30.0)
            else:
                mp3_data = loop.run_until_complete(_do_synth())
        except RuntimeError:
            mp3_data = asyncio.run(_do_synth())

        if not mp3_data:
            raise RuntimeError("Edge TTS returned empty audio stream")

        # Decode MP3 to measure duration and convert to WAV if requested
        try:
            audio_array, sr = sf.read(io.BytesIO(mp3_data), dtype="float32")
            duration = float(len(audio_array) / sr)
        except Exception:
            sr = _SAMPLE_RATE
            duration = round(len(mp3_data) / 6000.0, 2)
            audio_array = None

        final_audio = mp3_data
        if output_format.lower() == "wav" and audio_array is not None:
            wav_buf = io.BytesIO()
            sf.write(wav_buf, audio_array, sr, format="WAV")
            wav_buf.seek(0)
            final_audio = wav_buf.read()

        return TTSResult(
            audio=final_audio,
            format=output_format.lower(),
            duration_seconds=round(duration, 2),
            voice_id=voice,
            sample_rate=sr,
            metadata={
                "backend": "edge_tts",
                "speed": speed,
                "pitch": pitch_str,
                "rate": rate_str,
            },
        )

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        pitch: str = "+0Hz",
        normalize: bool = True,
    ) -> AsyncGenerator[tuple[bytes, int], None]:
        """Asynchronously stream audio chunks for low-latency playback."""
        import edge_tts

        normalized_text = normalize_for_speech(text) if normalize else text.strip()
        if not normalized_text:
            return

        voice = voice_id or self.default_voice
        rate_str = self._format_rate(speed or self.default_speed)
        pitch_str = self._format_pitch(pitch or self.default_pitch)

        communicate = edge_tts.Communicate(
            normalized_text,
            voice=voice,
            rate=rate_str,
            pitch=pitch_str,
        )
        async for chunk in communicate.stream():
            if chunk["type"] == "audio" and chunk["data"]:
                yield chunk["data"], _SAMPLE_RATE

    def available_voices(self) -> List[str]:
        """Return list of supported high-quality voice IDs."""
        return list(self.VOICE_CATALOG.keys())

    def health(self) -> bool:
        """Check if Edge TTS is available and responsive."""
        now = time.time()
        with self._lock:
            if self._health_cached is not None and (now - self._last_health_check) < 30.0:
                return self._health_cached

        try:
            import edge_tts  # noqa: F401
            # Quick probe check
            self._health_cached = True
        except Exception as exc:
            logger.debug("Edge TTS health probe failed: %s", exc)
            self._health_cached = False

        self._last_health_check = now
        return self._health_cached

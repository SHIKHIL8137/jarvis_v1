"""Microphone recording with silence detection, audio device management, and self-echo protected playback."""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from typing import Any, Dict, List, Optional

from openjarvis.speech.echo_guard import EchoGuard, get_echo_guard

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_CHUNK = 1024
_SILENCE_THRESHOLD = 500  # RMS below this → silence
_SILENCE_SECONDS = 1.5  # seconds of silence before auto-stop
_STARTUP_SILENCE_SECONDS = 5.0  # give up early if speech never begins
_MAX_RECORD_SECONDS = 30  # safety ceiling


def _rms(data: bytes) -> float:
    """Compute RMS amplitude of 16-bit PCM bytes."""
    import struct

    n = len(data) // 2
    if n == 0:
        return 0.0
    shorts = struct.unpack(f"{n}h", data[: n * 2])
    return (sum(s * s for s in shorts) / n) ** 0.5


def list_audio_devices() -> Dict[str, List[Dict[str, Any]]]:
    """List available host input (microphone) and output (speaker) devices."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:
        logger.debug("Failed to query sound devices: %s", exc)
        return {"inputs": [], "outputs": []}

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    default_input = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else -1
    default_output = sd.default.device[1] if isinstance(sd.default.device, (list, tuple)) else -1

    for idx, d in enumerate(devices):
        api_name = hostapis[d["hostapi"]]["name"] if "hostapi" in d and d["hostapi"] < len(hostapis) else ""
        dev_info = {
            "id": idx,
            "name": d.get("name", f"Device {idx}"),
            "hostapi": api_name,
            "max_input_channels": d.get("max_input_channels", 0),
            "max_output_channels": d.get("max_output_channels", 0),
            "default_samplerate": d.get("default_samplerate", 16000),
            "is_default": (idx == default_input or idx == default_output),
        }
        if d.get("max_input_channels", 0) > 0:
            inputs.append(dev_info)
        if d.get("max_output_channels", 0) > 0:
            outputs.append(dev_info)

    return {"inputs": inputs, "outputs": outputs}


def record_until_silence(
    *,
    sample_rate: int = _SAMPLE_RATE,
    silence_threshold: int = _SILENCE_THRESHOLD,
    silence_seconds: float = _SILENCE_SECONDS,
    startup_silence_seconds: float = _STARTUP_SILENCE_SECONDS,
    max_seconds: float = _MAX_RECORD_SECONDS,
    device: Optional[int] = None,
    echo_guard: Optional[EchoGuard] = None,
    stop_event: Optional[threading.Event] = None,
) -> bytes:
    """Record from the microphone until silence is detected.

    Integrates with EchoGuard to reject self-echo from Bluetooth/speakers during
    or immediately following audio playback.

    Returns raw WAV bytes (16-bit mono).
    """
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError(
            "sounddevice is required for voice input. "
            "Install with: pip install sounddevice"
        )

    guard = echo_guard or get_echo_guard()
    chunks_per_second = sample_rate / _CHUNK
    silence_chunks = int(silence_seconds * chunks_per_second)
    startup_silence_chunks = max(1, int(startup_silence_seconds * chunks_per_second))
    max_chunks = int(max_seconds * chunks_per_second)

    frames: list[bytes] = []
    silence_count = 0
    has_speech = False

    with sd.RawInputStream(
        samplerate=sample_rate,
        channels=_CHANNELS,
        dtype="int16",
        blocksize=_CHUNK,
        device=device,
    ) as stream:
        for _ in range(max_chunks):
            if stop_event and stop_event.is_set():
                break

            raw, _ = stream.read(_CHUNK)
            data = bytes(raw)
            amplitude = _rms(data)

            # Check if this frame is self-echo from speaker playback
            if guard.should_suppress_frame(amplitude, silence_threshold):
                continue

            frames.append(data)

            if amplitude > silence_threshold:
                has_speech = True
                silence_count = 0
            elif not has_speech and len(frames) >= startup_silence_chunks:
                break
            elif has_speech:
                silence_count += 1
                if silence_count >= silence_chunks:
                    break

    return _frames_to_wav(frames, sample_rate)


def _frames_to_wav(frames: list[bytes], sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def play_wav(
    audio: bytes,
    sample_rate: int = 24000,
    *,
    device: Optional[int] = None,
    volume: float = 1.0,
    echo_guard: Optional[EchoGuard] = None,
    interrupt_event: Optional[threading.Event] = None,
    spoken_text: str = "",
) -> bool:
    """Play raw WAV bytes with self-echo tracking and barge-in interruption support.

    Returns True if played to completion, False if interrupted by user barge-in.
    """
    try:
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        raise RuntimeError(
            "sounddevice, numpy, and soundfile are required for voice output. "
            "Install with: pip install sounddevice numpy soundfile"
        )

    guard = echo_guard or get_echo_guard()
    buf = io.BytesIO(audio)
    try:
        data, sr = sf.read(buf, dtype="float32")
    except Exception:
        import struct

        n = len(audio) // 2
        data = (
            np.array(struct.unpack(f"{n}h", audio[: n * 2]), dtype="float32") / 32768.0
        )
        sr = sample_rate

    # Apply volume scaling
    if volume != 1.0 and volume > 0.0:
        data = data * min(2.0, max(0.0, volume))

    guard.start_playback(text=spoken_text)

    try:
        # Play in an interruptible OutputStream loop (chunked)
        blocksize = 2048
        total_samples = len(data)
        curr_idx = 0
        was_interrupted = False

        with sd.OutputStream(
            samplerate=sr,
            channels=1 if data.ndim == 1 else data.shape[1],
            dtype="float32",
            device=device,
        ) as stream:
            while curr_idx < total_samples:
                # Check for interruption
                if interrupt_event and interrupt_event.is_set():
                    was_interrupted = True
                    break
                if not guard.is_playing:
                    was_interrupted = True
                    break

                chunk_end = min(curr_idx + blocksize, total_samples)
                chunk = data[curr_idx:chunk_end]
                if data.ndim > 1:
                    stream.write(chunk)
                else:
                    stream.write(chunk.reshape(-1, 1))
                curr_idx = chunk_end

        if was_interrupted:
            logger.info("play_wav: playback was interrupted")
            return False
        return True
    finally:
        guard.stop_playback()


__all__ = [
    "list_audio_devices",
    "play_wav",
    "record_until_silence",
]

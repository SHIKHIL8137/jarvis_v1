"""Stateful Voice Session Manager for continuous listening, wake-word activation,
barge-in interruption, and instant voice acknowledgment."""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from openjarvis.speech.echo_guard import EchoGuard, get_echo_guard
from openjarvis.speech.language import LanguageManager, get_language_manager
from openjarvis.speech.voice_io import list_audio_devices, play_wav, record_until_silence

logger = logging.getLogger(__name__)


class VoiceState(str, Enum):
    """Lifecycle states of the voice assistant."""

    IDLE = "idle"
    WAKE_LISTENING = "wake_listening"
    ACTIVE_LISTENING = "active_listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


class VoiceSessionManager:
    """Manages the full voice assistant lifecycle:
    - Wake-word detection ("Hey Jarvis") via local openWakeWord
    - Push-to-talk and continuous listening
    - Speech-to-Text transcription via Faster-Whisper
    - Instant voice acknowledgment for long operations
    - Speech synthesis with interruptible playback and self-echo guard
    - Language consistency tracking
    """

    def __init__(
        self,
        wake_word_model: str = "hey_jarvis",
        wake_threshold: float = 0.5,
        inactivity_timeout_seconds: float = 20.0,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        echo_guard: Optional[EchoGuard] = None,
        language_manager: Optional[LanguageManager] = None,
        state_change_callback: Optional[Callable[[VoiceState], None]] = None,
    ) -> None:
        self.wake_word_model = wake_word_model
        self.wake_threshold = wake_threshold
        self.inactivity_timeout_seconds = inactivity_timeout_seconds
        self.input_device = input_device
        self.output_device = output_device
        self.echo_guard = echo_guard or get_echo_guard()
        self.language_manager = language_manager or get_language_manager()
        self._state_callback = state_change_callback

        self._state = VoiceState.IDLE
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._interrupt_event = threading.Event()
        self._wake_model: Any = None
        self._last_user_activity = 0.0

        # Wire interruption callback on EchoGuard
        self.echo_guard._interruption_callback = self._on_playback_interrupted

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    def _set_state(self, new_state: VoiceState) -> None:
        with self._lock:
            if self._state == new_state:
                return
            old_state = self._state
            self._state = new_state
            logger.info("VoiceSessionManager: %s -> %s", old_state.value, new_state.value)

        if self._state_callback:
            try:
                self._state_callback(new_state)
            except Exception as exc:
                logger.debug("Error in voice state callback: %s", exc)

    def _on_playback_interrupted(self) -> None:
        """Invoked when user interrupts Jarvis during playback."""
        self._interrupt_event.set()
        self._set_state(VoiceState.INTERRUPTED)

    def _ensure_wake_model(self) -> Any:
        if self._wake_model is None:
            from openwakeword.model import Model

            self._wake_model = Model(
                wakeword_models=[self.wake_word_model],
                inference_framework="onnx",
            )
        return self._wake_model

    def play_activation_chime(self) -> None:
        """Provide brief audio/system feedback on wake-word activation."""
        try:
            import winsound

            winsound.Beep(880, 120)
        except Exception:
            pass

    def start_wake_listener(
        self,
        on_activated: Optional[Callable[[], None]] = None,
    ) -> None:
        """Start local wake-word listener thread."""
        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._set_state(VoiceState.WAKE_LISTENING)
            self._worker_thread = threading.Thread(
                target=self._wake_listener_loop,
                args=(on_activated,),
                name="jarvis-wake-listener",
                daemon=True,
            )
            self._worker_thread.start()

    def _wake_listener_loop(self, on_activated: Optional[Callable[[], None]]) -> None:
        """Background thread monitoring microphone for 'hey jarvis'."""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            logger.warning("sounddevice or numpy not installed; wake listener cannot start")
            self._set_state(VoiceState.IDLE)
            return

        try:
            model = self._ensure_wake_model()
        except Exception as exc:
            logger.error("Failed to load openWakeWord model: %s", exc)
            self._set_state(VoiceState.IDLE)
            return

        logger.info("Wake listener running locally for '%s' (threshold %.2f)", self.wake_word_model, self.wake_threshold)

        chunk_size = 1280
        cooldown = 2.0
        last_activated = 0.0

        try:
            with sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="int16",
                blocksize=chunk_size,
                device=self.input_device,
            ) as stream:
                while not self._stop_event.is_set():
                    # If assistant is currently speaking or processing, wait
                    if self.state not in (VoiceState.WAKE_LISTENING, VoiceState.IDLE):
                        time.sleep(0.1)
                        continue

                    # If echo guard is active or in post-playback guard window, skip
                    if self.echo_guard.is_playing or self.echo_guard.is_in_guard_interval():
                        time.sleep(0.05)
                        continue

                    raw_chunk, overflow = stream.read(chunk_size)
                    if self._stop_event.is_set():
                        break

                    samples = np.squeeze(raw_chunk)
                    scores = model.predict(samples)
                    score = scores.get(self.wake_word_model, 0.0)

                    if score >= self.wake_threshold and (time.time() - last_activated > cooldown):
                        last_activated = time.time()
                        logger.info("Wake word detected! Score: %.3f", score)
                        self.play_activation_chime()
                        self._set_state(VoiceState.ACTIVE_LISTENING)
                        model.reset()
                        if on_activated:
                            try:
                                on_activated()
                            except Exception as cb_err:
                                logger.error("Error in on_activated callback: %s", cb_err)
        except Exception as exc:
            logger.error("Wake listener loop terminated with error: %s", exc)
        finally:
            if not self._stop_event.is_set():
                self._set_state(VoiceState.IDLE)

    def stop_listening(self) -> None:
        """Stop wake listener or any active voice recording."""
        self._stop_event.set()
        self.echo_guard.interrupt()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        self._set_state(VoiceState.IDLE)
        logger.info("VoiceSessionManager: listening stopped")

    def record_user_speech(
        self,
        silence_seconds: float = 1.5,
        max_seconds: float = 30.0,
    ) -> bytes:
        """Record audio until user stops speaking."""
        self._set_state(VoiceState.ACTIVE_LISTENING)
        self._last_user_activity = time.time()
        try:
            audio = record_until_silence(
                silence_seconds=silence_seconds,
                max_seconds=max_seconds,
                device=self.input_device,
                echo_guard=self.echo_guard,
                stop_event=self._stop_event,
            )
            return audio
        finally:
            if self.state == VoiceState.ACTIVE_LISTENING:
                self._set_state(VoiceState.PROCESSING)

    def generate_instant_ack(self, user_prompt: str) -> Optional[str]:
        """Generate immediate natural verbal acknowledgment for long/complex operations.

        Example:
        User: "Jarvis, research the latest developments in local AI models..."
        Returns: "Sure. I'll research the options and compare them for you."
        """
        if not user_prompt:
            return None

        lower = user_prompt.lower()

        # Research requests
        if any(w in lower for w in ("research", "investigate", "look up", "find papers", "deep dive")):
            return "Sure. I'll research the options and compile the details for you."

        # Analysis & comparison
        if any(w in lower for w in ("compare", "analyze", "benchmark", "evaluate")):
            return "Understood. I'm analyzing the options and comparing them now."

        # Plan & execution
        if any(w in lower for w in ("plan", "create a plan", "execute", "build an architecture")):
            return "I'm on it. Breaking down the steps and preparing the execution plan."

        # Codebase inspection & development
        if any(w in lower for w in ("inspect codebase", "check the repository", "run audit")):
            return "Right away. Inspecting the project files now."

        return None

    def speak(
        self,
        text: str,
        tts_backend: Any = None,
        voice_id: Optional[str] = None,
        voice_speed: float = 1.0,
        volume: float = 1.0,
    ) -> bool:
        """Synthesize and play audio with barge-in support and self-echo protection.

        Returns True if played to completion, False if interrupted by user barge-in.
        """
        if not text:
            return True

        self._set_state(VoiceState.SPEAKING)
        self._interrupt_event.clear()

        # Detect language and adapt voice
        detected_lang = self.language_manager.detect_language(text)
        effective_voice = voice_id or self.language_manager.get_tts_voice(detected_lang)

        # Generate audio via TTS backend
        try:
            if tts_backend is None:
                from openjarvis.speech._tts_discovery import get_tts_backend

                tts_backend = get_tts_backend()

            if tts_backend is None:
                logger.warning("No TTS backend available to speak")
                self._set_state(VoiceState.IDLE)
                return False

            tts_result = tts_backend.synthesize(
                text=text,
                voice_id=effective_voice,
                speed=voice_speed,
            )
            audio_bytes = tts_result.audio_bytes
        except Exception as exc:
            logger.error("TTS synthesis error: %s", exc)
            self._set_state(VoiceState.IDLE)
            return False

        # Play audio with interruptibility
        try:
            success = play_wav(
                audio_bytes,
                device=self.output_device,
                volume=volume,
                echo_guard=self.echo_guard,
                interrupt_event=self._interrupt_event,
                spoken_text=text,
            )
            return success
        finally:
            if self.state in (VoiceState.SPEAKING, VoiceState.INTERRUPTED):
                self._set_state(VoiceState.IDLE)


_GLOBAL_VOICE_SESSION_MANAGER: Optional[VoiceSessionManager] = None


def get_voice_session_manager() -> VoiceSessionManager:
    global _GLOBAL_VOICE_SESSION_MANAGER
    if _GLOBAL_VOICE_SESSION_MANAGER is None:
        _GLOBAL_VOICE_SESSION_MANAGER = VoiceSessionManager()
    return _GLOBAL_VOICE_SESSION_MANAGER

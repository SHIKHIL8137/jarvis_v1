"""Self-echo protection, playback tracking, and barge-in voice interruption."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Stop / interruption keywords that allow user barge-in while Jarvis is speaking
INTERRUPT_KEYWORDS = frozenset(
    {
        "stop",
        "wait",
        "hold on",
        "cancel",
        "pause",
        "shut up",
        "hey jarvis",
        "quiet",
        "silence",
    }
)


class EchoGuard:
    """Detects playback state, prevents self-echo feedback loops, and enables
    barge-in interruption.

    When Jarvis is speaking through speakers (laptop or Bluetooth), microphone input
    is heavily contaminated by Jarvis's own voice. EchoGuard tracks playback state,
    maintains a post-playback guard interval, and discriminates between speaker echo
    and intentional user barge-in commands.
    """

    def __init__(
        self,
        guard_interval_seconds: float = 0.5,
        interruption_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self._guard_interval = guard_interval_seconds
        self._interruption_callback = interruption_callback
        self._is_playing = False
        self._last_playback_ended = 0.0
        self._playback_started = 0.0
        self._lock = threading.Lock()
        self._playback_tokens: list[str] = []

    @property
    def is_playing(self) -> bool:
        """Whether Jarvis is currently playing synthesized audio."""
        with self._lock:
            return self._is_playing

    @property
    def guard_interval(self) -> float:
        return self._guard_interval

    @guard_interval.setter
    def guard_interval(self, value: float) -> None:
        self._guard_interval = max(0.0, float(value))

    def start_playback(self, text: str = "") -> None:
        """Signal that audio playback has started."""
        with self._lock:
            self._is_playing = True
            self._playback_started = time.time()
            if text:
                # Tokenize spoken words for echo comparison
                self._playback_tokens = [w.lower().strip(",.!?") for w in text.split() if w]
            else:
                self._playback_tokens = []
        logger.debug("EchoGuard: playback started")

    def stop_playback(self) -> None:
        """Signal that audio playback has ended normally."""
        with self._lock:
            self._is_playing = False
            self._last_playback_ended = time.time()
        logger.debug("EchoGuard: playback stopped (guard interval active for %.2fs)", self._guard_interval)

    def is_in_guard_interval(self) -> bool:
        """Whether we are within the post-playback guard window (absorbing reverb)."""
        with self._lock:
            if self._is_playing:
                return True
            elapsed = time.time() - self._last_playback_ended
            return elapsed < self._guard_interval

    def interrupt(self) -> bool:
        """Immediately abort audio playback when user barges in."""
        with self._lock:
            if not self._is_playing:
                return False
            self._is_playing = False
            self._last_playback_ended = time.time()

        logger.info("EchoGuard: user interrupted audio playback")
        if self._interruption_callback:
            try:
                self._interruption_callback()
            except Exception as exc:
                logger.debug("Error in interruption callback: %s", exc)

        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass

        return True

    def check_barge_in(self, transcribed_text: str) -> bool:
        """Check if incoming transcription is an intentional barge-in interruption.

        Returns True and triggers interrupt() if the user uttered a stop word.
        Returns False if the text appears to be self-echo of Jarvis's own speech.
        """
        if not transcribed_text:
            return False

        lower = transcribed_text.lower().strip()

        # Check for explicit interruption phrases
        for keyword in INTERRUPT_KEYWORDS:
            if keyword in lower or lower == keyword:
                self.interrupt()
                return True

        # Check if the text matches Jarvis's own spoken text (self-echo)
        with self._lock:
            tokens = self._playback_tokens

        if tokens:
            incoming_words = [w.lower().strip(",.!?") for w in lower.split() if w]
            matching = sum(1 for w in incoming_words if w in tokens)
            if incoming_words and (matching / len(incoming_words)) > 0.5:
                # High overlap with what Jarvis was speaking -> self-echo
                logger.debug("EchoGuard: filtered out self-echo: %r", transcribed_text)
                return False

        return False

    def should_suppress_frame(self, rms_amplitude: float, silence_threshold: float = 500.0) -> bool:
        """Determine if a raw audio frame should be ignored during recording.

        Suppresses frames while playing unless amplitude indicates strong local speech,
        and suppresses all frames during the post-playback guard interval.
        """
        with self._lock:
            if not self._is_playing:
                # Not playing: check post-playback guard
                if time.time() - self._last_playback_ended < self._guard_interval:
                    return True
                return False

            # Playing: speaker output is active.
            # Only allow frames through if energy is significantly higher than silence threshold
            # (potential barge-in attempt)
            return rms_amplitude < (silence_threshold * 2.5)


_GLOBAL_ECHO_GUARD = EchoGuard()


def get_echo_guard() -> EchoGuard:
    """Return the global EchoGuard singleton."""
    return _GLOBAL_ECHO_GUARD

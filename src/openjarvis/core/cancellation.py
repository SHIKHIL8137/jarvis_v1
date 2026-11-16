"""Thread-safe cancellation token for cooperative task and agent cancellation."""

from __future__ import annotations

import threading


class CancellationToken:
    """Thread-safe cancellation token with optional reason message."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str = ""

    def cancel(self, reason: str = "") -> None:
        """Signal cancellation."""
        self._reason = reason
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """True if cancellation was requested."""
        return self._event.is_set()

    def is_set(self) -> bool:
        """Threading.Event compatibility."""
        return self._event.is_set()

    @property
    def reason(self) -> str:
        """Reason provided for cancellation."""
        return self._reason

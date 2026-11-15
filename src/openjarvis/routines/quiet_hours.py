"""Quiet hours manager — prevents unsolicited voice announcements during night hours."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class QuietHoursManager:
    """Enforces quiet hours policy to protect user sleep and privacy.

    During quiet hours:
    - Unsolicited voice output is suppressed or queued.
    - Scheduled reminders execute silently or record text notifications.
    - Default timezone: Asia/Kolkata.
    """

    def __init__(
        self,
        enabled: bool = True,
        start_time_str: str = "22:00",
        end_time_str: str = "07:00",
        timezone_name: str = "Asia/Kolkata",
    ) -> None:
        self.enabled = enabled
        self.start_time_str = start_time_str
        self.end_time_str = end_time_str
        self.timezone_name = timezone_name
        self._queued_notifications: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def parse_time(self, s: str) -> dt_time:
        parts = s.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return dt_time(hour=hour, minute=minute)

    def is_in_quiet_hours(self, check_timestamp: Optional[float] = None) -> bool:
        """Return True if check_timestamp (or current time) falls in the quiet hours window."""
        if not self.enabled:
            return False

        try:
            tz = ZoneInfo(self.timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")

        ts = check_timestamp if check_timestamp is not None else time.time()
        now_dt = datetime.fromtimestamp(ts, tz=tz)
        current_t = now_dt.time()

        start_t = self.parse_time(self.start_time_str)
        end_t = self.parse_time(self.end_time_str)

        # Overnight window (e.g. 22:00 to 07:00)
        if start_t > end_t:
            return current_t >= start_t or current_t < end_t
        else:
            # Daytime window
            return start_t <= current_t < end_t

    def should_suppress_voice(self, is_voice_announcement: bool, check_timestamp: Optional[float] = None) -> bool:
        """Return True if an unsolicited voice announcement should be suppressed."""
        if not is_voice_announcement:
            return False
        return self.is_in_quiet_hours(check_timestamp)

    def queue_notification(self, notification: Dict[str, Any]) -> None:
        """Queue a suppressed voice notification until quiet hours end."""
        with self._lock:
            self._queued_notifications.append(
                {
                    **notification,
                    "queued_at": time.time(),
                }
            )
            logger.info("QuietHours: queued voice notification (queue size: %d)", len(self._queued_notifications))

    def flush_queued(self) -> List[Dict[str, Any]]:
        """Return and clear queued notifications."""
        with self._lock:
            items = list(self._queued_notifications)
            self._queued_notifications.clear()
            return items

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "start_time": self.start_time_str,
            "end_time": self.end_time_str,
            "timezone": self.timezone_name,
            "is_currently_active": self.is_in_quiet_hours(),
            "queued_count": len(self._queued_notifications),
        }

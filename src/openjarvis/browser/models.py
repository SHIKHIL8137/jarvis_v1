"""Data models and state representations for desktop browser integration."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BrowserState(str, Enum):
    """Lifecycle states for the desktop browser manager."""

    DISCONNECTED = "DISCONNECTED"
    DETECTING = "DETECTING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    READY = "READY"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


@dataclass
class BrowserTabInfo:
    """Represents a single open browser tab."""

    tab_id: str | int
    window_id: str | int = 0
    url: str = ""
    title: str = ""
    domain: str = ""
    is_active: bool = False
    fav_icon_url: str = ""
    last_accessed: float = field(default_factory=time.time)
    task_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserWindowInfo:
    """Represents a browser window containing one or more tabs."""

    window_id: str | int
    is_focused: bool = False
    tabs: List[BrowserTabInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "is_focused": self.is_focused,
            "tabs": [t.to_dict() for t in self.tabs],
        }


@dataclass
class TabMatchResult:
    """Result of matching a requested destination against open tabs."""

    matched_tab: Optional[BrowserTabInfo] = None
    score: float = 0.0
    reason: str = ""
    can_reuse: bool = False
    should_navigate: bool = False
    requires_user_confirmation: bool = False

    @property
    def confidence(self) -> float:
        return self.score


@dataclass
class OpenUrlDecision:
    """Decision made by the intelligent tab reuse pipeline."""

    action: str  # "activate_tab", "navigate_tab", "create_tab", "launch_browser", "ask_user"
    target_url: str
    target_tab: Optional[BrowserTabInfo] = None
    reason: str = ""
    window_id: Optional[str | int] = None


@dataclass
class BrowserActionResult:
    """Result of executing a browser operation."""

    success: bool
    action: str
    message: str
    reused_existing: bool = False
    tab: Optional[BrowserTabInfo] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "success": self.success,
            "action": self.action,
            "message": self.message,
            "reused_existing": self.reused_existing,
            "metadata": self.metadata,
        }
        if self.tab:
            d["tab"] = self.tab.to_dict()
        return d

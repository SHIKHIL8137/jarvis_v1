"""Desktop browser integration package for Jarvis V1."""

from openjarvis.browser.manager import (
    DesktopBrowserManager,
    get_browser_manager,
)
from openjarvis.browser.matcher import TabMatcher
from openjarvis.browser.models import (
    BrowserActionResult,
    BrowserState,
    BrowserTabInfo,
    BrowserWindowInfo,
    OpenUrlDecision,
    TabMatchResult,
)
from openjarvis.browser.windows_detector import WindowsBrowserDetector

__all__ = [
    "BrowserActionResult",
    "BrowserState",
    "BrowserTabInfo",
    "BrowserWindowInfo",
    "DesktopBrowserManager",
    "OpenUrlDecision",
    "TabMatchResult",
    "TabMatcher",
    "WindowsBrowserDetector",
    "get_browser_manager",
]

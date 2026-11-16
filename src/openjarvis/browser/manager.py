"""Centralized Desktop Browser Manager for persistent browser control and intelligent tab reuse."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from openjarvis.browser.cdp_bridge import CDPBridge
from openjarvis.browser.extension_bridge import ExtensionBridge
from openjarvis.browser.matcher import SERVICE_DOMAIN_MAP, TabMatcher
from openjarvis.browser.models import (
    BrowserActionResult,
    BrowserState,
    BrowserTabInfo,
    BrowserWindowInfo,
    OpenUrlDecision,
    TabMatchResult,
)
from openjarvis.browser.windows_detector import WindowsBrowserDetector

logger = logging.getLogger(__name__)


class DesktopBrowserManager:
    """Singleton service providing persistent control, tab inspection, and intelligent reuse for desktop browsers."""

    def __init__(self) -> None:
        self.state = BrowserState.DISCONNECTED
        self.extension_bridge = ExtensionBridge()
        self.cdp_bridge = CDPBridge()
        self.detector = WindowsBrowserDetector()
        self._last_state_change = time.time()
        self._task_registry: Dict[str, str] = {}  # tab_id -> task_id

        # Listen to extension state changes
        self.extension_bridge.register_state_callback(self._on_extension_state_change)

        # Initial check
        self.refresh_status()

    def _set_state(self, new_state: BrowserState) -> None:
        if self.state != new_state:
            logger.info("BrowserManager state transition: %s -> %s", self.state, new_state)
            self.state = new_state
            self._last_state_change = time.time()

    def _on_extension_state_change(self, connected: bool) -> None:
        if connected:
            self._set_state(BrowserState.READY)
        else:
            self.refresh_status()

    def refresh_status(self) -> BrowserState:
        """Probe browser process and bridge statuses to update manager state."""
        if self.extension_bridge.is_connected:
            self._set_state(BrowserState.READY)
            return self.state

        if self.cdp_bridge.is_available():
            self._set_state(BrowserState.READY)
            return self.state

        is_running = self.detector.is_browser_running()
        if is_running:
            self._set_state(BrowserState.CONNECTED)
        else:
            self._set_state(BrowserState.DISCONNECTED)

        return self.state

    def resolve_destination_url(self, query_or_url: str) -> str:
        """Resolve a user prompt, service name, or URL into a fully-qualified destination URL."""
        if not query_or_url:
            return ""

        raw = query_or_url.strip()
        lower_raw = raw.lower()

        # Check known service names (e.g. "linkedin" -> "https://www.linkedin.com")
        if lower_raw in SERVICE_DOMAIN_MAP:
            return f"https://www.{SERVICE_DOMAIN_MAP[lower_raw]}"

        # If already a valid http(s) URL
        if raw.startswith(("http://", "https://", "file://")):
            return raw

        # If looks like a domain (e.g. "linkedin.com/jobs" or "github.com")
        if "." in raw and " " not in raw:
            return f"https://{raw}"

        # Otherwise treat as Google search query
        return f"https://www.google.com/search?q={quote_plus(raw)}"

    def list_tabs(self) -> List[BrowserTabInfo]:
        """Retrieve accessible open browser tabs across all windows."""
        # 1. Extension bridge (highest fidelity)
        if self.extension_bridge.is_connected:
            return self.extension_bridge.get_tabs()

        # 2. CDP bridge (if running with remote debugging)
        if self.cdp_bridge.is_available():
            return self.cdp_bridge.list_tabs()

        # 3. Windows window detector fallback (infers tabs from window titles)
        windows = self.detector.get_browser_windows()
        inferred_tabs: List[BrowserTabInfo] = []
        for idx, win in enumerate(windows):
            title = win.get("title", "")
            # Filter standard chrome/edge suffixes to extract page title
            clean_title = (
                title.replace(" - Google Chrome", "")
                .replace(" - Microsoft Edge", "")
                .replace(" - Brave", "")
                .strip()
            )
            inferred_tabs.append(
                BrowserTabInfo(
                    tab_id=f"win_{win['hwnd']}",
                    window_id=win["hwnd"],
                    title=clean_title or title,
                    url="",  # Cannot read full URL without extension or CDP
                    is_active=True,
                )
            )

        return inferred_tabs

    def get_active_tab(self) -> Optional[BrowserTabInfo]:
        """Return the tab currently in focus."""
        if self.extension_bridge.is_connected:
            return self.extension_bridge.get_active_tab()
        tabs = self.list_tabs()
        return tabs[0] if tabs else None

    def plan_open_url_decision(
        self,
        destination_url: str,
        explicit_new_tab: bool = False,
        task_context: str = "",
    ) -> OpenUrlDecision:
        """Execute the 6-step decision logic to plan tab reuse vs creation."""
        # STEP 1: Check whether a supported browser is running
        browser_running = self.detector.is_browser_running() or self.extension_bridge.is_connected

        # If user explicitly asked for a new tab or window, honor that intent
        is_explicit = explicit_new_tab or TabMatcher.is_explicit_new_tab_request(task_context)

        # STEP 6: If no browser is running
        if not browser_running:
            return OpenUrlDecision(
                action="launch_browser",
                target_url=destination_url,
                reason="No supported browser is currently running. Launching browser.",
            )

        # STEP 2: Retrieve all accessible browser windows and tabs
        tabs = self.list_tabs()

        # If browser process is running but has zero reported tabs
        if not tabs and not is_explicit:
            return OpenUrlDecision(
                action="launch_browser",
                target_url=destination_url,
                reason="Browser is running but no open tabs accessible. Opening URL in browser.",
            )

        # If user explicitly requested another tab, skip reuse
        if is_explicit:
            return OpenUrlDecision(
                action="create_tab",
                target_url=destination_url,
                reason="User explicitly requested a separate new tab or window.",
            )

        # STEP 3: Compare requested destination against existing tabs
        match_result: TabMatchResult = TabMatcher.find_best_tab(
            destination_url, tabs, task_context=task_context
        )

        # STEP 4: If an appropriate tab already exists
        if match_result.can_reuse and match_result.matched_tab:
            # Check if tab navigation would destroy sensitive active work (forms/compose)
            if match_result.requires_user_confirmation:
                return OpenUrlDecision(
                    action="ask_user",
                    target_url=destination_url,
                    target_tab=match_result.matched_tab,
                    reason=(
                        f"Existing {match_result.matched_tab.title} tab has active unsaved work. "
                        "Confirm before navigating or open in new tab."
                    ),
                )

            if match_result.should_navigate:
                return OpenUrlDecision(
                    action="navigate_tab",
                    target_url=destination_url,
                    target_tab=match_result.matched_tab,
                    window_id=match_result.matched_tab.window_id,
                    reason=f"Reusing existing {match_result.matched_tab.domain or match_result.matched_tab.title} tab and navigating to {destination_url} ({match_result.reason})",
                )

            return OpenUrlDecision(
                action="activate_tab",
                target_url=destination_url,
                target_tab=match_result.matched_tab,
                window_id=match_result.matched_tab.window_id,
                reason=f"Activating existing tab for {destination_url} without creating duplicate ({match_result.reason})",
            )

        # STEP 5: Destination is not open -> reuse existing browser window and open single tab
        active_tab = self.get_active_tab()
        target_win = active_tab.window_id if active_tab else 0
        return OpenUrlDecision(
            action="create_tab",
            target_url=destination_url,
            window_id=target_win,
            reason=f"Destination {destination_url} is not open in any existing tab. Opening single new tab.",
        )

    def execute_decision(self, decision: OpenUrlDecision) -> BrowserActionResult:
        """Execute the planned tab decision across the active browser bridge."""
        action = decision.action
        target_url = decision.target_url

        # Case 1: Activate existing tab
        if action == "activate_tab" and decision.target_tab:
            tab = decision.target_tab
            success = self.activate_tab(tab.tab_id, window_id=tab.window_id, title_hint=tab.title)
            return BrowserActionResult(
                success=success,
                action="activate_tab",
                message=f"Activated existing tab: '{tab.title or tab.url}' (tab ID {tab.tab_id}). No duplicate tab created.",
                reused_existing=True,
                tab=tab,
                metadata={"tab_id": tab.tab_id, "window_id": tab.window_id, "url": tab.url},
            )

        # Case 2: Navigate existing tab
        if action == "navigate_tab" and decision.target_tab:
            tab = decision.target_tab
            success = self.navigate_tab(tab.tab_id, target_url, window_id=tab.window_id)
            tab.url = target_url
            return BrowserActionResult(
                success=success,
                action="navigate_tab",
                message=f"Navigated existing tab '{tab.title}' to {target_url}. Reused tab {tab.tab_id}.",
                reused_existing=True,
                tab=tab,
                metadata={"tab_id": tab.tab_id, "window_id": tab.window_id, "url": target_url},
            )

        # Case 3: Create one new tab in existing browser
        if action == "create_tab":
            created_tab = self.create_tab(target_url, window_id=decision.window_id or 0)
            return BrowserActionResult(
                success=created_tab is not None,
                action="create_tab",
                message=f"Opened {target_url} in a new tab.",
                reused_existing=False,
                tab=created_tab,
                metadata={"url": target_url},
            )

        # Case 4: Launch browser process
        if action == "launch_browser":
            launched = self.detector.launch_browser(target_url)
            self._set_state(BrowserState.CONNECTED)
            return BrowserActionResult(
                success=launched,
                action="launch_browser",
                message=f"Launched browser with {target_url}.",
                reused_existing=False,
                metadata={"url": target_url},
            )

        # Case 5: Ask user / Sensitive confirmation
        if action == "ask_user":
            return BrowserActionResult(
                success=False,
                action="ask_user",
                message=decision.reason,
                reused_existing=True,
                tab=decision.target_tab,
            )

        # Fallback
        return BrowserActionResult(
            success=False,
            action="unknown",
            message=f"Unrecognized browser decision: {action}",
        )

    def open_or_reuse(
        self,
        query_or_url: str,
        explicit_new_tab: bool = False,
        task_context: str = "",
    ) -> BrowserActionResult:
        """High-level entry point: resolve URL, apply 6-step decision pipeline, and execute."""
        dest_url = self.resolve_destination_url(query_or_url)
        if not dest_url:
            return BrowserActionResult(
                success=False,
                action="error",
                message="No URL or search query provided.",
            )

        decision = self.plan_open_url_decision(
            dest_url,
            explicit_new_tab=explicit_new_tab,
            task_context=task_context,
        )
        return self.execute_decision(decision)

    def activate_tab(
        self,
        tab_id: str | int,
        window_id: str | int = 0,
        title_hint: str = "",
    ) -> bool:
        """Activate a specific tab and bring its window to the foreground."""
        success = False

        # 1. Try extension bridge
        if self.extension_bridge.is_connected:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule asynchronous activation
                    asyncio.create_task(
                        self.extension_bridge.send_command(
                            "activate_tab",
                            {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id, "window_id": window_id},
                        )
                    )
                    success = True
                else:
                    loop.run_until_complete(
                        self.extension_bridge.send_command(
                            "activate_tab",
                            {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id, "window_id": window_id},
                        )
                    )
                    success = True
            except Exception as exc:
                logger.debug("Extension tab activation error: %s", exc)

        # 2. Try CDP bridge
        if not success and self.cdp_bridge.is_available():
            success = self.cdp_bridge.activate_tab(str(tab_id))

        # 3. Bring window to foreground on Windows
        if str(tab_id).startswith("win_"):
            hwnd = int(str(tab_id).replace("win_", ""))
            self.detector.bring_window_to_foreground(hwnd)
            success = True
        elif title_hint:
            self.detector.bring_browser_window_matching_title_to_foreground(title_hint)

        return success

    def navigate_tab(
        self,
        tab_id: str | int,
        url: str,
        window_id: str | int = 0,
    ) -> bool:
        """Navigate an existing tab to a new URL."""
        if self.extension_bridge.is_connected:
            try:
                loop = asyncio.get_event_loop()
                cmd_coro = self.extension_bridge.send_command(
                    "navigate_tab",
                    {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id, "url": url},
                )
                if loop.is_running():
                    asyncio.create_task(cmd_coro)
                    return True
                else:
                    loop.run_until_complete(cmd_coro)
                    return True
            except Exception as exc:
                logger.debug("Extension tab navigation error: %s", exc)

        # Fallback to activating and launching url
        self.activate_tab(tab_id, window_id=window_id)
        return self.detector.launch_browser(url)

    def create_tab(
        self,
        url: str,
        window_id: str | int = 0,
    ) -> Optional[BrowserTabInfo]:
        """Create a single new tab in the running browser."""
        # 1. Extension bridge
        if self.extension_bridge.is_connected:
            try:
                loop = asyncio.get_event_loop()
                cmd_coro = self.extension_bridge.send_command(
                    "create_tab",
                    {"url": url, "window_id": window_id, "active": True},
                )
                if loop.is_running():
                    asyncio.create_task(cmd_coro)
                else:
                    loop.run_until_complete(cmd_coro)
                return BrowserTabInfo(tab_id="new_tab", url=url, is_active=True)
            except Exception as exc:
                logger.debug("Extension create tab error: %s", exc)

        # 2. CDP bridge
        if self.cdp_bridge.is_available():
            return self.cdp_bridge.create_tab(url)

        # 3. Windows launch fallback
        self.detector.launch_browser(url)
        return BrowserTabInfo(tab_id="launched", url=url, is_active=True)

    def close_tab(self, tab_id: str | int) -> bool:
        """Close an open browser tab."""
        if self.extension_bridge.is_connected:
            try:
                loop = asyncio.get_event_loop()
                cmd_coro = self.extension_bridge.send_command(
                    "close_tab",
                    {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id},
                )
                if loop.is_running():
                    asyncio.create_task(cmd_coro)
                    return True
                else:
                    loop.run_until_complete(cmd_coro)
                    return True
            except Exception as exc:
                logger.debug("Extension close tab error: %s", exc)

        if self.cdp_bridge.is_available():
            return self.cdp_bridge.close_tab(str(tab_id))

        return False

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic and operational status of the browser manager."""
        self.refresh_status()
        tabs = self.list_tabs()
        active = self.get_active_tab()
        running_browsers = self.detector.get_running_browsers()

        mode = "unconnected"
        if self.extension_bridge.is_connected:
            mode = "extension"
        elif self.cdp_bridge.is_available():
            mode = "cdp"
        elif running_browsers:
            mode = "windows_gui"

        return {
            "state": self.state.value,
            "mode": mode,
            "browser_running": len(running_browsers) > 0,
            "running_browsers": running_browsers,
            "tab_count": len(tabs),
            "active_tab": active.to_dict() if active else None,
            "extension_connected": self.extension_bridge.is_connected,
            "pairing_token": self.extension_bridge.token,
            "installed_browsers": self.detector.get_installed_browsers(),
        }


# Global singleton instance
_GLOBAL_BROWSER_MANAGER: Optional[DesktopBrowserManager] = None


def get_browser_manager() -> DesktopBrowserManager:
    """Return the global DesktopBrowserManager singleton."""
    global _GLOBAL_BROWSER_MANAGER
    if _GLOBAL_BROWSER_MANAGER is None:
        _GLOBAL_BROWSER_MANAGER = DesktopBrowserManager()
    return _GLOBAL_BROWSER_MANAGER

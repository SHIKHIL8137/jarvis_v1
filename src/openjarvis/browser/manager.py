"""Centralized Desktop Browser Manager for persistent browser control and intelligent tab reuse."""

from __future__ import annotations

import asyncio
import logging
import threading
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

    def __init__(
        self,
        cdp_bridge: Optional[CDPBridge] = None,
        extension_bridge: Optional[ExtensionBridge] = None,
        detector: Optional[WindowsBrowserDetector] = None,
    ) -> None:
        self.state = BrowserState.DISCONNECTED
        self.extension_bridge = extension_bridge or ExtensionBridge()
        self.cdp_bridge = cdp_bridge or CDPBridge()
        self.detector = detector or WindowsBrowserDetector()
        self._last_state_change = time.time()
        self._destination_locks: Dict[str, asyncio.Lock] = {}
        self._lock_guard = threading.Lock()
        self._playwright_context: Optional[Any] = None
        self._active_playwright_tab_idx: Optional[int] = None

        # Listen to extension state changes
        self.extension_bridge.register_state_callback(self._on_extension_state_change)

        # Initial check
        self.refresh_status()

    def _get_destination_lock(self, dest_key: str) -> asyncio.Lock:
        """Thread-safe acquisition of a destination-specific asyncio lock to prevent race conditions."""
        with self._lock_guard:
            if dest_key not in self._destination_locks:
                self._destination_locks[dest_key] = asyncio.Lock()
            return self._destination_locks[dest_key]

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

    def _instrument_playwright_context(self, context: Any) -> None:
        """Instrument Playwright context and pages to track active tab transitions accurately."""
        pages = getattr(context, "pages", [])
        for idx, page in enumerate(pages):
            self._instrument_playwright_page(page, idx)

        if hasattr(context, "on"):
            def on_new_page(new_page):
                idx = len(getattr(context, "pages", [])) - 1
                self._active_playwright_tab_idx = max(0, idx)
                self._instrument_playwright_page(new_page, idx)
            try:
                context.on("page", on_new_page)
            except Exception:
                pass

    def _instrument_playwright_page(self, page: Any, idx: int) -> None:
        if getattr(page, "_jarvis_instrumented", False):
            return
        orig_bring_to_front = getattr(page, "bring_to_front", None)
        if orig_bring_to_front:
            def hooked_bring_to_front(*args, **kwargs):
                pages = getattr(self._playwright_context, "pages", [])
                try:
                    curr_idx = pages.index(page)
                except Exception:
                    curr_idx = idx
                self._active_playwright_tab_idx = curr_idx
                return orig_bring_to_front(*args, **kwargs)
            try:
                page.bring_to_front = hooked_bring_to_front
                page._jarvis_instrumented = True
            except Exception:
                pass

    def register_playwright_context(self, context: Any) -> None:
        """Register an active Playwright browser context for direct tab inspection and automation."""
        self._playwright_context = context
        self._active_playwright_tab_idx = None
        if context is not None:
            self._set_state(BrowserState.READY)
            self._instrument_playwright_context(context)

    def unregister_playwright_context(self) -> None:
        """Clear the registered Playwright context."""
        self._playwright_context = None
        self._active_playwright_tab_idx = None
        self.refresh_status()

    def refresh_status(self) -> BrowserState:
        """Probe browser process and bridge statuses to update manager state."""
        if self._playwright_context is not None:
            self._set_state(BrowserState.READY)
            return self.state

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
        # 0. Playwright context bridge (direct programmatic control)
        if self._playwright_context is not None:
            try:
                pw_tabs: List[BrowserTabInfo] = []
                pages = getattr(self._playwright_context, "pages", [])
                active_idx = self._active_playwright_tab_idx
                if active_idx is None or active_idx >= len(pages):
                    active_idx = len(pages) - 1 if pages else 0
                for idx, page in enumerate(pages):
                    page_url = getattr(page, "url", "")
                    title = ""
                    try:
                        title = page.title()
                    except Exception:
                        pass
                    pw_tabs.append(
                        BrowserTabInfo(
                            tab_id=f"pw_{idx}",
                            window_id=1,
                            url=page_url,
                            title=title or page_url,
                            domain=TabMatcher.extract_domain(page_url),
                            is_active=(idx == active_idx),
                        )
                    )
                if pw_tabs:
                    return pw_tabs
            except Exception as e:
                logger.debug("Playwright context tab enumeration error: %s", e)

        # 1. Extension bridge (highest fidelity)
        if self.extension_bridge.is_connected:
            return self.extension_bridge.get_tabs()

        # 2. CDP bridge (if running with remote debugging)
        if self.cdp_bridge.is_available():
            return self.cdp_bridge.list_tabs()

        # 3. Windows window detector fallback (infers tabs from window titles only)
        windows = self.detector.get_browser_windows()
        inferred_tabs: List[BrowserTabInfo] = []
        for idx, win in enumerate(windows):
            title = win.get("title", "")
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
        if self._playwright_context is not None:
            tabs = self.list_tabs()
            for t in tabs:
                if t.is_active:
                    return t
            return tabs[0] if tabs else None
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
        browser_running = (
            self._playwright_context is not None
            or self.extension_bridge.is_connected
            or self.cdp_bridge.is_available()
            or self.detector.is_browser_running()
        )

        # If user explicitly asked for a new tab or window, honor that intent
        is_explicit = explicit_new_tab or TabMatcher.is_explicit_new_tab_request(task_context)

        # STEP 6: If no browser is running
        if not browser_running:
            return OpenUrlDecision(
                action="launch_browser",
                target_url=destination_url,
                reason="No supported browser is currently running. Launching browser.",
            )

        # If user explicitly requested another tab, skip reuse
        if is_explicit:
            return OpenUrlDecision(
                action="create_tab",
                target_url=destination_url,
                reason="User explicitly requested a separate new tab or window.",
            )

        # STEP 2: Retrieve all accessible browser windows and tabs
        tabs = self.list_tabs()

        # If browser is running but has zero reported tabs
        if not tabs:
            return OpenUrlDecision(
                action="create_tab",
                target_url=destination_url,
                reason="Browser is running but no open tabs accessible. Opening URL in browser.",
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

        # STEP 5: Destination is not open
        active_tab = self.get_active_tab()
        target_win = active_tab.window_id if active_tab else 0
        
        has_full_inspection = self.extension_bridge.is_connected or self.cdp_bridge.is_available()
        if not has_full_inspection:
            reason = f"Destination {destination_url} is not open in any visible window title. (Companion Extension disconnected; background tabs cannot be inspected). Opening new tab."
        else:
            reason = f"Destination {destination_url} is not open in any existing tab. Opening single new tab."

        return OpenUrlDecision(
            action="create_tab",
            target_url=destination_url,
            window_id=target_win,
            reason=reason,
        )

    async def execute_decision_async(self, decision: OpenUrlDecision) -> BrowserActionResult:
        """Asynchronously execute the planned tab decision across the active browser bridge."""
        action = decision.action
        target_url = decision.target_url

        # Case 1: Activate existing tab
        if action == "activate_tab" and decision.target_tab:
            tab = decision.target_tab
            success = await self.activate_tab_async(tab.tab_id, window_id=tab.window_id, title_hint=tab.title)
            if success:
                return BrowserActionResult(
                    success=True,
                    action="activate_tab",
                    message=f"Activated existing tab: '{tab.title or tab.url}' (tab ID {tab.tab_id}). No duplicate tab created.",
                    reused_existing=True,
                    tab=tab,
                    metadata={"tab_id": tab.tab_id, "window_id": tab.window_id, "url": tab.url},
                )
            # If tab activation failed, the tab might have been closed by user
            logger.warning("Tab activation failed for tab ID %s; purging from cache and re-planning.", tab.tab_id)
            self.extension_bridge.remove_tab_from_cache(tab.tab_id)
            re_decision = self.plan_open_url_decision(target_url, explicit_new_tab=False)
            if re_decision.action != "activate_tab":
                return await self.execute_decision_async(re_decision)
            return BrowserActionResult(
                success=False,
                action="activate_tab",
                message=f"Failed to activate existing tab {tab.tab_id}. Tab may have been closed.",
                reused_existing=False,
            )

        # Case 2: Navigate existing tab
        if action == "navigate_tab" and decision.target_tab:
            tab = decision.target_tab
            success = await self.navigate_tab_async(tab.tab_id, target_url, window_id=tab.window_id)
            if success:
                tab.url = target_url
                tab.domain = TabMatcher.extract_domain(target_url)
                return BrowserActionResult(
                    success=True,
                    action="navigate_tab",
                    message=f"Navigated existing tab '{tab.title}' to {target_url}. Reused tab {tab.tab_id}.",
                    reused_existing=True,
                    tab=tab,
                    metadata={"tab_id": tab.tab_id, "window_id": tab.window_id, "url": target_url},
                )
            # NEVER launch a new browser window/tab as fallback for failed navigation!
            return BrowserActionResult(
                success=False,
                action="navigate_tab",
                message=f"Failed to navigate existing tab {tab.tab_id} to {target_url}. (Extension or CDP navigation unavailable).",
                reused_existing=False,
            )

        # Case 3: Create one new tab in existing browser
        if action == "create_tab":
            created_tab = await self.create_tab_async(target_url, window_id=decision.window_id or 0)
            if created_tab is not None:
                return BrowserActionResult(
                    success=True,
                    action="create_tab",
                    message=f"Opened {target_url} in a new tab.",
                    reused_existing=False,
                    tab=created_tab,
                    metadata={"url": target_url, "tab_id": created_tab.tab_id},
                )
            return BrowserActionResult(
                success=False,
                action="create_tab",
                message=f"Could not open new tab for {target_url}.",
                reused_existing=False,
            )

        # Case 4: Launch browser process
        if action == "launch_browser":
            launched = self.detector.launch_browser(target_url)
            if launched:
                self._set_state(BrowserState.CONNECTED)
                return BrowserActionResult(
                    success=True,
                    action="launch_browser",
                    message=f"Launched desktop browser with {target_url}.",
                    reused_existing=False,
                    metadata={"url": target_url},
                )
            return BrowserActionResult(
                success=False,
                action="launch_browser",
                message=f"Failed to launch desktop browser for {target_url}. Verify browser executable is installed.",
                reused_existing=False,
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

        return BrowserActionResult(
            success=False,
            action="unknown",
            message=f"Unrecognized browser decision: {action}",
        )

    def _playwright_activate_tab(self, tab_id: str | int) -> bool:
        """Synchronous tab activation via direct Playwright context calls."""
        if self._playwright_context is None:
            return False
        try:
            idx = int(str(tab_id).replace("pw_", ""))
            pages = getattr(self._playwright_context, "pages", [])
            if 0 <= idx < len(pages):
                res = pages[idx].bring_to_front()
                if asyncio.iscoroutine(res):
                    pass
                self._active_playwright_tab_idx = idx
                return True
        except Exception as e:
            logger.debug("Playwright page activation error: %s", e)
        return False

    def _playwright_navigate_tab(self, tab_id: str | int, url: str) -> bool:
        """Synchronous tab navigation via direct Playwright context calls."""
        if self._playwright_context is None:
            return False
        try:
            idx = int(str(tab_id).replace("pw_", ""))
            pages = getattr(self._playwright_context, "pages", [])
            if 0 <= idx < len(pages):
                res = pages[idx].goto(url, wait_until="domcontentloaded")
                if asyncio.iscoroutine(res):
                    pass
                self._active_playwright_tab_idx = idx
                return True
        except Exception as e:
            logger.debug("Playwright page navigation error: %s", e)
        return False

    def _playwright_create_tab(self, url: str) -> Optional[BrowserTabInfo]:
        """Synchronous tab creation via direct Playwright context calls."""
        if self._playwright_context is None:
            return None
        try:
            new_page = self._playwright_context.new_page()
            new_page.goto(url, wait_until="domcontentloaded")
            pages = getattr(self._playwright_context, "pages", [])
            idx = len(pages) - 1
            self._active_playwright_tab_idx = idx
            title = ""
            try:
                title = new_page.title()
            except Exception:
                pass
            return BrowserTabInfo(
                tab_id=f"pw_{idx}",
                window_id=1,
                url=url,
                title=title or url,
                domain=TabMatcher.extract_domain(url),
                is_active=True,
            )
        except Exception as e:
            logger.debug("Playwright page creation error: %s", e)
            return None

    def _execute_playwright_decision(self, decision: OpenUrlDecision) -> BrowserActionResult:
        """Synchronously execute a tab decision directly against the registered Playwright context."""
        action = decision.action
        target_url = decision.target_url

        if action == "activate_tab" and decision.target_tab:
            tab = decision.target_tab
            success = self._playwright_activate_tab(tab.tab_id)
            if success:
                return BrowserActionResult(
                    success=True,
                    action="activate_tab",
                    message=f"Activated existing tab: '{tab.title or tab.url}' (tab ID {tab.tab_id}). No duplicate tab created.",
                    reused_existing=True,
                    tab=tab,
                    metadata={"tab_id": tab.tab_id, "window_id": tab.window_id, "url": tab.url},
                )
            return BrowserActionResult(
                success=False,
                action="activate_tab",
                message=f"Failed to activate existing tab {tab.tab_id}.",
                reused_existing=False,
            )

        if action == "navigate_tab" and decision.target_tab:
            tab = decision.target_tab
            success = self._playwright_navigate_tab(tab.tab_id, target_url)
            if success:
                tab.url = target_url
                tab.domain = TabMatcher.extract_domain(target_url)
                return BrowserActionResult(
                    success=True,
                    action="navigate_tab",
                    message=f"Navigated existing tab '{tab.title}' to {target_url}. Reused tab {tab.tab_id}.",
                    reused_existing=True,
                    tab=tab,
                    metadata={"tab_id": tab.tab_id, "window_id": tab.window_id, "url": target_url},
                )
            return BrowserActionResult(
                success=False,
                action="navigate_tab",
                message=f"Failed to navigate existing tab {tab.tab_id} to {target_url}.",
                reused_existing=False,
            )

        if action == "create_tab":
            created_tab = self._playwright_create_tab(target_url)
            if created_tab is not None:
                return BrowserActionResult(
                    success=True,
                    action="create_tab",
                    message=f"Opened {target_url} in a new tab.",
                    reused_existing=False,
                    tab=created_tab,
                    metadata={"url": target_url, "tab_id": created_tab.tab_id},
                )
            return BrowserActionResult(
                success=False,
                action="create_tab",
                message=f"Could not open new tab for {target_url}.",
                reused_existing=False,
            )

        if action == "ask_user":
            return BrowserActionResult(
                success=False,
                action="ask_user",
                message=decision.reason,
                reused_existing=True,
                tab=decision.target_tab,
            )

        return BrowserActionResult(
            success=False,
            action="unknown",
            message=f"Unrecognized browser decision: {action}",
        )

    def _run_sync(self, coro: Any) -> Any:
        """Safely execute an async coroutine from synchronous contexts without deadlocks."""
        bridge_loop = self.extension_bridge.loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None:
            # If current thread already has a running event loop, run in a worker thread to prevent deadlocking
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(coro)).result(timeout=15.0)

        if bridge_loop is not None and bridge_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, bridge_loop)
            return future.result(timeout=15.0)

        return asyncio.run(coro)

    def execute_decision(self, decision: OpenUrlDecision) -> BrowserActionResult:
        """Synchronous wrapper for execute_decision_async or direct Playwright execution."""
        if self._playwright_context is not None:
            return self._execute_playwright_decision(decision)
        return self._run_sync(self.execute_decision_async(decision))

    async def open_or_reuse_async(
        self,
        query_or_url: str,
        explicit_new_tab: bool = False,
        task_context: str = "",
    ) -> BrowserActionResult:
        """High-level async entry point with per-destination request coordination."""
        dest_url = self.resolve_destination_url(query_or_url)
        if not dest_url:
            return BrowserActionResult(
                success=False,
                action="error",
                message="No URL or search query provided.",
            )

        # Lock per destination domain/key to avoid concurrent race conditions creating duplicate tabs
        dest_key = TabMatcher.extract_domain(dest_url) or TabMatcher.normalize_url(dest_url) or dest_url
        lock = self._get_destination_lock(dest_key)

        async with lock:
            # Query fresh tabs if extension is connected
            if self.extension_bridge.is_connected:
                try:
                    fresh_tabs = await self.extension_bridge.send_command("get_tabs", {}, timeout=2.0)
                    if isinstance(fresh_tabs, list):
                        self.extension_bridge.update_tabs(fresh_tabs)
                except Exception:
                    pass

            decision = self.plan_open_url_decision(
                dest_url,
                explicit_new_tab=explicit_new_tab,
                task_context=task_context,
            )
            return await self.execute_decision_async(decision)

    def open_or_reuse(
        self,
        query_or_url: str,
        explicit_new_tab: bool = False,
        task_context: str = "",
    ) -> BrowserActionResult:
        """Synchronous entry point delegating to open_or_reuse_async or executing Playwright directly."""
        if self._playwright_context is not None:
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
            return self._execute_playwright_decision(decision)

        return self._run_sync(
            self.open_or_reuse_async(
                query_or_url=query_or_url,
                explicit_new_tab=explicit_new_tab,
                task_context=task_context,
            )
        )

    async def activate_tab_async(
        self,
        tab_id: str | int,
        window_id: str | int = 0,
        title_hint: str = "",
    ) -> bool:
        """Activate a specific tab and bring its window to the foreground asynchronously."""
        # 0. Playwright context page activation
        if str(tab_id).startswith("pw_") and self._playwright_context is not None:
            try:
                idx = int(str(tab_id).replace("pw_", ""))
                if 0 <= idx < len(self._playwright_context.pages):
                    self._playwright_context.pages[idx].bring_to_front()
                    return True
            except Exception as e:
                logger.debug("Playwright page activation error: %s", e)
                return False

        # 1. Try extension bridge
        if self.extension_bridge.is_connected:
            try:
                res = await self.extension_bridge.send_command(
                    "activate_tab",
                    {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id, "window_id": window_id},
                    timeout=5.0,
                )
                return bool(res)
            except Exception as exc:
                logger.debug("Extension tab activation failed: %s", exc)
                return False

        # 2. Try CDP bridge
        if self.cdp_bridge.is_available():
            return self.cdp_bridge.activate_tab(str(tab_id))

        # 3. Bring window to foreground on Windows GUI
        if str(tab_id).startswith("win_"):
            hwnd = int(str(tab_id).replace("win_", ""))
            return self.detector.bring_window_to_foreground(hwnd)

        if title_hint:
            return self.detector.bring_browser_window_matching_title_to_foreground(title_hint)

        return False

    def activate_tab(
        self,
        tab_id: str | int,
        window_id: str | int = 0,
        title_hint: str = "",
    ) -> bool:
        """Synchronous wrapper for activate_tab_async."""
        if self._playwright_context is not None:
            return self._playwright_activate_tab(tab_id)
        return self._run_sync(self.activate_tab_async(tab_id, window_id=window_id, title_hint=title_hint))

    async def navigate_tab_async(
        self,
        tab_id: str | int,
        url: str,
        window_id: str | int = 0,
    ) -> bool:
        """Navigate an existing tab to a new URL asynchronously without fallback duplicate tab creation."""
        # 0. Playwright page navigation
        if str(tab_id).startswith("pw_") and self._playwright_context is not None:
            try:
                idx = int(str(tab_id).replace("pw_", ""))
                if 0 <= idx < len(self._playwright_context.pages):
                    self._playwright_context.pages[idx].goto(url, wait_until="domcontentloaded")
                    return True
            except Exception as e:
                logger.debug("Playwright page navigation error: %s", e)
                return False

        if self.extension_bridge.is_connected:
            try:
                res = await self.extension_bridge.send_command(
                    "navigate_tab",
                    {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id, "url": url},
                    timeout=6.0,
                )
                if res:
                    # Update local cache URL for this tab
                    for t in self.extension_bridge._tabs_cache:
                        if str(t.tab_id) == str(tab_id):
                            t.url = url
                            t.domain = TabMatcher.extract_domain(url)
                    return True
                return False
            except Exception as exc:
                logger.debug("Extension tab navigation failed: %s", exc)
                return False

        if self.cdp_bridge.is_available():
            # CDP cannot navigate without extension in standard user session
            return False

        logger.warning("Cannot navigate tab %s to %s: extension and CDP are unavailable.", tab_id, url)
        return False

    def navigate_tab(
        self,
        tab_id: str | int,
        url: str,
        window_id: str | int = 0,
    ) -> bool:
        """Synchronous wrapper for navigate_tab_async."""
        if self._playwright_context is not None:
            return self._playwright_navigate_tab(tab_id, url)
        return self._run_sync(self.navigate_tab_async(tab_id, url, window_id=window_id))

    async def create_tab_async(
        self,
        url: str,
        window_id: str | int = 0,
    ) -> Optional[BrowserTabInfo]:
        """Create a single new tab in the running browser asynchronously."""
        # 0. Playwright page creation
        if self._playwright_context is not None:
            try:
                new_page = self._playwright_context.new_page()
                new_page.goto(url, wait_until="domcontentloaded")
                idx = len(self._playwright_context.pages) - 1
                return BrowserTabInfo(
                    tab_id=f"pw_{idx}",
                    window_id=1,
                    url=url,
                    title=new_page.title() or url,
                    domain=TabMatcher.extract_domain(url),
                    is_active=True,
                )
            except Exception as e:
                logger.debug("Playwright page creation error: %s", e)
                return None

        # 1. Extension bridge
        if self.extension_bridge.is_connected:
            try:
                res = await self.extension_bridge.send_command(
                    "create_tab",
                    {"url": url, "window_id": window_id, "active": True},
                    timeout=6.0,
                )
                if res and isinstance(res, dict) and "tab_id" in res:
                    new_tab_id = res["tab_id"]
                    win_id = res.get("window_id", window_id)
                    domain = TabMatcher.extract_domain(url)
                    new_tab = BrowserTabInfo(
                        tab_id=new_tab_id,
                        window_id=win_id,
                        url=url,
                        domain=domain,
                        is_active=True,
                    )
                    self.extension_bridge._tabs_cache.append(new_tab)
                    return new_tab
            except Exception as exc:
                logger.debug("Extension create tab failed: %s", exc)
                return None

        # 2. CDP bridge
        if self.cdp_bridge.is_available():
            return self.cdp_bridge.create_tab(url)

        # 3. Windows launch fallback (only when extension and CDP are not active)
        launched = self.detector.launch_browser(url)
        if launched:
            return BrowserTabInfo(tab_id="launched", url=url, is_active=True)
        return None

    def create_tab(
        self,
        url: str,
        window_id: str | int = 0,
    ) -> Optional[BrowserTabInfo]:
        """Synchronous wrapper for create_tab_async."""
        if self._playwright_context is not None:
            return self._playwright_create_tab(url)
        return self._run_sync(self.create_tab_async(url, window_id=window_id))

    async def close_tab_async(self, tab_id: str | int) -> bool:
        """Close an open browser tab asynchronously."""
        if self.extension_bridge.is_connected:
            try:
                res = await self.extension_bridge.send_command(
                    "close_tab",
                    {"tab_id": int(tab_id) if str(tab_id).isdigit() else tab_id},
                    timeout=5.0,
                )
                if res:
                    self.extension_bridge.remove_tab_from_cache(tab_id)
                    return True
                return False
            except Exception as exc:
                logger.debug("Extension close tab failed: %s", exc)
                return False

        if self.cdp_bridge.is_available():
            return self.cdp_bridge.close_tab(str(tab_id))

        return False

    def close_tab(self, tab_id: str | int) -> bool:
        """Synchronous wrapper for close_tab_async."""
        return self._run_sync(self.close_tab_async(tab_id))

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic and operational status of the browser manager."""
        self.refresh_status()
        tabs = self.list_tabs()
        active = self.get_active_tab()
        running_browsers = self.detector.get_running_browsers()

        mode = "unconnected"
        full_tab_control = False
        diagnostic_message = "No desktop browser running."

        if self.extension_bridge.is_connected:
            mode = "extension"
            full_tab_control = True
            diagnostic_message = "Connected to Chrome/Edge Companion Extension with full tab visibility and control."
        elif self.cdp_bridge.is_available():
            mode = "cdp"
            full_tab_control = True
            diagnostic_message = "Connected via Chrome DevTools Protocol (CDP)."
        elif running_browsers:
            mode = "windows_gui"
            full_tab_control = False
            diagnostic_message = (
                "Browser is running, but Companion Extension is disconnected and CDP is unavailable. "
                "Only window titles can be inspected; background tabs cannot be verified. "
                "Load the Jarvis Companion Extension for intelligent tab reuse."
            )

        return {
            "state": self.state.value,
            "mode": mode,
            "full_tab_control": full_tab_control,
            "diagnostic_message": diagnostic_message,
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

"""Regression test suite for browser integration reliability, concurrency, error handling, and security."""

import asyncio
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from openjarvis.browser.manager import DesktopBrowserManager
from openjarvis.browser.models import BrowserActionResult, BrowserTabInfo, OpenUrlDecision
from openjarvis.server.app import create_app
from openjarvis.tools.desktop import OpenUrlTool


@pytest.fixture
def manager():
    with patch("openjarvis.browser.manager.WindowsBrowserDetector"):
        mgr = DesktopBrowserManager()
        return mgr


# ---------------------------------------------------------------------------
# Test 1: Manager failure does not invoke webbrowser.open()
# ---------------------------------------------------------------------------
def test_manager_failure_does_not_invoke_webbrowser_open():
    tool = OpenUrlTool()
    failed_result = BrowserActionResult(
        success=False,
        action="create_tab",
        message="Companion extension is disconnected.",
        reused_existing=False,
    )

    with patch("webbrowser.open") as mock_webbrowser, \
         patch("openjarvis.browser.manager.get_browser_manager") as mock_get_mgr:
        mock_mgr = MagicMock()
        mock_mgr.open_or_reuse.return_value = failed_result
        mock_get_mgr.return_value = mock_mgr

        res = tool.execute(url="https://www.linkedin.com")
        assert res.success is False
        assert "Browser control unavailable" in res.content
        mock_webbrowser.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Failed extension navigation does not launch a duplicate browser tab
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_extension_navigation_does_not_launch_duplicate_tab(manager):
    manager.extension_bridge._connected = True
    manager.extension_bridge._active_ws = MagicMock()

    # Extension navigation fails
    with patch.object(manager.extension_bridge, "send_command", side_effect=RuntimeError("Tab crashed")):
        res = await manager.navigate_tab_async(tab_id=42, url="https://www.linkedin.com/jobs")
        assert res is False
        # Verify launch_browser was NOT called as a silent fallback
        manager.detector.launch_browser.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Extension activation waits for acknowledgement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extension_activation_waits_for_acknowledgement(manager):
    manager.extension_bridge._connected = True
    manager.extension_bridge._active_ws = MagicMock()

    acknowledged = False

    async def slow_send_command(action, params, timeout=5.0):
        nonlocal acknowledged
        await asyncio.sleep(0.05)
        acknowledged = True
        return {"tab_id": 42}

    with patch.object(manager.extension_bridge, "send_command", side_effect=slow_send_command):
        task = asyncio.create_task(manager.activate_tab_async(42))
        assert acknowledged is False  # Has not completed immediately
        res = await task
        assert acknowledged is True
        assert res is True


# ---------------------------------------------------------------------------
# Test 4: Extension tab creation waits for acknowledgement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extension_tab_creation_waits_for_acknowledgement(manager):
    manager.extension_bridge._connected = True
    manager.extension_bridge._active_ws = MagicMock()

    async def mock_create(action, params, timeout=6.0):
        await asyncio.sleep(0.02)
        return {"tab_id": 789, "window_id": 10, "url": params["url"]}

    with patch.object(manager.extension_bridge, "send_command", side_effect=mock_create):
        tab = await manager.create_tab_async("https://youtube.com")
        assert tab is not None
        assert tab.tab_id == 789
        assert tab.url == "https://youtube.com"


# ---------------------------------------------------------------------------
# Test 5: Repeated concurrent requests do not create duplicate tabs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repeated_concurrent_requests_do_not_create_duplicate_tabs(manager):
    manager.detector.is_browser_running.return_value = True
    manager.extension_bridge._connected = True
    manager.extension_bridge._active_ws = MagicMock()

    created_tabs = []

    async def mock_send_command(action, params, timeout=6.0):
        if action == "create_tab":
            await asyncio.sleep(0.03)  # Simulate network latency
            new_id = len(created_tabs) + 100
            tab_dict = {"tab_id": new_id, "window_id": 1, "url": params["url"]}
            created_tabs.append(tab_dict)
            return tab_dict
        elif action == "activate_tab":
            return {"tab_id": params["tab_id"]}
        elif action == "get_tabs":
            return [
                {"id": t["tab_id"], "windowId": 1, "url": t["url"], "title": "LinkedIn", "active": True}
                for t in created_tabs
            ]
        return {}

    with patch.object(manager.extension_bridge, "send_command", side_effect=mock_send_command):
        # Fire 3 concurrent open requests for linkedin
        results = await asyncio.gather(
            manager.open_or_reuse_async("https://www.linkedin.com"),
            manager.open_or_reuse_async("https://www.linkedin.com"),
            manager.open_or_reuse_async("linkedin"),
        )

        assert len(created_tabs) == 1  # Exactly ONE tab was created!
        # The other requests reused the created tab
        actions = [r.action for r in results]
        assert actions.count("create_tab") == 1
        assert actions.count("activate_tab") == 2
        assert sum(1 for r in results if r.reused_existing) == 2


# ---------------------------------------------------------------------------
# Test 6: Disconnected extension returns an honest error
# ---------------------------------------------------------------------------
def test_disconnected_extension_returns_honest_status(manager):
    manager.detector.is_browser_running.return_value = True
    manager.extension_bridge._connected = False
    manager.cdp_bridge._available = False

    status = manager.get_status()
    assert status["mode"] == "windows_gui"
    assert status["full_tab_control"] is False
    assert "Companion Extension is disconnected" in status["diagnostic_message"]


# ---------------------------------------------------------------------------
# Test 7: Stale tab IDs are handled safely
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_tab_id_purged_and_handled(manager):
    manager.detector.is_browser_running.return_value = True
    manager.extension_bridge._connected = True
    manager.extension_bridge._active_ws = MagicMock()

    # Prepopulate tab cache with stale tab 99
    stale_tab = BrowserTabInfo(tab_id=99, url="https://www.linkedin.com", title="LinkedIn")
    manager.extension_bridge._tabs_cache = [stale_tab]

    call_count = 0

    async def mock_command(action, params, timeout=5.0):
        nonlocal call_count
        call_count += 1
        if action == "activate_tab" and params.get("tab_id") == 99:
            # Tab 99 is gone, simulate extension reporting failure and stale ID
            manager.extension_bridge.remove_tab_from_cache(99)
            raise RuntimeError("No tab with id: 99")
        if action == "create_tab":
            return {"tab_id": 101, "window_id": 1, "url": params["url"]}
        return {}

    with patch.object(manager.extension_bridge, "send_command", side_effect=mock_command):
        decision = manager.plan_open_url_decision("https://www.linkedin.com")
        assert decision.action == "activate_tab"

        result = await manager.execute_decision_async(decision)
        # Should detect failure, purge stale tab 99, and recover cleanly
        assert 99 not in [t.tab_id for t in manager.extension_bridge.get_tabs()]
        assert result.success is True
        assert result.action == "create_tab"
        assert result.tab.tab_id == 101


# ---------------------------------------------------------------------------
# Test 8: Failed navigation does not update cached URL incorrectly
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_navigation_does_not_corrupt_cache(manager):
    manager.extension_bridge._connected = True
    manager.extension_bridge._active_ws = MagicMock()
    tab = BrowserTabInfo(tab_id=42, url="https://github.com", title="GitHub")
    manager.extension_bridge._tabs_cache = [tab]

    with patch.object(manager.extension_bridge, "send_command", side_effect=RuntimeError("Navigation rejected")):
        ok = await manager.navigate_tab_async(tab_id=42, url="https://github.com/settings")
        assert ok is False
        # Tab URL in cache must remain original
        assert manager.extension_bridge._tabs_cache[0].url == "https://github.com"


# ---------------------------------------------------------------------------
# Test 9: Browser reconnection refreshes tab state
# ---------------------------------------------------------------------------
def test_browser_reconnection_refreshes_tab_state(manager):
    bridge = manager.extension_bridge
    assert bridge.is_connected is False

    raw_tabs = [
        {"id": 1, "windowId": 1, "url": "https://reddit.com", "title": "Reddit", "active": True}
    ]
    bridge.update_tabs(raw_tabs)
    assert bridge.is_connected is True
    assert len(manager.list_tabs()) == 1
    assert manager.list_tabs()[0].url == "https://reddit.com"


# ---------------------------------------------------------------------------
# Test 10: Pairing endpoint is not remotely accessible
# ---------------------------------------------------------------------------
def test_pairing_endpoint_remote_access_blocked():
    app = create_app(MagicMock(), "test-model")
    client = TestClient(app)

    # Remote client IP simulation
    with patch("openjarvis.server.browser_routes._is_local_request", return_value=False):
        response = client.get("/v1/browser/extension/token")
        assert response.status_code == 403
        assert "only accessible from local loopback" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Test 11: Sensitive active tabs still require confirmation
# ---------------------------------------------------------------------------
def test_sensitive_active_tabs_require_confirmation(manager):
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://mail.google.com/mail/u/0/#compose", title="Compose - Gmail"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        decision = manager.plan_open_url_decision("https://mail.google.com/mail/u/0/#inbox")
        assert decision.action == "ask_user"
        assert "unsaved work" in decision.reason


# ---------------------------------------------------------------------------
# Test 12: Explicit new-tab requests still work
# ---------------------------------------------------------------------------
def test_explicit_new_tab_requests_work(manager):
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://www.linkedin.com/feed", title="LinkedIn"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        # Explicit parameter
        d1 = manager.plan_open_url_decision("https://www.linkedin.com", explicit_new_tab=True)
        assert d1.action == "create_tab"

        # Explicit natural language context
        d2 = manager.plan_open_url_decision("https://www.linkedin.com", task_context="open another tab for linkedin")
        assert d2.action == "create_tab"

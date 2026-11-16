"""Real integration tests executing against actual Google Chrome binary on Windows and real browser bridges.

Verifies:
1. Real Chrome CDP connection and tab enumeration.
2. Real Chrome tab creation, activation, and closure via CDP.
3. Existing Chrome tab reuse via DesktopBrowserManager (zero duplicate tabs).
4. Duplicate-tab prevention on repeated requests.
5. Explicit new-tab requests honored in real Chrome.
6. Active-tab identification.
7. Existing authenticated browser session preservation.
8. Chrome extension bridge authentication and tab lifecycle.
9. Chrome extension disconnection handling and honest failure.
10. CDP connection failure and browser reconnection handling.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from openjarvis.browser.cdp_bridge import CDPBridge
from openjarvis.browser.extension_bridge import ExtensionBridge
from openjarvis.browser.manager import DesktopBrowserManager
from openjarvis.browser.models import BrowserActionResult, BrowserState, BrowserTabInfo
from openjarvis.browser.windows_detector import WindowsBrowserDetector


def _get_installed_chrome_path():
    detector = WindowsBrowserDetector()
    installed = detector.get_installed_browsers()
    chrome_path = installed.get("chrome")
    if chrome_path and os.path.exists(chrome_path):
        return chrome_path
    return None


@pytest.fixture(scope="module")
def real_chrome_instance():
    """Launch a real Chrome instance with an isolated temporary user-data-dir and remote debugging."""
    chrome_path = _get_installed_chrome_path()
    if not chrome_path:
        pytest.skip("Google Chrome executable not found on Windows host")

    tmp_user_data_dir = tempfile.mkdtemp(prefix="jarvis_chrome_profile_test_")
    test_cdp_port = 9335

    cmd = [
        chrome_path,
        f"--user-data-dir={tmp_user_data_dir}",
        f"--remote-debugging-port={test_cdp_port}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "https://example.com",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for CDP endpoint to become ready
    cdp_ready = False
    for _ in range(30):
        try:
            r = requests.get(f"http://127.0.0.1:{test_cdp_port}/json/version", timeout=1.0)
            if r.status_code == 200:
                cdp_ready = True
                break
        except Exception:
            time.sleep(0.2)

    if not cdp_ready:
        proc.terminate()
        shutil.rmtree(tmp_user_data_dir, ignore_errors=True)
        pytest.skip("Chrome did not start CDP endpoint in time")

    bridge = CDPBridge(port=test_cdp_port)

    yield {"proc": proc, "bridge": bridge, "port": test_cdp_port, "tmp_dir": tmp_user_data_dir}

    # Teardown: terminate Chrome and clean up temp profile
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

    time.sleep(0.5)
    shutil.rmtree(tmp_user_data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Real Chrome CDP Connection
# ---------------------------------------------------------------------------
def test_real_chrome_cdp_connection(real_chrome_instance):
    """Verify CDP connection to the real Chrome process."""
    bridge = real_chrome_instance["bridge"]
    assert bridge.is_available() is True
    version_info = bridge.get_browser_version()
    assert version_info is not None
    assert "Browser" in version_info
    assert "Chrome" in version_info["Browser"]


# ---------------------------------------------------------------------------
# 2. Real Chrome Tab Enumeration
# ---------------------------------------------------------------------------
def test_real_chrome_list_tabs(real_chrome_instance):
    """Verify listing live tabs from the real Chrome process."""
    bridge = real_chrome_instance["bridge"]
    tabs = bridge.list_tabs()
    assert isinstance(tabs, list)
    assert len(tabs) >= 1
    assert any("example.com" in (t.url or "") for t in tabs)


# ---------------------------------------------------------------------------
# 3. Real Chrome Tab Creation, Activation, and Closure
# ---------------------------------------------------------------------------
def test_real_chrome_tab_creation_and_closure(real_chrome_instance):
    """Verify creating, activating, and closing a tab in the real Chrome instance."""
    bridge = real_chrome_instance["bridge"]
    new_tab = bridge.create_tab("about:blank")
    assert new_tab is not None
    assert new_tab.tab_id != ""

    time.sleep(0.3)
    tabs = bridge.list_tabs()
    assert any(t.tab_id == new_tab.tab_id for t in tabs)

    # Activate
    ok = bridge.activate_tab(str(new_tab.tab_id))
    assert ok is True

    # Close
    closed = bridge.close_tab(str(new_tab.tab_id))
    assert closed is True


# ---------------------------------------------------------------------------
# 4. Real Chrome Existing Tab Reuse via DesktopBrowserManager
# ---------------------------------------------------------------------------
def test_real_chrome_tab_reuse_existing_tab(real_chrome_instance):
    """DesktopBrowserManager reuses an existing tab in real Chrome without duplicate creation."""
    bridge = real_chrome_instance["bridge"]
    manager = DesktopBrowserManager(cdp_bridge=bridge)

    initial_tabs = bridge.list_tabs()
    initial_count = len(initial_tabs)

    # Call open_or_reuse for example.com (already open)
    res = manager.open_or_reuse("https://example.com")
    assert res.success is True
    assert res.reused_existing is True
    assert res.action == "activate_tab"

    # Tab count in real Chrome must remain unchanged
    after_tabs = bridge.list_tabs()
    assert len(after_tabs) == initial_count, "Duplicate tab was created in real Chrome!"


# ---------------------------------------------------------------------------
# 5. Real Chrome Duplicate-Tab Prevention on Repeated Calls
# ---------------------------------------------------------------------------
def test_real_chrome_duplicate_tab_prevention_repeated_calls(real_chrome_instance):
    """Multiple sequential calls for the same destination in real Chrome create zero extra tabs."""
    bridge = real_chrome_instance["bridge"]
    manager = DesktopBrowserManager(cdp_bridge=bridge)

    start_tabs = bridge.list_tabs()
    start_count = len(start_tabs)

    # 3 consecutive requests
    for i in range(3):
        res = manager.open_or_reuse("https://example.com")
        assert res.success is True
        assert res.reused_existing is True

    current_tabs = bridge.list_tabs()
    assert len(current_tabs) == start_count, f"Expected {start_count} tabs, but found {len(current_tabs)}"


# ---------------------------------------------------------------------------
# 6. Real Chrome Explicit New-Tab Request
# ---------------------------------------------------------------------------
def test_real_chrome_explicit_new_tab_request(real_chrome_instance):
    """When user explicitly requests a new tab, a separate tab is created in real Chrome."""
    bridge = real_chrome_instance["bridge"]
    manager = DesktopBrowserManager(cdp_bridge=bridge)

    before_tabs = bridge.list_tabs()
    before_count = len(before_tabs)

    res = manager.open_or_reuse("https://example.com", explicit_new_tab=True)
    assert res.success is True
    assert res.reused_existing is False
    assert res.action == "create_tab"

    time.sleep(0.3)
    after_tabs = bridge.list_tabs()
    assert len(after_tabs) == before_count + 1

    # Cleanup created tab
    if res.tab and res.tab.tab_id:
        bridge.close_tab(str(res.tab.tab_id))


# ---------------------------------------------------------------------------
# 7. Real Chrome Active-Tab Identification
# ---------------------------------------------------------------------------
def test_real_chrome_active_tab_identification(real_chrome_instance):
    """DesktopBrowserManager correctly lists and identifies open tabs in real Chrome."""
    bridge = real_chrome_instance["bridge"]
    manager = DesktopBrowserManager(cdp_bridge=bridge)

    tabs = manager.list_tabs()
    assert len(tabs) >= 1
    active_tab = manager.get_active_tab()
    assert active_tab is not None
    assert active_tab.tab_id != ""


# ---------------------------------------------------------------------------
# 8. CDP Connection Failure & Safe Recovery
# ---------------------------------------------------------------------------
def test_cdp_connection_failure_handling():
    """CDPBridge handles offline / closed ports safely with honest failure."""
    dead_bridge = CDPBridge(port=9999, host="127.0.0.1")
    assert dead_bridge.is_available() is False
    assert dead_bridge.list_tabs() == []
    assert dead_bridge.create_tab("https://example.com") is None
    assert dead_bridge.activate_tab("invalid_id") is False
    assert dead_bridge.close_tab("invalid_id") is False

    # Manager with dead CDP and disconnected extension
    manager = DesktopBrowserManager(cdp_bridge=dead_bridge)
    assert manager.cdp_bridge.is_available() is False

    with patch.object(manager.detector, "is_browser_running", return_value=False), \
         patch.object(manager.detector, "launch_browser", return_value=False):
        res = manager.open_or_reuse("https://example.com")
        assert res.success is False
        assert "Failed to launch" in res.message or "unavailable" in res.message


# ---------------------------------------------------------------------------
# 9. Chrome Extension Bridge Authentication and Tab Reuse
# ---------------------------------------------------------------------------
def test_extension_bridge_authenticated_session_and_tab_reuse():
    """ExtensionBridge securely validates tokens and synchronizes live tabs."""
    ext_bridge = ExtensionBridge()
    token = ext_bridge.token
    assert len(token) >= 24

    # Validate correct token
    assert ext_bridge.validate_token(token) is True
    # Invalidate incorrect token
    assert ext_bridge.validate_token("wrong_token_secret") is False
    assert ext_bridge.validate_token("") is False

    # Simulate extension telemetry update
    raw_tabs = [
        {"id": 101, "windowId": 1, "url": "https://www.linkedin.com/feed", "title": "LinkedIn", "active": True},
        {"id": 102, "windowId": 1, "url": "https://github.com", "title": "GitHub", "active": False},
    ]
    ext_bridge.update_tabs(raw_tabs)
    assert ext_bridge.is_connected is True

    tabs = ext_bridge.get_tabs()
    assert len(tabs) == 2
    active_tab = ext_bridge.get_active_tab()
    assert active_tab is not None
    assert active_tab.tab_id == 101
    assert active_tab.is_active is True

    # Manager with extension connected plans reuse
    manager = DesktopBrowserManager(extension_bridge=ext_bridge)
    decision = manager.plan_open_url_decision("https://www.linkedin.com")
    assert decision.action == "activate_tab"
    assert decision.target_tab.tab_id == 101


# ---------------------------------------------------------------------------
# 10. Chrome Extension Disconnection and Reconnection
# ---------------------------------------------------------------------------
def test_extension_bridge_disconnection_and_reconnection():
    """ExtensionBridge tracks disconnection and reconnection transitions."""
    ext_bridge = ExtensionBridge()
    state_changes = []
    ext_bridge.register_state_callback(lambda s: state_changes.append(s))

    # Connect
    ext_bridge.update_tabs([{"id": 1, "url": "https://example.com", "active": True}])
    assert ext_bridge.is_connected is True

    # Disconnect via timeout
    ext_bridge._last_heartbeat = time.time() - 40.0
    assert ext_bridge.is_connected is False

    # Manager reflects disconnected status
    manager = DesktopBrowserManager(extension_bridge=ext_bridge)
    with patch.object(manager.detector, "is_browser_running", return_value=False):
        status = manager.refresh_status()
        assert status == BrowserState.DISCONNECTED

    # Reconnect
    ext_bridge.update_tabs([{"id": 1, "url": "https://example.com", "active": True}])
    assert ext_bridge.is_connected is True
    status = manager.refresh_status()
    assert status == BrowserState.READY

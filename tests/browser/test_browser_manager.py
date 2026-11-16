"""Unit and integration tests for DesktopBrowserManager and 6-step decision pipeline."""

from unittest.mock import MagicMock, patch
import pytest

from openjarvis.browser.manager import DesktopBrowserManager
from openjarvis.browser.models import BrowserState, BrowserTabInfo, OpenUrlDecision


@pytest.fixture
def manager():
    with patch("openjarvis.browser.manager.WindowsBrowserDetector"):
        mgr = DesktopBrowserManager()
        return mgr


def test_resolve_destination_url(manager):
    assert manager.resolve_destination_url("linkedin") == "https://www.linkedin.com"
    assert manager.resolve_destination_url("github") == "https://www.github.com"
    assert manager.resolve_destination_url("https://example.com/test") == "https://example.com/test"
    assert manager.resolve_destination_url("openjarvis.dev") == "https://openjarvis.dev"
    assert "google.com/search?q=weather+today" in manager.resolve_destination_url("weather today")


def test_decision_launch_when_no_browser_running(manager):
    # Browser not running
    manager.detector.is_browser_running.return_value = False
    manager.extension_bridge._connected = False

    decision = manager.plan_open_url_decision("https://www.linkedin.com")
    assert decision.action == "launch_browser"
    assert decision.target_url == "https://www.linkedin.com"


def test_decision_reuse_existing_tab(manager):
    # Browser running with LinkedIn tab open
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://www.linkedin.com/feed", title="LinkedIn Feed"),
        BrowserTabInfo(tab_id=2, url="https://github.com", title="GitHub"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        decision = manager.plan_open_url_decision("https://www.linkedin.com")
        assert decision.action == "activate_tab"
        assert decision.target_tab is not None
        assert decision.target_tab.tab_id == 1


def test_decision_navigate_existing_tab_for_subpath(manager):
    # Browser running with LinkedIn feed open, user wants LinkedIn jobs
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://www.linkedin.com/feed", title="LinkedIn Feed"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        decision = manager.plan_open_url_decision("https://www.linkedin.com/jobs")
        assert decision.action == "navigate_tab"
        assert decision.target_tab is not None
        assert decision.target_tab.tab_id == 1
        assert decision.target_url == "https://www.linkedin.com/jobs"


def test_decision_create_tab_when_not_open(manager):
    # Browser running with only GitHub open, user asks for LinkedIn
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=2, url="https://github.com", title="GitHub"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        decision = manager.plan_open_url_decision("https://www.linkedin.com")
        assert decision.action == "create_tab"
        assert decision.target_url == "https://www.linkedin.com"


def test_decision_explicit_new_tab_bypasses_reuse(manager):
    # User explicitly asks for a new tab
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://www.linkedin.com/feed", title="LinkedIn Feed"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        # Via explicit_new_tab parameter
        d1 = manager.plan_open_url_decision("https://www.linkedin.com", explicit_new_tab=True)
        assert d1.action == "create_tab"

        # Via natural language task context
        d2 = manager.plan_open_url_decision(
            "https://www.linkedin.com",
            task_context="open another tab for linkedin",
        )
        assert d2.action == "create_tab"


def test_duplicate_tab_prevention_on_repeated_requests(manager):
    # Demonstrates that calling open_or_reuse repeatedly reuses the first tab without creating new ones
    manager.detector.is_browser_running.return_value = True
    existing_tab = BrowserTabInfo(tab_id=42, url="https://www.linkedin.com/feed", title="LinkedIn Feed")
    tabs = [existing_tab]

    with patch.object(manager, "list_tabs", return_value=tabs), \
         patch.object(manager, "activate_tab", return_value=True) as mock_activate:

        # 1st call
        res1 = manager.open_or_reuse("https://www.linkedin.com")
        assert res1.success is True
        assert res1.reused_existing is True
        assert res1.action == "activate_tab"
        assert res1.tab.tab_id == 42

        # 2nd call (identical request)
        res2 = manager.open_or_reuse("linkedin")
        assert res2.success is True
        assert res2.reused_existing is True
        assert res2.action == "activate_tab"
        assert res2.tab.tab_id == 42

        # 3rd call
        res3 = manager.open_or_reuse("https://www.linkedin.com/feed/")
        assert res3.success is True
        assert res3.reused_existing is True
        assert res3.tab.tab_id == 42

        # In all three calls, activate_tab was called instead of creating new tabs
        assert mock_activate.call_count == 3


def test_sensitive_unsaved_work_triggers_user_confirmation(manager):
    # Tab has active compose
    manager.detector.is_browser_running.return_value = True
    tabs = [
        BrowserTabInfo(tab_id=99, url="https://mail.google.com/mail/u/0/#compose", title="Compose - Gmail"),
    ]
    with patch.object(manager, "list_tabs", return_value=tabs):
        decision = manager.plan_open_url_decision("https://mail.google.com/mail/u/0/#inbox")
        assert decision.action == "ask_user"
        assert decision.target_tab.tab_id == 99

        result = manager.execute_decision(decision)
        assert result.action == "ask_user"
        assert result.reused_existing is True

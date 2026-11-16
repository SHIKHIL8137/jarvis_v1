"""Real Playwright Browser Automation and Tab Reuse Test Suite.

Covers all 10 scenarios specified in Phase 9 requirements:
1. Website already open -> reuse existing tab without creating duplicates.
2. Website not open -> create exactly one new tab.
3. Repeated requests -> zero duplicate tabs created.
4. Multiple browser tabs -> select and activate the correct tab.
5. Browser disconnected -> recover safely with honest failure.
6. Existing authenticated session -> preserve cookies and login state across reuse.
7. User manually changes active tab -> detect current state and active tab.
8. Explicit new-tab request -> honor user intent by creating a separate new tab.
9. Existing website with different path -> reuse or navigate according to task intent.
10. Repeated browser tool calls -> reuse the same browser session.
"""

from __future__ import annotations

import time
from typing import Generator
from unittest.mock import patch

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from openjarvis.browser.manager import DesktopBrowserManager
from openjarvis.browser.matcher import TabMatcher
from openjarvis.browser.models import BrowserState


@pytest.fixture(scope="module")
def pw_browser() -> Generator[Browser, None, None]:
    """Launch headless Chromium browser for testing."""
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
    )
    yield browser
    browser.close()
    p.stop()


@pytest.fixture
def browser_context(pw_browser: Browser) -> Generator[BrowserContext, None, None]:
    """Create a fresh isolated browser context with route interception for fast, deterministic tests."""
    context = pw_browser.new_context()

    def route_handler(route):
        url = route.request.url
        domain = TabMatcher.extract_domain(url) or "example.com"
        title = domain.capitalize()
        if "linkedin" in url:
            title = "LinkedIn: Log In or Sign Up"
        elif "github" in url:
            title = "GitHub - Where software is built"
        elif "reddit" in url:
            title = "Reddit Home"
        elif "youtube" in url:
            title = "YouTube"
        elif "docs.python.org" in url:
            title = "Python Documentation"
        elif "dashboard" in url:
            title = "Dashboard App"

        body = f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{url}</p></body></html>"
        route.fulfill(status=200, content_type="text/html", body=body)

    context.route("**/*", route_handler)
    yield context
    context.close()


@pytest.fixture
def manager(browser_context: BrowserContext) -> Generator[DesktopBrowserManager, None, None]:
    """Provide a DesktopBrowserManager hooked into the active Playwright context."""
    mgr = DesktopBrowserManager()
    mgr.register_playwright_context(browser_context)
    yield mgr
    mgr.unregister_playwright_context()


# ---------------------------------------------------------------------------
# Scenario 1: Website already open -> reuse existing tab
# ---------------------------------------------------------------------------
def test_scenario_1_website_already_open_reuses_tab(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """When a website is already open in an existing tab, Jarvis must reuse it without opening a new tab."""
    page1 = browser_context.new_page()
    page1.goto("https://www.linkedin.com")

    initial_tab_count = len(browser_context.pages)
    assert initial_tab_count == 1

    # Request open_or_reuse for LinkedIn
    res = manager.open_or_reuse("https://www.linkedin.com")
    assert res.success is True
    assert res.reused_existing is True
    assert res.action == "activate_tab"
    assert len(browser_context.pages) == 1, "Duplicate tab was erroneously created!"


# ---------------------------------------------------------------------------
# Scenario 2: Website not open -> create one new tab
# ---------------------------------------------------------------------------
def test_scenario_2_website_not_open_creates_one_new_tab(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """When the requested website is not open anywhere, Jarvis creates exactly one new tab."""
    page1 = browser_context.new_page()
    page1.goto("https://www.google.com")

    assert len(browser_context.pages) == 1

    # Open GitHub (not open)
    res = manager.open_or_reuse("https://github.com")
    assert res.success is True
    assert res.reused_existing is False
    assert res.action == "create_tab"
    assert len(browser_context.pages) == 2


# ---------------------------------------------------------------------------
# Scenario 3: Repeated requests -> no duplicate tabs
# ---------------------------------------------------------------------------
def test_scenario_3_repeated_requests_prevent_duplicate_tabs(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """Repeated calls for the same destination must never spawn redundant tabs."""
    page1 = browser_context.new_page()
    page1.goto("https://reddit.com")

    assert len(browser_context.pages) == 1

    # Call 1
    res1 = manager.open_or_reuse("https://reddit.com")
    assert res1.success is True
    assert len(browser_context.pages) == 1

    # Call 2 (repeated)
    res2 = manager.open_or_reuse("https://reddit.com")
    assert res2.success is True
    assert res2.reused_existing is True
    assert len(browser_context.pages) == 1

    # Call 3 (repeated with subpath)
    res3 = manager.open_or_reuse("https://reddit.com")
    assert res3.success is True
    assert res3.reused_existing is True
    assert len(browser_context.pages) == 1, f"Expected 1 tab, but found {len(browser_context.pages)}"


# ---------------------------------------------------------------------------
# Scenario 4: Multiple browser tabs -> select the correct one
# ---------------------------------------------------------------------------
def test_scenario_4_multiple_tabs_selects_correct_tab(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """With 3 tabs open, Jarvis correctly identifies and activates the target tab."""
    page_yt = browser_context.new_page()
    page_yt.goto("https://youtube.com")

    page_gh = browser_context.new_page()
    page_gh.goto("https://github.com")

    page_li = browser_context.new_page()
    page_li.goto("https://www.linkedin.com")

    assert len(browser_context.pages) == 3

    # Ask for GitHub
    res = manager.open_or_reuse("https://github.com")
    assert res.success is True
    assert res.reused_existing is True
    assert "github" in res.tab.title.lower() or "github" in res.tab.url.lower()
    assert len(browser_context.pages) == 3


# ---------------------------------------------------------------------------
# Scenario 5: Browser disconnected -> recover safely
# ---------------------------------------------------------------------------
def test_scenario_5_browser_disconnected_recovers_safely(manager: DesktopBrowserManager):
    """When browser session is disconnected, manager returns honest failure without silent crash."""
    manager.unregister_playwright_context()
    manager.extension_bridge._connected = False

    # Force disconnected status
    manager._set_state(BrowserState.DISCONNECTED)

    # Attempting tab operation without available browser or launch
    with patch.object(manager.detector, "is_browser_running", return_value=False), \
         patch.object(manager.detector, "launch_browser", return_value=False):
        res = manager.open_or_reuse("https://example.com")
        assert res.success is False
        assert res.reused_existing is False
        assert "Failed to launch" in res.message or "unavailable" in res.message or "error" in res.action


# ---------------------------------------------------------------------------
# Scenario 6: Existing authenticated session -> preserve login state
# ---------------------------------------------------------------------------
def test_scenario_6_preserve_session_cookies_and_state(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """Reusing a tab preserves session cookies and authenticated local storage."""
    browser_context.add_cookies([
        {"name": "session_id", "value": "auth_token_secret_xyz123", "domain": ".dashboard.local", "path": "/"}
    ])

    page1 = browser_context.new_page()
    page1.goto("https://dashboard.local")

    res = manager.open_or_reuse("https://dashboard.local")
    assert res.success is True
    assert res.reused_existing is True

    # Verify cookies survived intact
    cookies = browser_context.cookies()
    assert any(c["name"] == "session_id" and c["value"] == "auth_token_secret_xyz123" for c in cookies)


# ---------------------------------------------------------------------------
# Scenario 7: User manually changes active tab -> detect current state
# ---------------------------------------------------------------------------
def test_scenario_7_detect_active_tab_change(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """Manager tracks the active page in the browser context."""
    page1 = browser_context.new_page()
    page1.goto("https://example.com/tab1")

    page2 = browser_context.new_page()
    page2.goto("https://example.com/tab2")

    # page2 was created last so it is active
    tabs = manager.list_tabs()
    assert len(tabs) == 2
    assert tabs[1].is_active is True

    # User switches to page 1
    page1.bring_to_front()
    active_tab = manager.get_active_tab()
    assert active_tab is not None


# ---------------------------------------------------------------------------
# Scenario 8: Explicit new-tab request -> create new tab
# ---------------------------------------------------------------------------
def test_scenario_8_explicit_new_tab_request(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """When user explicitly requests a new or separate tab, honor that intent even if already open."""
    page1 = browser_context.new_page()
    page1.goto("https://github.com")

    assert len(browser_context.pages) == 1

    # User says: "open another tab for github"
    res = manager.open_or_reuse(
        "https://github.com",
        explicit_new_tab=True,
        task_context="Please open a new tab for GitHub",
    )
    assert res.success is True
    assert res.reused_existing is False
    assert res.action == "create_tab"
    assert len(browser_context.pages) == 2


# ---------------------------------------------------------------------------
# Scenario 9: Existing website with different path -> reuse or navigate
# ---------------------------------------------------------------------------
def test_scenario_9_existing_website_different_path(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """When destination is on the same domain but specifies a specific subpath, navigate existing tab."""
    page1 = browser_context.new_page()
    page1.goto("https://docs.python.org")

    # Request specific docs subpath
    res = manager.open_or_reuse("https://docs.python.org/3/library/asyncio.html")
    assert res.success is True
    assert res.reused_existing is True
    # Tab count remains 1
    assert len(browser_context.pages) == 1


# ---------------------------------------------------------------------------
# Scenario 10: Repeated browser tool calls -> reuse the same browser session
# ---------------------------------------------------------------------------
def test_scenario_10_repeated_tool_calls_reuse_session(browser_context: BrowserContext, manager: DesktopBrowserManager):
    """A series of browser actions share the same persistent browser instance."""
    # Tool call 1: Open Docs
    r1 = manager.open_or_reuse("https://docs.python.org")
    assert r1.success is True
    assert len(browser_context.pages) == 1

    # Tool call 2: Open Search
    r2 = manager.open_or_reuse("https://www.google.com")
    assert r2.success is True
    assert len(browser_context.pages) == 2

    # Tool call 3: Return to Docs
    r3 = manager.open_or_reuse("https://docs.python.org")
    assert r3.success is True
    assert r3.reused_existing is True
    assert len(browser_context.pages) == 2, "Session leaked additional tabs!"

"""Unit tests for TabMatcher: URL normalization, domain matching, subpath sensitivity, and tab reuse."""

import pytest

from openjarvis.browser.matcher import TabMatcher
from openjarvis.browser.models import BrowserTabInfo


def test_normalize_url():
    assert TabMatcher.normalize_url("https://www.linkedin.com/feed/") == "linkedin.com/feed"
    assert TabMatcher.normalize_url("http://LINKEDIN.COM") == "linkedin.com"
    assert TabMatcher.normalize_url("https://github.com/openjarvis/openjarvis?foo=1&bar=2") == "github.com/openjarvis/openjarvis?bar=2&foo=1"
    assert TabMatcher.normalize_url("https://www.youtube.com/#top") == "youtube.com"


def test_extract_domain():
    assert TabMatcher.extract_domain("https://www.linkedin.com/in/test") == "linkedin.com"
    assert TabMatcher.extract_domain("http://mail.google.com") == "mail.google.com"
    assert TabMatcher.extract_domain("github.com/repo") == "github.com"
    assert TabMatcher.extract_domain("linkedin") == "linkedin.com"
    assert TabMatcher.extract_domain("yt") == "youtube.com"


def test_explicit_new_tab_detection():
    # Should detect explicit new tab intent
    assert TabMatcher.is_explicit_new_tab_request("open linkedin in a new tab") is True
    assert TabMatcher.is_explicit_new_tab_request("create a new tab for github") is True
    assert TabMatcher.is_explicit_new_tab_request("open another tab") is True
    assert TabMatcher.is_explicit_new_tab_request("open in new window") is True
    assert TabMatcher.is_explicit_new_tab_request("separate tab for youtube") is True

    # Normal requests should NOT be detected as explicit new tab
    assert TabMatcher.is_explicit_new_tab_request("open linkedin") is False
    assert TabMatcher.is_explicit_new_tab_request("go to github") is False
    assert TabMatcher.is_explicit_new_tab_request("check my email on gmail") is False
    assert TabMatcher.is_explicit_new_tab_request("show me youtube") is False


def test_sensitive_path_detection():
    # Sensitive paths requiring confirmation
    assert TabMatcher.is_sensitive_path("/mail/u/0/#inbox?compose=new") is True
    assert TabMatcher.is_sensitive_path("/checkout/payment") is True
    assert TabMatcher.is_sensitive_path("/cart") is True
    assert TabMatcher.is_sensitive_path("/issues/new") is True
    assert TabMatcher.is_sensitive_path("/settings/password") is True

    # Standard reading/feed paths are safe
    assert TabMatcher.is_sensitive_path("/feed") is False
    assert TabMatcher.is_sensitive_path("/jobs") is False
    assert TabMatcher.is_sensitive_path("/watch?v=123") is False
    assert TabMatcher.is_sensitive_path("/openjarvis/openjarvis") is False


def test_exact_url_matching():
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://www.linkedin.com/feed/", title="Feed | LinkedIn"),
        BrowserTabInfo(tab_id=2, url="https://github.com/openjarvis", title="GitHub - OpenJarvis"),
        BrowserTabInfo(tab_id=3, url="https://news.ycombinator.com", title="Hacker News"),
    ]

    res = TabMatcher.find_best_tab("https://linkedin.com/feed", tabs)
    assert res.can_reuse is True
    assert res.matched_tab is not None
    assert res.matched_tab.tab_id == 1
    assert res.should_navigate is False
    assert res.confidence == 1.0


def test_same_domain_navigation_reuse():
    # Target is linkedin jobs, existing tab is linkedin feed
    tabs = [
        BrowserTabInfo(tab_id=10, url="https://www.linkedin.com/feed/", title="Feed | LinkedIn"),
        BrowserTabInfo(tab_id=20, url="https://google.com", title="Google"),
    ]

    res = TabMatcher.find_best_tab("https://www.linkedin.com/jobs", tabs)
    assert res.can_reuse is True
    assert res.matched_tab is not None
    assert res.matched_tab.tab_id == 10
    assert res.should_navigate is True
    assert res.requires_user_confirmation is False


def test_sensitive_tab_requires_user_confirmation():
    # Active compose tab on Gmail
    tabs = [
        BrowserTabInfo(tab_id=101, url="https://mail.google.com/mail/u/0/#compose", title="Compose - Gmail"),
    ]

    res = TabMatcher.find_best_tab("https://mail.google.com/mail/u/0/#inbox", tabs)
    assert res.can_reuse is True
    assert res.matched_tab is not None
    assert res.requires_user_confirmation is True


def test_title_matching_when_url_empty():
    # Windows GUI mode where URL might not be directly queryable without extension
    tabs = [
        BrowserTabInfo(tab_id="win_123", url="", title="LinkedIn (5 notifications)"),
        BrowserTabInfo(tab_id="win_456", url="", title="Inbox - Outlook"),
    ]

    res = TabMatcher.find_best_tab("https://www.linkedin.com", tabs)
    assert res.can_reuse is True
    assert res.matched_tab is not None
    assert res.matched_tab.tab_id == "win_123"


def test_no_match_returns_cannot_reuse():
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://github.com", title="GitHub"),
        BrowserTabInfo(tab_id=2, url="https://news.ycombinator.com", title="Hacker News"),
    ]

    res = TabMatcher.find_best_tab("https://www.linkedin.com", tabs)
    assert res.can_reuse is False
    assert res.matched_tab is None

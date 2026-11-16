"""Intelligent tab matching, URL normalization, and subpath routing for desktop browser control."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from openjarvis.browser.models import BrowserTabInfo, TabMatchResult

# Common service and brand name mappings to canonical domains
SERVICE_DOMAIN_MAP = {
    "linkedin": "linkedin.com",
    "youtube": "youtube.com",
    "yt": "youtube.com",
    "gmail": "mail.google.com",
    "google mail": "mail.google.com",
    "github": "github.com",
    "google": "google.com",
    "google search": "google.com",
    "x": "x.com",
    "twitter": "x.com",
    "reddit": "reddit.com",
    "whatsapp": "web.whatsapp.com",
    "slack": "app.slack.com",
    "notion": "notion.so",
    "figma": "figma.com",
    "chatgpt": "chatgpt.com",
    "claude": "claude.ai",
    "amazon": "amazon.com",
    "netflix": "netflix.com",
    "spotify": "open.spotify.com",
    "stackoverflow": "stackoverflow.com",
    "stack overflow": "stackoverflow.com",
}

# Domain alias normalizations
DOMAIN_ALIASES = {
    "twitter.com": "x.com",
    "www.x.com": "x.com",
    "gmail.com": "mail.google.com",
    "www.gmail.com": "mail.google.com",
}

# Sensitive subpaths that should NOT be overwritten without confirmation (e.g. composing messages, checkout)
SENSITIVE_PATH_PATTERNS = [
    re.compile(r"/compose|\bcompose\b", re.IGNORECASE),
    re.compile(r"/messages?/\w+", re.IGNORECASE),
    re.compile(r"/checkout", re.IGNORECASE),
    re.compile(r"/cart", re.IGNORECASE),
    re.compile(r"/editor", re.IGNORECASE),
    re.compile(r"/new", re.IGNORECASE),
    re.compile(r"/edit", re.IGNORECASE),
    re.compile(r"/settings/(?:password|security|billing)", re.IGNORECASE),
]

# Explicit new tab or window intent keywords
EXPLICIT_NEW_TAB_REGEX = re.compile(
    r"\b(another\s+tab|new\s+tab|second\s+tab|separate\s+tab|different\s+tab|open\s+another|new\s+window|separate\s+window)\b",
    re.IGNORECASE,
)


class TabMatcher:
    """Matches requested URLs and user intents against open browser tabs."""

    @classmethod
    def is_explicit_new_tab_request(cls, text: str) -> bool:
        """Check if user explicitly requested creating a separate new tab or window."""
        if not text:
            return False
        return bool(EXPLICIT_NEW_TAB_REGEX.search(text))

    @classmethod
    def normalize_url(cls, url: str) -> str:
        """Normalize URL for consistent comparison.

        Strips schemes, www prefixes, default ports, trailing slashes.
        """
        if not url:
            return ""

        url = url.strip()

        # If bare domain or service name, e.g. "linkedin.com" or "linkedin"
        if not url.startswith(("http://", "https://", "file://", "chrome://", "edge://")):
            # Check service name lookup
            lower_name = url.lower().rstrip("/")
            if lower_name in SERVICE_DOMAIN_MAP:
                url = "https://" + SERVICE_DOMAIN_MAP[lower_name]
            elif "." not in url and not url.startswith("localhost"):
                # Treat single word as search or standard .com
                url = f"https://www.google.com/search?q={url}"
            else:
                url = "https://" + url

        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if netloc.endswith(":80"):
                netloc = netloc[:-3]
            elif netloc.endswith(":443"):
                netloc = netloc[:-4]

            # Apply domain alias
            netloc = DOMAIN_ALIASES.get(netloc, netloc)

            path = parsed.path.rstrip("/")
            # Keep query string sorted if present
            query_str = ""
            if parsed.query:
                params = parse_qs(parsed.query, keep_blank_values=True)
                sorted_pairs = []
                for k in sorted(params.keys()):
                    for v in sorted(params[k]):
                        sorted_pairs.append(f"{k}={v}")
                query_str = "?" + "&".join(sorted_pairs)

            frag_str = ""
            if parsed.fragment and parsed.fragment.lower() not in ("top", "bottom", "main", "#", ""):
                frag_str = f"#{parsed.fragment}"

            normalized = f"{netloc}{path}{query_str}{frag_str}"
            return normalized.lower()
        except Exception:
            return url.lower().strip()

    @classmethod
    def extract_domain(cls, url: str) -> str:
        """Extract the registered base domain from a URL or service name."""
        if not url:
            return ""

        url_str = url.strip().lower()
        if url_str in SERVICE_DOMAIN_MAP:
            return SERVICE_DOMAIN_MAP[url_str]

        if not url_str.startswith(("http://", "https://")):
            url_str = "https://" + url_str

        try:
            parsed = urlparse(url_str)
            domain = parsed.netloc
            if domain.startswith("www."):
                domain = domain[4:]
            if ":" in domain:
                domain = domain.split(":")[0]
            return DOMAIN_ALIASES.get(domain, domain)
        except Exception:
            return ""

    @classmethod
    def is_sensitive_path(cls, url: str) -> bool:
        """Check if tab is currently on a page with active work (messaging, compose, checkout)."""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            full_check = f"{parsed.path} {parsed.query} {parsed.fragment} {url}"
            return any(pat.search(full_check) for pat in SENSITIVE_PATH_PATTERNS)
        except Exception:
            return False

    @classmethod
    def calculate_match_score(
        cls,
        target_url: str,
        tab: BrowserTabInfo,
        task_context: str = "",
    ) -> Tuple[float, str]:
        """Compute matching score (0.0 to 1.0) and descriptive reason for a candidate tab."""
        target_norm = cls.normalize_url(target_url)
        tab_norm = cls.normalize_url(tab.url)

        # 1. Exact normalized URL match
        if target_norm and tab_norm and target_norm == tab_norm:
            return 1.0, f"Exact URL match: {tab.url}"

        target_domain = cls.extract_domain(target_url)
        tab_domain = tab.domain or cls.extract_domain(tab.url)

        # 2. Same domain comparison
        if target_domain and tab_domain and (target_domain == tab_domain or target_domain in tab_domain or tab_domain in target_domain):
            # Check if target is just a root/landing request (e.g. "open linkedin" or "linkedin.com")
            target_path = urlparse("https://" + target_norm if "://" not in target_norm else target_norm).path
            tab_path = urlparse("https://" + tab_norm if "://" not in tab_norm else tab_norm).path

            # If user requested base site (e.g. linkedin.com) and tab is on that site (e.g. linkedin.com/feed)
            if not target_path or target_path in ("/", ""):
                score = 0.90
                # Active tab gets a slight priority boost
                if tab.is_active:
                    score += 0.05
                return score, f"Matching site: {target_domain} (tab on {tab.title or tab_path})"

            # If target has a subpath and tab is on the exact same subpath
            if target_path and tab_path and target_path == tab_path:
                return 0.95, f"Matching subpath: {target_domain}{target_path}"

            # Both belong to the same website, but different subpaths (e.g. /jobs vs /feed)
            score = 0.80
            if tab.is_active:
                score += 0.05
            return score, f"Same domain {target_domain}, different subpath ({tab_path} vs {target_path})"

        # 3. Title-based matching
        tab_title_lower = (tab.title or "").lower()
        target_clean = re.sub(r"https?://(www\.)?", "", target_url).lower().split("/")[0]

        # Check if brand name from target is in title
        for brand, domain in SERVICE_DOMAIN_MAP.items():
            if (brand in target_clean or domain in target_domain) and brand in tab_title_lower:
                return 0.85, f"Title match for '{brand}': {tab.title}"

        # General keyword match between title and target
        words = [w for w in re.split(r"[^a-zA-Z0-9]+", target_clean) if len(w) > 3]
        if words and any(w in tab_title_lower for w in words):
            return 0.60, f"Title keyword match: {tab.title}"

        return 0.0, "No match"

    @classmethod
    def find_best_tab(
        cls,
        destination: str,
        tabs: List[BrowserTabInfo],
        task_context: str = "",
        min_threshold: float = 0.65,
    ) -> TabMatchResult:
        """Find the best existing tab to activate or reuse for the destination."""
        if not tabs or not destination:
            return TabMatchResult(matched_tab=None, score=0.0, reason="No tabs or destination provided")

        best_tab: Optional[BrowserTabInfo] = None
        best_score = 0.0
        best_reason = ""

        for tab in tabs:
            score, reason = cls.calculate_match_score(destination, tab, task_context)
            if score > best_score:
                best_score = score
                best_tab = tab
                best_reason = reason

        if best_score < min_threshold or best_tab is None:
            return TabMatchResult(
                matched_tab=None,
                score=best_score,
                reason=f"No matching tab exceeded threshold {min_threshold} (best: {best_score:.2f})",
                can_reuse=False,
            )

        target_norm = cls.normalize_url(destination)
        tab_norm = cls.normalize_url(best_tab.url)

        # Determine whether navigation is needed
        target_path = urlparse("https://" + target_norm if "://" not in target_norm else target_norm).path
        is_root_destination = not target_path or target_path in ("/", "")

        # If user asked for general site, do NOT navigate away from current subpage (e.g. keep /feed)
        if is_root_destination:
            should_navigate = False
        else:
            should_navigate = (target_norm != tab_norm)

        # Check for sensitive path that shouldn't be blindly overwritten
        is_sensitive = cls.is_sensitive_path(best_tab.url) and should_navigate

        return TabMatchResult(
            matched_tab=best_tab,
            score=best_score,
            reason=best_reason,
            can_reuse=True,
            should_navigate=should_navigate,
            requires_user_confirmation=is_sensitive,
        )

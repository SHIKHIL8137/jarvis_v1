"""Chrome DevTools Protocol (CDP) client for direct browser automation without extensions."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from openjarvis.browser.models import BrowserTabInfo

logger = logging.getLogger(__name__)


class CDPBridge:
    """Connects to Chrome or Edge via remote debugging port (e.g. http://127.0.0.1:9222)."""

    def __init__(self, port: int = 9222, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"

    def is_available(self, timeout: float = 1.0) -> bool:
        """Check whether Chrome/Edge DevTools endpoint is listening."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/version", headers={"User-Agent": "Jarvis"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_browser_version(self, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """Return browser version information from DevTools."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/version", headers={"User-Agent": "Jarvis"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("CDP version check failed: %s", exc)
            return None

    def list_tabs(self, timeout: float = 3.0) -> List[BrowserTabInfo]:
        """Enumerate active pages/tabs via DevTools endpoint."""
        tabs: List[BrowserTabInfo] = []
        try:
            req = urllib.request.Request(f"{self.base_url}/json/list", headers={"User-Agent": "Jarvis"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for item in data:
                # We only want visible web pages, not background service workers or extensions
                if item.get("type") != "page":
                    continue

                target_id = item.get("id", "")
                url = item.get("url", "")
                title = item.get("title", "")

                domain = ""
                if url:
                    try:
                        p = urllib.parse.urlparse(url)
                        domain = p.netloc.lower()
                        if domain.startswith("www."):
                            domain = domain[4:]
                    except Exception:
                        pass

                tabs.append(
                    BrowserTabInfo(
                        tab_id=target_id,
                        window_id=0,
                        url=url,
                        title=title,
                        domain=domain,
                        is_active=False,  # DevTools doesn't track active flag in /json/list
                        fav_icon_url=item.get("faviconUrl", ""),
                    )
                )
        except Exception as exc:
            logger.debug("CDP list tabs failed: %s", exc)

        return tabs

    def activate_tab(self, target_id: str, timeout: float = 3.0) -> bool:
        """Activate and focus a tab via DevTools endpoint."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/json/activate/{target_id}",
                headers={"User-Agent": "Jarvis"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.debug("CDP activate tab failed: %s", exc)
            return False

    def create_tab(self, url: str, timeout: float = 3.0) -> Optional[BrowserTabInfo]:
        """Open a new tab via DevTools endpoint."""
        try:
            encoded_url = urllib.parse.quote(url, safe="")
            req = urllib.request.Request(
                f"{self.base_url}/json/new?{encoded_url}",
                headers={"User-Agent": "Jarvis"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                target_id = data.get("id", "")
                return BrowserTabInfo(
                    tab_id=target_id,
                    url=url,
                    title=data.get("title", ""),
                    is_active=True,
                )
        except Exception as exc:
            logger.debug("CDP create tab failed: %s", exc)
            return None

    def close_tab(self, target_id: str, timeout: float = 3.0) -> bool:
        """Close a tab via DevTools endpoint."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/json/close/{target_id}",
                headers={"User-Agent": "Jarvis"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.debug("CDP close tab failed: %s", exc)
            return False

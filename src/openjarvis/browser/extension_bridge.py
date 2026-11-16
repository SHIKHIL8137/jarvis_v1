"""Secure local communication bridge between Jarvis and the desktop browser extension."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from openjarvis.browser.models import BrowserTabInfo, BrowserWindowInfo
from openjarvis.core.paths import get_config_dir

logger = logging.getLogger(__name__)


def _write_token_securely(token_file, token: str) -> None:
    """Persist the extension token with restrictive file permissions on Windows/POSIX."""
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
        try:
            os.chmod(token_file, 0o600)
        except Exception:
            pass

        # Apply Windows ACL to restrict file access to current user only
        user = os.environ.get("USERNAME")
        if user and os.name == "nt":
            subprocess.run(
                ["icacls", str(token_file), "/inheritance:r", "/grant:r", f"{user}:F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception as exc:
        logger.debug("Could not set restrictive permissions on browser extension token: %s", exc)


def _get_or_create_browser_token() -> str:
    """Retrieve or generate the persistent security token for browser extension pairing."""
    token_file = get_config_dir() / "browser_extension_token.key"
    try:
        if token_file.exists():
            val = token_file.read_text(encoding="utf-8").strip()
            if len(val) >= 24:
                return val
    except Exception:
        pass

    new_token = secrets.token_urlsafe(32)
    _write_token_securely(token_file, new_token)
    return new_token


class ExtensionBridge:
    """Manages real-time communication with the Jarvis Chrome/Edge browser extension."""

    def __init__(self) -> None:
        self.token = _get_or_create_browser_token()
        self._connected = False
        self._last_heartbeat = time.time()
        self._tabs_cache: List[BrowserTabInfo] = []
        self._windows_cache: List[BrowserWindowInfo] = []
        self._active_ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_commands: Dict[str, asyncio.Future[Any]] = {}
        self._command_counter = 0
        self._state_callbacks: List[Callable[[bool], None]] = []

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Event loop where WebSocket connection lives."""
        return self._loop

    @property
    def is_connected(self) -> bool:
        """Check if browser extension is actively connected and heartbeat is fresh (within 35s)."""
        if not self._connected:
            return False
        if self._last_heartbeat > 0 and time.time() - self._last_heartbeat > 35.0:
            self._connected = False
            return False
        return True

    def register_state_callback(self, cb: Callable[[bool], None]) -> None:
        self._state_callbacks.append(cb)

    def validate_token(self, provided_token: Optional[str]) -> bool:
        """Verify token matches the authorized pairing secret using constant-time comparison."""
        if not provided_token or not self.token:
            return False
        return secrets.compare_digest(provided_token.strip(), self.token.strip())

    def regenerate_token(self) -> str:
        """Regenerate pairing token, invalidate current connection, and persist securely."""
        new_token = secrets.token_urlsafe(32)
        token_file = get_config_dir() / "browser_extension_token.key"
        _write_token_securely(token_file, new_token)
        self.token = new_token

        # Terminate any active connection so it must re-pair
        if self._active_ws is not None:
            try:
                loop = self._loop or asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._active_ws.close(code=1008))
            except Exception:
                pass
            self.on_ws_disconnect()
        return new_token

    def update_tabs(self, raw_tabs: List[Dict[str, Any]]) -> None:
        """Update cached tab and window information from extension payload."""
        self._last_heartbeat = time.time()
        self._connected = True

        new_tabs: List[BrowserTabInfo] = []
        windows_map: Dict[str | int, List[BrowserTabInfo]] = {}

        for item in raw_tabs:
            tab_id = item.get("id") or item.get("tab_id")
            if tab_id is None:
                continue

            window_id = item.get("windowId") or item.get("window_id", 0)
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            is_active = bool(item.get("active", False))
            fav_icon = str(item.get("favIconUrl", ""))

            # Extract domain
            domain = ""
            if url:
                try:
                    p = urlparse(url)
                    domain = p.netloc.lower()
                    if domain.startswith("www."):
                        domain = domain[4:]
                except Exception:
                    pass

            tab_info = BrowserTabInfo(
                tab_id=tab_id,
                window_id=window_id,
                url=url,
                title=title,
                domain=domain,
                is_active=is_active,
                fav_icon_url=fav_icon,
                last_accessed=time.time() if is_active else 0.0,
            )
            new_tabs.append(tab_info)
            windows_map.setdefault(window_id, []).append(tab_info)

        self._tabs_cache = new_tabs
        self._windows_cache = [
            BrowserWindowInfo(
                window_id=wid,
                is_focused=any(t.is_active for t in wtabs),
                tabs=wtabs,
            )
            for wid, wtabs in windows_map.items()
        ]

    def remove_tab_from_cache(self, tab_id: str | int) -> None:
        """Purge a stale or closed tab ID from local cache."""
        self._tabs_cache = [t for t in self._tabs_cache if str(t.tab_id) != str(tab_id)]

    def get_tabs(self) -> List[BrowserTabInfo]:
        """Return the current cached list of open browser tabs."""
        return list(self._tabs_cache)

    def get_windows(self) -> List[BrowserWindowInfo]:
        """Return the current cached list of open browser windows."""
        return list(self._windows_cache)

    def get_active_tab(self) -> Optional[BrowserTabInfo]:
        """Return the tab that is currently active/focused in the browser."""
        for t in self._tabs_cache:
            if t.is_active:
                return t
        return self._tabs_cache[0] if self._tabs_cache else None

    async def send_command(self, action: str, params: Dict[str, Any], timeout: float = 8.0) -> Any:
        """Send a command to the browser extension and await confirmation response."""
        if not self.is_connected or self._active_ws is None:
            raise ConnectionError("Browser extension is not connected")

        self._command_counter += 1
        cmd_id = f"cmd_{self._command_counter}_{int(time.time()*1000)}"

        payload = {
            "id": cmd_id,
            "action": action,
            "params": params,
        }

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop or asyncio.get_event_loop()

        future: asyncio.Future[Any] = loop.create_future()
        self._pending_commands[cmd_id] = future

        try:
            await self._active_ws.send_text(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Extension command '{action}' timed out after {timeout}s")
        except Exception:
            raise
        finally:
            self._pending_commands.pop(cmd_id, None)

    def handle_message(self, message_str: str) -> Optional[Dict[str, Any]]:
        """Process an incoming message from the browser extension."""
        self._last_heartbeat = time.time()
        self._connected = True

        try:
            data = json.loads(message_str)
        except Exception as exc:
            logger.debug("Failed to decode extension message: %s", exc)
            return None

        msg_type = data.get("type")

        # 1. Heartbeat or full tab report
        if msg_type == "tabs_report" or "tabs" in data:
            tabs = data.get("tabs", [])
            self.update_tabs(tabs)
            return {"status": "ok", "tab_count": len(tabs)}

        # 2. Command response
        if msg_type == "command_response" or "cmd_id" in data:
            cmd_id = data.get("cmd_id") or data.get("id")

            # If response contains reconciled tab state, update immediately
            if "tabs" in data and isinstance(data["tabs"], list):
                self.update_tabs(data["tabs"])

            # If stale tab ID was reported, remove from cache
            if data.get("stale_tab_id"):
                self.remove_tab_from_cache(data["stale_tab_id"])

            if cmd_id in self._pending_commands:
                fut = self._pending_commands[cmd_id]
                if not fut.done():
                    if data.get("success", True):
                        fut.set_result(data.get("result"))
                    else:
                        fut.set_exception(RuntimeError(data.get("error", "Extension command failed")))
            return {"status": "acknowledged"}

        # 3. Tab activated event
        if msg_type == "tab_activated":
            tab_id = data.get("tab_id")
            for t in self._tabs_cache:
                t.is_active = (str(t.tab_id) == str(tab_id))
            return {"status": "updated"}

        return {"status": "received"}

    def on_ws_connect(self, ws: Any, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Handle new WebSocket connection from extension."""
        self._active_ws = ws
        self._connected = True
        self._last_heartbeat = time.time()
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = loop

        for cb in self._state_callbacks:
            try:
                cb(True)
            except Exception:
                pass

    def on_ws_disconnect(self) -> None:
        """Handle WebSocket disconnection."""
        self._active_ws = None
        self._connected = False
        for cb in self._state_callbacks:
            try:
                cb(False)
            except Exception:
                pass
        # Fail any pending commands
        for cmd_id, fut in list(self._pending_commands.items()):
            if not fut.done():
                fut.set_exception(ConnectionError("Browser extension disconnected"))
        self._pending_commands.clear()

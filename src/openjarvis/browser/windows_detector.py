"""Windows process detection, installation paths, and window foreground management for desktop browsers."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Standard installation paths on Windows
CHROME_CANDIDATE_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

EDGE_CANDIDATE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]

BRAVE_CANDIDATE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
]


class WindowsBrowserDetector:
    """Manages Windows browser detection, process inspection, and window activation."""

    @classmethod
    def get_installed_browsers(cls) -> Dict[str, str]:
        """Detect installed desktop browsers on Windows, returning {browser_name: executable_path}."""
        browsers: Dict[str, str] = {}

        # 1. Google Chrome
        for path in CHROME_CANDIDATE_PATHS:
            if os.path.isfile(path):
                browsers["chrome"] = path
                break

        # 2. Microsoft Edge
        for path in EDGE_CANDIDATE_PATHS:
            if os.path.isfile(path):
                browsers["edge"] = path
                break

        # 3. Brave
        for path in BRAVE_CANDIDATE_PATHS:
            if os.path.isfile(path):
                browsers["brave"] = path
                break

        return browsers

    @classmethod
    def get_default_browser_path(cls) -> Optional[str]:
        """Return the primary detected browser executable on Windows (Chrome preferred, then Edge)."""
        installed = cls.get_installed_browsers()
        return installed.get("chrome") or installed.get("edge") or installed.get("brave")

    @classmethod
    def get_running_browsers(cls) -> List[Dict[str, Any]]:
        """Detect currently running browser processes on Windows."""
        running: List[Dict[str, Any]] = []

        # Use psutil if available, otherwise fallback to tasklist
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name"]):
                name = (proc.info.get("name") or "").lower()
                pid = proc.info.get("pid")
                if name in ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"):
                    browser_type = name.replace(".exe", "")
                    running.append({"name": name, "pid": pid, "type": browser_type})
        except ImportError:
            # Fallback to tasklist on Windows
            if sys.platform == "win32":
                try:
                    output = subprocess.run(
                        ["tasklist", "/FO", "CSV", "/NH"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    ).stdout
                    for line in output.splitlines():
                        parts = [p.strip('"') for p in line.split('","')]
                        if parts:
                            pname = parts[0].lower()
                            if pname in ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"):
                                pid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                                running.append({"name": pname, "pid": pid, "type": pname.replace(".exe", "")})
                except Exception as exc:
                    logger.debug("Tasklist inspection failed: %s", exc)

        return running

    @classmethod
    def is_browser_running(cls, browser_type: str = "") -> bool:
        """Check if any supported browser or specific browser is currently running."""
        running = cls.get_running_browsers()
        if not running:
            return False
        if not browser_type:
            return len(running) > 0
        return any(b.get("type") == browser_type.lower() for b in running)

    @classmethod
    def get_browser_windows(cls) -> List[Dict[str, Any]]:
        """Enumerate visible browser windows on Windows with their HWND, PID, and Title."""
        if sys.platform != "win32":
            return []

        windows: List[Dict[str, Any]] = []
        try:
            import win32gui
            import win32process

            running_pids = {b["pid"] for b in cls.get_running_browsers()}

            def enum_handler(hwnd: int, _: Any) -> None:
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd).strip()
                    if title:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if pid in running_pids or any(
                            tag in title.lower()
                            for tag in ("google chrome", "microsoft edge", "brave", "mozilla firefox")
                        ):
                            windows.append({"hwnd": hwnd, "pid": pid, "title": title})

            win32gui.EnumWindows(enum_handler, None)
        except Exception as exc:
            logger.debug("Windows window enumeration error: %s", exc)

        return windows

    @classmethod
    def bring_window_to_foreground(cls, hwnd: int) -> bool:
        """Bring a specific window handle to the foreground on Windows."""
        if sys.platform != "win32" or not hwnd:
            return False

        try:
            import win32con
            import win32gui

            # If window is minimized, restore it
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            # Bring to front
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception as exc:
            logger.debug("Failed to set foreground window for HWND %s: %s", hwnd, exc)
            return False

    @classmethod
    def bring_browser_window_matching_title_to_foreground(cls, title_keyword: str) -> bool:
        """Find a browser window matching a title keyword and bring it to the foreground."""
        windows = cls.get_browser_windows()
        keyword_lower = title_keyword.lower()

        # Find best matching window
        for win in windows:
            if keyword_lower in win["title"].lower():
                return cls.bring_window_to_foreground(win["hwnd"])

        # Fallback to any open browser window
        if windows:
            return cls.bring_window_to_foreground(windows[0]["hwnd"])

        return False

    @classmethod
    def launch_browser(cls, url: str, browser_path: Optional[str] = None) -> bool:
        """Launch desktop browser with the target URL."""
        exe = browser_path or cls.get_default_browser_path()
        try:
            if exe and os.path.isfile(exe):
                subprocess.Popen([exe, url], close_fds=True)
                return True
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception as exc:
            logger.error("Failed to launch desktop browser: %s", exc)
            return False

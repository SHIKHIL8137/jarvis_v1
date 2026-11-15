"""Desktop tools: open apps/URLs, read the screen (OCR), click/type, focus windows.

Opening things and reading the screen run freely. Clicking, typing and key
presses need confirmation unless "desktop control" is switched on (Settings),
see ``openjarvis.tools.approval_store.ui_confirm``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote_plus, urlparse

from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# --- desktop-control switch (persistent) ------------------------------------


def _flag_file() -> Path:
    return get_config_dir() / "desktop_control.json"


def control_enabled() -> bool:
    try:
        return bool(json.loads(_flag_file().read_text(encoding="utf-8")).get("enabled"))
    except (OSError, ValueError):
        return False


def set_control_enabled(enabled: bool) -> None:
    f = _flag_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"enabled": bool(enabled)}), encoding="utf-8")


# --- helpers ----------------------------------------------------------------

# Typing into these windows is refused outright: ask the user to do it.
_SENSITIVE_WINDOW = re.compile(
    r"password|sign.?in|log.?in|bank|wallet|payment|checkout|credit card|2fa|otp|vault",
    re.I,
)
_ocr_engine = None


def _fail(name: str, msg: str) -> ToolResult:
    return ToolResult(tool_name=name, content=msg, success=False)


def _ok(name: str, msg: str, **meta: Any) -> ToolResult:
    return ToolResult(tool_name=name, content=msg, success=True, metadata=meta)


def _foreground_title() -> str:
    import win32gui

    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def _grab(region: Optional[dict] = None) -> "Any":
    import mss
    from PIL import Image

    with mss.mss() as sct:
        mon = region or sct.monitors[1]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), mon


def _ocr(img) -> List[dict]:
    """OCR an image; returns [{text, x, y}] with centre coordinates in image px."""
    global _ocr_engine
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    result, _ = _ocr_engine(np.array(img))
    out = []
    for box, text, conf in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append(
            {
                "text": text,
                "x": int(sum(xs) / 4),
                "y": int(sum(ys) / 4),
                "conf": round(float(conf), 2),
            }
        )
    return out


def _find_text(lines: List[dict], needle: str) -> Optional[dict]:
    needle = needle.lower()
    hits = [l for l in lines if needle in l["text"].lower()]
    return min(hits, key=lambda l: len(l["text"])) if hits else None


# --- tools ------------------------------------------------------------------


@ToolRegistry.register("open_app")
class OpenAppTool(BaseTool):
    tool_id = "open_app"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_app",
            description=(
                "Open an application on the user's PC by name (e.g. 'Docker Desktop',"
                " 'Notepad', 'Chrome', 'VS Code'). Searches the Start menu."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "App name."}},
                "required": ["name"],
            },
            category="desktop",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        name = str(params.get("name", "")).strip()
        if not name or re.search(r"[&|;<>`$\n\r]", name):
            return _fail("open_app", "Provide a plain app name.")
        try:
            raw = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                 "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            ).stdout
            apps = json.loads(raw) if raw.strip() else []
            apps = [apps] if isinstance(apps, dict) else apps
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return _fail("open_app", f"Could not search apps: {exc}")
        want = name.lower()
        # Best match wins: exact name, then name starting with it, then containing it.
        for test in (lambda n: n == want, lambda n: n.startswith(want), lambda n: want in n):
            hits = sorted((a for a in apps if test(a["Name"].lower())), key=lambda a: len(a["Name"]))
            if hits:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{hits[0]['AppID']}"])
                return _ok("open_app", f"Launched {hits[0]['Name']}.", app=hits[0]["Name"])
        try:  # plain executables on PATH (notepad, calc, code, ...)
            os.startfile(name)  # type: ignore[attr-defined]
            return _ok("open_app", f"Launched {name}.", app=name)
        except OSError:
            return _fail("open_app", f"No app matching '{name}' found.")


@ToolRegistry.register("open_url")
class OpenUrlTool(BaseTool):
    tool_id = "open_url"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_url",
            description=(
                "Open a web page, or run a Google search, in the user's default browser."
                " Give either url or query."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL."},
                    "query": {"type": "string", "description": "Google search text."},
                },
            },
            category="desktop",
            timeout_seconds=15.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query", "")).strip()
        url = str(params.get("url", "")).strip()
        if query:
            url = "https://www.google.com/search?q=" + quote_plus(query)
        if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
            return _fail("open_url", "Only http(s) URLs can be opened.")
        webbrowser.open(url)
        return _ok("open_url", f"Opened {url}", url=url)


@ToolRegistry.register("screen_ocr")
class ScreenOcrTool(BaseTool):
    tool_id = "screen_ocr"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="screen_ocr",
            description=(
                "Take a screenshot of the primary screen and read all visible text with"
                " OCR. Returns each text with its screen x,y so you can click it with"
                " desktop_click. Also saves the screenshot."
            ),
            parameters={"type": "object", "properties": {}},
            category="desktop",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            img, mon = _grab()
            lines = _ocr(img)
        except Exception as exc:  # OCR/capture backends vary by machine
            return _fail("screen_ocr", f"Screen capture/OCR failed: {exc}")
        cap = get_config_dir() / "captures"
        cap.mkdir(parents=True, exist_ok=True)
        path = cap / f"screen_{int(time.time())}.png"
        img.save(path)
        rows = "\n".join(f"({l['x'] + mon['left']},{l['y'] + mon['top']}) {l['text']}" for l in lines[:150])
        return _ok(
            "screen_ocr",
            f"Active window: {_foreground_title()!r}\nScreenshot: {path}\nText on screen:\n{rows}",
            screenshot=str(path),
        )


@ToolRegistry.register("desktop_click")
class DesktopClickTool(BaseTool):
    tool_id = "desktop_click"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="desktop_click",
            description=(
                "Click on the screen: either at x,y, or on the on-screen text given in"
                " 'text' (found via OCR). Use screen_ocr first to see what is there."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "text": {"type": "string", "description": "Visible text to click."},
                    "double": {"type": "boolean"},
                },
            },
            category="desktop",
            requires_confirmation=True,
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        import pyautogui

        x, y = params.get("x"), params.get("y")
        text = str(params.get("text", "")).strip()
        if text:
            try:
                img, mon = _grab()
                hit = _find_text(_ocr(img), text)
            except Exception as exc:
                return _fail("desktop_click", f"OCR failed: {exc}")
            if not hit:
                return _fail("desktop_click", f"Text '{text}' not found on screen.")
            x, y = hit["x"] + mon["left"], hit["y"] + mon["top"]
        if x is None or y is None:
            return _fail("desktop_click", "Give x and y, or text.")
        w, h = pyautogui.size()
        if not (0 <= int(x) < w and 0 <= int(y) < h):
            return _fail("desktop_click", f"({x},{y}) is off screen ({w}x{h}).")
        (pyautogui.doubleClick if params.get("double") else pyautogui.click)(int(x), int(y))
        return _ok("desktop_click", f"Clicked ({x},{y}).")


@ToolRegistry.register("desktop_type")
class DesktopTypeTool(BaseTool):
    tool_id = "desktop_type"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="desktop_type",
            description="Type text into the focused window; optionally press Enter after.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean"},
                },
                "required": ["text"],
            },
            category="desktop",
            requires_confirmation=True,
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        import pyautogui

        title = _foreground_title()
        if _SENSITIVE_WINDOW.search(title):
            return _fail(
                "desktop_type",
                f"Refusing to type into a sensitive window ({title!r}). Ask the user to do it.",
            )
        pyautogui.write(str(params.get("text", "")), interval=0.01)
        if params.get("press_enter"):
            pyautogui.press("enter")
        return _ok("desktop_type", f"Typed text into {title!r}.")


@ToolRegistry.register("desktop_key")
class DesktopKeyTool(BaseTool):
    tool_id = "desktop_key"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="desktop_key",
            description="Press a key or combo, e.g. 'enter', 'tab', 'ctrl+l', 'win', 'alt+tab'.",
            parameters={
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
            category="desktop",
            requires_confirmation=True,
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        import pyautogui

        keys = [k.strip().lower() for k in str(params.get("keys", "")).split("+") if k.strip()]
        if not keys or any(k not in pyautogui.KEYBOARD_KEYS for k in keys):
            return _fail("desktop_key", f"Unknown key in {params.get('keys')!r}.")
        pyautogui.hotkey(*keys)
        return _ok("desktop_key", f"Pressed {'+'.join(keys)}.")


@ToolRegistry.register("window_manage")
class WindowManageTool(BaseTool):
    tool_id = "window_manage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="window_manage",
            description="List open windows, or bring one to the front by (part of) its title.",
            parameters={
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "description": "Title text to focus."},
                },
            },
            category="desktop",
            timeout_seconds=15.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        import win32con
        import win32gui

        wins = []
        win32gui.EnumWindows(
            lambda h, _: wins.append((h, win32gui.GetWindowText(h)))
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h)
            else None,
            None,
        )
        target = str(params.get("focus", "")).strip().lower()
        if not target:
            return _ok("window_manage", "\n".join(t for _, t in wins))
        for h, t in wins:
            if target in t.lower():
                if win32gui.IsIconic(h):
                    win32gui.ShowWindow(h, win32con.SW_RESTORE)
                try:
                    win32gui.SetForegroundWindow(h)
                except Exception as exc:  # Windows may block focus stealing
                    return _fail("window_manage", f"Could not focus {t!r}: {exc}")
                return _ok("window_manage", f"Focused {t!r}.")
        return _fail("window_manage", f"No window matching '{target}'.")

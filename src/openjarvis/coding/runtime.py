"""Shared helpers: engine selection, model choice, settings, project instructions."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.paths import get_config_dir

_DEFAULTS: Dict[str, Any] = {"max_rounds": 12, "subagent_depth": 1, "auto": True}


def goal_settings() -> Dict[str, Any]:
    """``[goal]`` from ~/.openjarvis/config.toml over defaults."""
    cfg = dict(_DEFAULTS)
    try:
        with open(get_config_dir() / "config.toml", "rb") as f:
            cfg.update(tomllib.load(f).get("goal", {}))
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return cfg


def engine_for(model: str) -> Any:
    """The engine that can serve *model* (cloud engine for cloud models)."""
    from openjarvis.core.config import load_config
    from openjarvis.engine._discovery import get_engine
    from openjarvis.server.cloud_router import is_cloud_model

    cfg = load_config()
    found = get_engine(cfg, "cloud") if is_cloud_model(model) else get_engine(cfg, model=model)
    if not found:
        raise RuntimeError(f"No engine is available for {model}.")
    return found[1]


def pick_model(kind: str) -> str:
    from openjarvis.routing.auto_router import model_for

    return model_for(kind)


# --- project instructions (AGENTS.md / CLAUDE.md) -----------------------------

_NAMES = ("AGENTS.md", "CLAUDE.md")
_MAX_INSTRUCTIONS = 12_000


def project_dir_from(text: str) -> Optional[Path]:
    """A project folder mentioned in *text* (a Windows/POSIX absolute path that exists)."""
    for m in re.finditer(r"[A-Za-z]:[\\/][^\s\"'`<>|*?]+|/[\w.\-/]{3,}", text):
        p = Path(m.group(0).rstrip(".,;:)"))
        for cand in (p, p.parent):
            if cand.is_dir():
                return cand
    return None


def discover_instructions(start: Optional[Path]) -> str:
    """AGENTS.md/CLAUDE.md from *start* up to the root, nearest first, plus the global one."""
    found = []
    seen = set()
    cur = start.resolve() if start else None
    while cur is not None:
        for name in _NAMES:
            f = cur / name
            if f.is_file() and f not in seen:
                found.append(f)
                seen.add(f)
                break
        if cur.parent == cur:
            break
        cur = cur.parent
    g = get_config_dir() / "AGENTS.md"
    if g.is_file():
        found.append(g)
    out, total = [], 0
    for f in found:
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        chunk = f"Instructions from: {f}\n{text}"
        if total + len(chunk) > _MAX_INSTRUCTIONS:
            chunk = chunk[: max(0, _MAX_INSTRUCTIONS - total)] + "\n[truncated]"
        out.append(chunk)
        total += len(chunk)
        if total >= _MAX_INSTRUCTIONS:
            break
    return "\n\n".join(out)


def _brief(tool: str, args: dict) -> str:
    key = {"shell_exec": "command", "edit": "path", "file_write": "path", "apply_patch": "path", "file_read": "path",
           "grep": "pattern", "glob": "pattern", "subagent": "description", "web_search": "query",
           "git_commit": "message", "todo_write": ""}.get(tool, "")
    val = str(args.get(key, "")) if key else ""
    if tool == "subagent" and not val:
        val = str(args.get("agent", ""))
    return val.replace("\n", " ")[:90]


def tool_progress_bus(emit):
    """An EventBus that reports each tool call through ``emit("tool", name, brief)``."""
    from openjarvis.core.events import EventBus, EventType

    bus = EventBus()

    def on_start(event):
        data = getattr(event, "data", None) or {}
        emit("tool", str(data.get("tool", "")), _brief(str(data.get("tool", "")), data.get("arguments") or {}))

    bus.subscribe(EventType.TOOL_CALL_START, on_start)
    return bus

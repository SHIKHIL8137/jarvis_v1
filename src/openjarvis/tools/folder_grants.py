"""Persistent folder permissions: a granted folder is never asked about again.

Grants live in ``~/.openjarvis/folder_grants.json`` so they survive restarts and
apply to every chat. Revoke from Settings (or ``revoke``) to take one back.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import List

from openjarvis.core.paths import get_config_dir

_lock = threading.Lock()


def _file() -> Path:
    return get_config_dir() / "folder_grants.json"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(path)))


def list_grants() -> List[str]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return [g for g in data if isinstance(g, str)]
    except (OSError, ValueError):
        return []


def _save(grants: List[str]) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(grants, indent=2), encoding="utf-8")


def is_granted(path: str) -> bool:
    """True if *path* is a granted folder or lies inside one."""
    if not path:
        return False
    target = _norm(path)
    for g in list_grants():
        root = _norm(g)
        try:
            if os.path.commonpath([target, root]) == root:
                return True
        except ValueError:  # different drives
            continue
    return False


def grant(path: str) -> str:
    """Grant a folder (must exist, must not be a drive root). Returns the stored path."""
    resolved = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(resolved):
        raise ValueError(f"Not a folder: {path}")
    if os.path.dirname(resolved) == resolved:
        raise ValueError("Refusing to grant a whole drive; grant a project folder.")
    with _lock:
        grants = list_grants()
        if _norm(resolved) not in {_norm(g) for g in grants}:
            grants.append(resolved)
            _save(grants)
    return resolved


def revoke(path: str) -> bool:
    with _lock:
        grants = list_grants()
        kept = [g for g in grants if _norm(g) != _norm(path)]
        if len(kept) == len(grants):
            return False
        _save(kept)
        return True

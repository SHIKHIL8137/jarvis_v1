"""Default directory allow-list and path security for the filesystem tools."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Sequence

from openjarvis.core.paths import get_config_dir


def get_safe_default_dirs() -> List[Path]:
    """Return safe-by-default directories when none are explicitly configured.

    Includes the user configuration directory, current working directory,
    system temporary directory, and any directories explicitly granted by the user.
    """
    safe_dirs: list[Path] = [
        get_config_dir().resolve(),
        Path.cwd().resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    try:
        from openjarvis.tools.folder_grants import list_grants

        for grant in list_grants():
            if grant:
                p = Path(grant).resolve()
                if p not in safe_dirs:
                    safe_dirs.append(p)
    except Exception:
        pass

    return safe_dirs


def validate_allowed_dirs(allowed_dirs: object) -> List[str]:
    """Validate an active policy without interpreting None as an ambient default."""
    if not isinstance(allowed_dirs, (list, tuple)) or any(
        not isinstance(directory, (str, os.PathLike))
        or not isinstance(os.fspath(directory), str)
        or not os.fspath(directory).strip()
        for directory in allowed_dirs
    ):
        raise ValueError("security.allowed_dirs must be a list of non-empty paths")
    return [os.fspath(directory) for directory in allowed_dirs]


def file_tool_kwargs(name: str, config: Any = None) -> dict[str, List[str]]:
    """Bind native file tools to the caller's config when one was supplied."""
    if name not in {"file_read", "file_write"} or config is None:
        return {}
    return {"allowed_dirs": validate_allowed_dirs(config.security.allowed_dirs)}


def resolve_allowed_dirs(allowed_dirs: Optional[List[str]]) -> List[str]:
    """Return *allowed_dirs*, or ``[security] allowed_dirs`` from the config.

    An explicit empty list keeps the historical contract where tool-level
    safe-by-default enforcement applies. Invalid or unreadable configuration
    must raise immediately.
    """
    if allowed_dirs is None:
        from openjarvis.core.config import load_config

        allowed_dirs = load_config().security.allowed_dirs
    return validate_allowed_dirs(allowed_dirs)


# System-critical directories that must never be accessed even under loose policies
_CRITICAL_SYSTEM_DIRS: list[Path] = []
if os.name == "nt":
    for _env_var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)"):
        _val = os.environ.get(_env_var)
        if _val:
            try:
                _CRITICAL_SYSTEM_DIRS.append(Path(_val).resolve())
            except Exception:
                pass
else:
    for _dir_str in ("/etc", "/boot", "/sys", "/proc", "/root", "/sbin"):
        try:
            _CRITICAL_SYSTEM_DIRS.append(Path(_dir_str).resolve())
        except Exception:
            pass


def is_path_within_allowed(
    path: Path | str,
    allowed_dirs: Sequence[Path | str],
    *,
    safe_by_default: bool = True,
) -> bool:
    """Check if canonical resolved path is within allowed directories, protecting
    against symlink escapes and path traversal."""
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        return False

    # Block access to system-critical directories
    for sys_dir in _CRITICAL_SYSTEM_DIRS:
        try:
            if resolved == sys_dir or resolved.is_relative_to(sys_dir):
                return False
        except ValueError:
            continue

    effective_dirs: Sequence[Path] = [Path(d).resolve() for d in allowed_dirs]
    if not effective_dirs and safe_by_default:
        effective_dirs = get_safe_default_dirs()

    if not effective_dirs:
        return False

    for allowed in effective_dirs:
        try:
            if resolved == allowed or resolved.is_relative_to(allowed):
                return True
        except ValueError:
            continue

    return False

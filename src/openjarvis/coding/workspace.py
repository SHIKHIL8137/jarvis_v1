"""
Workspace Intelligence and Autonomous Coding Support.

Provides:
- Canonical workspace path resolution and strict containment enforcement.
- Incremental file inventory respecting .gitignore and skip patterns.
- Binary and large file guards.
- Project command discovery (test/build/lint) across Python, JS/TS, Rust, Make.
- Graceful AST symbol indexing for rapid code navigation.
- Project summary generation with caching and invalidation strategies.
- Isolated Git worktree management for safe experimentation.
- Change ledger recording modified files and unified diffs.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from openjarvis.security.file_policy import is_sensitive_file
from openjarvis.tools._paths import is_path_within_allowed, resolve_allowed_dirs

logger = logging.getLogger(__name__)

# Common build, cache, and vendor directories to skip
DEFAULT_SKIP_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    ".idea",
    ".vscode",
    ".tox",
    "coverage",
    ".coverage",
}

# Maximum single file size to inspect / index (1MB)
MAX_INSPECT_FILE_SIZE = 1_048_576

# Maximum number of files to index to prevent memory exhaustion
DEFAULT_MAX_FILES = 5_000


class WorkspaceError(Exception):
    """Base exception for workspace operations."""


class PathTraversalError(WorkspaceError):
    """Raised when an operation attempts to escape the authorized workspace."""


class SymlinkEscapeError(WorkspaceError):
    """Raised when a symlink points outside the authorized workspace."""


@dataclass
class FileEntry:
    rel_path: str
    abs_path: str
    size_bytes: int
    mtime: float
    is_binary: bool
    extension: str
    sha256: Optional[str] = None
    symbols: List[str] = field(default_factory=list)


@dataclass
class FileInventory:
    root: Path
    entries: Dict[str, FileEntry] = field(default_factory=dict)
    scanned_at: float = 0.0
    is_dirty: bool = False

    def get(self, rel_path: str) -> Optional[FileEntry]:
        norm = rel_path.replace("\\", "/").lstrip("/")
        return self.entries.get(norm)

    def search(
        self, query: str, extension: Optional[str] = None, limit: int = 50
    ) -> List[FileEntry]:
        results = []
        q = query.lower()
        ext = extension.lower() if extension else None
        for entry in self.entries.values():
            if ext and entry.extension.lower() != ext:
                continue
            if q in entry.rel_path.lower() or any(
                q in s.lower() for s in entry.symbols
            ):
                results.append(entry)
                if len(results) >= limit:
                    break
        return results


class WorkspaceManager:
    """Manages workspace containment, inventory, project commands, and change ledger."""

    _instance: Optional[WorkspaceManager] = None

    def __init__(
        self,
        workspace_root: Optional[Path | str] = None,
        allowed_dirs: Optional[List[str]] = None,
    ) -> None:
        self._allowed_dirs = [
            Path(d).expanduser().resolve()
            for d in resolve_allowed_dirs(allowed_dirs)
        ]
        self._workspace_root: Optional[Path] = None
        self._inventory: Optional[FileInventory] = None
        self._change_ledger: Dict[str, str] = (
            {}
        )  # rel_path -> diff / description
        self._cached_summary: Optional[Dict[str, Any]] = None
        self._summary_cached_at: float = 0.0

        if workspace_root:
            self.set_workspace_root(workspace_root)
        else:
            self.set_workspace_root(Path.cwd())

        WorkspaceManager._instance = self

    @classmethod
    def get_instance(cls) -> WorkspaceManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # --- Root Resolution & Containment --------------------------------------

    def set_workspace_root(self, root: Path | str) -> Path:
        resolved = Path(root).expanduser().resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace root must be an existing directory: {root}")

        # Check path containment against allowed_dirs policy if configured
        if self._allowed_dirs and not is_path_within_allowed(
            resolved, self._allowed_dirs
        ):
            raise PathTraversalError(
                f"Workspace root {resolved} is outside authorized directories."
            )

        self._workspace_root = resolved
        self._inventory = None
        self._cached_summary = None
        self._change_ledger.clear()
        return self._workspace_root

    @property
    def root(self) -> Path:
        if self._workspace_root is None:
            self._workspace_root = Path.cwd().resolve()
        return self._workspace_root

    def resolve_path(self, raw_path: Path | str, must_exist: bool = False) -> Path:
        """Resolve a path relative to workspace root and strictly enforce containment."""
        raw_str = str(raw_path).strip()
        if not raw_str:
            return self.root

        p = Path(raw_str).expanduser()
        if not p.is_absolute():
            p = self.root / p

        # Check for link escapes
        if p.is_symlink():
            try:
                target = p.resolve()
                if not is_path_within_allowed(target, [self.root]):
                    raise SymlinkEscapeError(
                        f"Symlink {raw_path} points outside authorized workspace: {target}"
                    )
            except (OSError, RuntimeError) as err:
                raise SymlinkEscapeError(
                    f"Cannot inspect symlink target {raw_path}: {err}"
                ) from err

        resolved = p.resolve()

        if is_sensitive_file(resolved):
            raise PathTraversalError(f"Access denied: {raw_path} is a protected sensitive file.")

        if not is_path_within_allowed(resolved, [self.root]):
            raise PathTraversalError(
                f"Path {raw_path} resolves outside authorized workspace root: {resolved}"
            )

        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Path does not exist: {resolved}")

        return resolved

    # --- Ignore Rules & File Inventory ---------------------------------------

    def _load_ignore_patterns(self) -> List[str]:
        patterns: List[str] = list(DEFAULT_SKIP_DIRS)
        gitignore = self.root / ".gitignore"
        if gitignore.is_file():
            try:
                with open(gitignore, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            patterns.append(line.rstrip("/"))
            except OSError:
                pass
        return patterns

    def _is_ignored(self, path: Path, ignore_patterns: List[str]) -> bool:
        try:
            rel = path.relative_to(self.root)
            parts = rel.parts
        except ValueError:
            return True

        for part in parts:
            if part in DEFAULT_SKIP_DIRS:
                return True

        rel_posix = rel.as_posix()
        for pat in ignore_patterns:
            if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(path.name, pat):
                return True
            if pat in parts:
                return True
        return False

    def build_inventory(
        self, force_refresh: bool = False, max_files: int = DEFAULT_MAX_FILES
    ) -> FileInventory:
        """Scan workspace and generate an incremental file inventory."""
        if (
            self._inventory is not None
            and not force_refresh
            and not self._inventory.is_dirty
        ):
            return self._inventory

        ignore_pats = self._load_ignore_patterns()
        entries: Dict[str, FileEntry] = {}
        count = 0

        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune skipped directories
            dirnames[:] = [
                d
                for d in dirnames
                if d not in DEFAULT_SKIP_DIRS
                and not self._is_ignored(Path(dirpath) / d, ignore_pats)
            ]

            for fname in filenames:
                file_path = Path(dirpath) / fname
                if self._is_ignored(file_path, ignore_pats):
                    continue

                try:
                    stat = file_path.stat()
                except OSError:
                    continue

                size = stat.st_size
                is_binary = False
                symbols: List[str] = []

                if size > MAX_INSPECT_FILE_SIZE:
                    is_binary = True  # treat oversized as binary for safety
                else:
                    try:
                        with open(file_path, "rb") as f:
                            chunk = f.read(1024)
                            if b"\x00" in chunk:
                                is_binary = True
                    except OSError:
                        is_binary = True

                # Extract code symbols if source file
                ext = file_path.suffix.lower()
                if not is_binary and ext in {".py", ".js", ".ts", ".tsx", ".jsx"}:
                    symbols = self._extract_symbols(file_path, ext)

                rel_str = file_path.relative_to(self.root).as_posix()
                entries[rel_str] = FileEntry(
                    rel_path=rel_str,
                    abs_path=str(file_path),
                    size_bytes=size,
                    mtime=stat.st_mtime,
                    is_binary=is_binary,
                    extension=ext,
                    symbols=symbols,
                )
                count += 1
                if count >= max_files:
                    logger.warning(
                        "Workspace inventory hit max file limit of %d", max_files
                    )
                    break
            if count >= max_files:
                break

        self._inventory = FileInventory(
            root=self.root,
            entries=entries,
            scanned_at=time.time(),
            is_dirty=False,
        )
        self._cached_summary = None
        return self._inventory

    def update_file_in_inventory(self, raw_path: Path | str) -> Optional[FileEntry]:
        """Incrementally update a single file record in the inventory."""
        if self._inventory is None:
            self.build_inventory()
            return self.get_file_entry(raw_path)

        path = self.resolve_path(raw_path)
        try:
            rel_str = path.relative_to(self.root).as_posix()
        except ValueError:
            return None

        if not path.exists():
            self._inventory.entries.pop(rel_str, None)
            self._inventory.is_dirty = True
            return None

        try:
            stat = path.stat()
            size = stat.st_size
            is_binary = False
            symbols: List[str] = []
            if size <= MAX_INSPECT_FILE_SIZE:
                with open(path, "rb") as f:
                    chunk = f.read(1024)
                    if b"\x00" in chunk:
                        is_binary = True
                ext = path.suffix.lower()
                if not is_binary and ext in {".py", ".js", ".ts", ".tsx", ".jsx"}:
                    symbols = self._extract_symbols(path, ext)
            else:
                is_binary = True
                ext = path.suffix.lower()

            entry = FileEntry(
                rel_path=rel_str,
                abs_path=str(path),
                size_bytes=size,
                mtime=stat.st_mtime,
                is_binary=is_binary,
                extension=ext,
                symbols=symbols,
            )
            self._inventory.entries[rel_str] = entry
            self._cached_summary = None
            return entry
        except OSError:
            return None

    def get_file_entry(self, raw_path: Path | str) -> Optional[FileEntry]:
        if self._inventory is None:
            self.build_inventory()
        path = self.resolve_path(raw_path)
        try:
            rel = path.relative_to(self.root).as_posix()
            return self._inventory.get(rel) if self._inventory else None
        except ValueError:
            return None

    # --- Code Symbol Extraction (AST / Regex) -------------------------------

    def _extract_symbols(self, file_path: Path, ext: str) -> List[str]:
        symbols: List[str] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return symbols

        if ext == ".py":
            try:
                tree = ast.parse(content, filename=str(file_path))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(f"def {node.name}")
                    elif isinstance(node, ast.ClassDef):
                        symbols.append(f"class {node.name}")
            except (SyntaxError, ValueError):
                # Graceful degradation on syntax error in target repo
                pass
        elif ext in {".js", ".ts", ".tsx", ".jsx"}:
            for match in re.finditer(
                r"(?:function|class|const|let|var)\s+([A-Za-z0-9_$]+)", content
            ):
                symbols.append(match.group(1))
        return symbols[:50]  # cap symbol count per file

    # --- Project Command Discovery ------------------------------------------

    def discover_project_commands(self) -> Dict[str, Any]:
        """Inspect workspace configuration files to discover build, test, and run commands."""
        commands: Dict[str, Any] = {
            "test_command": None,
            "build_command": None,
            "lint_command": None,
            "scripts": {},
            "ecosystem": "unknown",
        }

        # Check Node.js (package.json)
        pkg_json = self.root / "package.json"
        if pkg_json.is_file():
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    scripts = data.get("scripts", {})
                    commands["scripts"] = scripts
                    commands["ecosystem"] = "node"
                    if "test" in scripts:
                        commands["test_command"] = "npm test"
                    if "build" in scripts:
                        commands["build_command"] = "npm run build"
                    if "lint" in scripts:
                        commands["lint_command"] = "npm run lint"
            except (OSError, json.JSONDecodeError):
                pass

        # Check Python (pyproject.toml, pytest.ini, requirements.txt)
        pyproject = self.root / "pyproject.toml"
        if pyproject.is_file():
            commands["ecosystem"] = "python"
            try:
                import tomllib

                with open(pyproject, "rb") as f:
                    toml_data = tomllib.load(f)
                    if "tool" in toml_data and "pytest" in toml_data["tool"]:
                        commands["test_command"] = "pytest"
                    elif commands["test_command"] is None:
                        commands["test_command"] = "pytest"
            except Exception:
                commands["test_command"] = "pytest"
        elif (self.root / "pytest.ini").is_file() or (self.root / "setup.py").is_file():
            commands["ecosystem"] = "python"
            commands["test_command"] = "pytest"

        # Check Rust (Cargo.toml)
        if (self.root / "Cargo.toml").is_file():
            commands["ecosystem"] = "rust"
            commands["test_command"] = "cargo test"
            commands["build_command"] = "cargo build"

        # Check Makefile
        if (self.root / "Makefile").is_file() and not commands["test_command"]:
            commands["test_command"] = "make test"

        return commands

    # --- Project Summary & Context ------------------------------------------

    def get_project_summary(
        self, invalidate_cache: bool = False
    ) -> Dict[str, Any]:
        """Generate a project summary with language distribution, commands, and instructions."""
        now = time.time()
        if (
            self._cached_summary is not None
            and not invalidate_cache
            and (now - self._summary_cached_at < 300.0)
        ):
            return self._cached_summary

        inv = self.build_inventory()
        cmds = self.discover_project_commands()

        # Language breakdown
        ext_counts: Dict[str, int] = {}
        for entry in inv.entries.values():
            ext = entry.extension or "(no ext)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

        # Instructions discovery
        from openjarvis.coding.runtime import discover_instructions

        instructions = discover_instructions(self.root)

        summary = {
            "workspace_root": str(self.root),
            "generated_at": now,
            "total_files": len(inv.entries),
            "language_distribution": sorted(
                ext_counts.items(), key=lambda x: x[1], reverse=True
            )[:10],
            "commands": cmds,
            "has_instructions": bool(instructions),
            "instructions_excerpt": (
                instructions[:500] if instructions else "None found"
            ),
            "uncommitted_changes_count": len(self._change_ledger),
        }
        self._cached_summary = summary
        self._summary_cached_at = now
        return summary

    # --- Change Ledger & Git Worktrees --------------------------------------

    def record_change(self, raw_path: str | Path, diff_or_action: str) -> None:
        """Record a file modification in the session change ledger."""
        p = Path(raw_path).resolve()
        try:
            rel = p.relative_to(self.root).as_posix()
        except ValueError:
            rel = str(raw_path).replace("\\", "/").lstrip("/")
        self._change_ledger[rel] = diff_or_action
        self.update_file_in_inventory(rel)

    def get_change_ledger(self) -> Dict[str, str]:
        return dict(self._change_ledger)

    def clear_change_ledger(self) -> None:
        self._change_ledger.clear()

    def create_isolated_worktree(
        self, worktree_name: str, base_branch: str = "HEAD"
    ) -> Path:
        """Create a dedicated git worktree for parallel or high-risk modifications."""
        target_dir = self.root.parent / f"{self.root.name}-worktree-{worktree_name}"
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        res = subprocess.run(
            ["git", "worktree", "add", "-b", f"agent/{worktree_name}", str(target_dir), base_branch],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise WorkspaceError(
                f"Failed to create git worktree: {res.stderr.strip() or res.stdout.strip()}"
            )
        return target_dir

    def remove_isolated_worktree(self, worktree_dir: Path) -> None:
        """Remove a git worktree and clean up references."""
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_dir)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        )
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)

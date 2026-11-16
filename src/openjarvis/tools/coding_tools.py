"""Coding tools ported from opencode's design: exact edit, grep, glob, todo list.

* ``edit``  replaces exact text and retries leniently (typographic quotes/dashes/
  spaces, then trailing whitespace) so small model slips do not fail the edit;
  it refuses ambiguous matches unless ``replace_all`` is set, and keeps the
  file's CRLF/LF line endings.
* ``grep`` / ``glob`` search a project without shelling out.
* ``todo_write`` keeps the working plan the user can see.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._paths import resolve_allowed_dirs
from openjarvis.tools._stubs import BaseTool, ToolSpec

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
              ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".idea", ".vscode"}
_MAX_FILE = 1_048_576


def _fail(name: str, msg: str) -> ToolResult:
    return ToolResult(tool_name=name, content=msg, success=False)


class _PathPolicy:
    """Resolve a path and enforce the same allowed-directory and secret rules as file_read."""

    def __init__(self, allowed_dirs: Optional[List[str]]) -> None:
        self._allowed = [Path(d).expanduser().resolve() for d in resolve_allowed_dirs(allowed_dirs)]

    def resolve(self, raw: str) -> Tuple[Optional[Path], str]:
        from openjarvis.security.file_policy import is_sensitive_file

        path = Path(raw).expanduser() if raw else Path.cwd()
        if not path.is_absolute():
            path = (self._allowed[0] if self._allowed else Path.cwd()) / path
        path = path.resolve()
        if is_sensitive_file(path):
            return None, f"Access denied: {raw} is a sensitive file."
        if self._allowed and not any(path == d or path.is_relative_to(d) for d in self._allowed):
            return None, f"Access denied: {raw} is outside allowed directories."
        return path, ""


# --- edit -------------------------------------------------------------------

_NORM = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", "　": " ",
})


def _spans(content: str, old: str) -> Tuple[List[Tuple[int, int]], str]:
    """All places *old* matches in *content*, trying exact, then lenient tiers."""
    out, i = [], content.find(old)
    while i != -1:
        out.append((i, i + len(old)))
        i = content.find(old, i + len(old))
    if out:
        return out, "exact"
    # Tier 2: ignore typographic quote/dash/space variants (1:1 character map keeps offsets).
    nc, no = content.translate(_NORM), old.translate(_NORM)
    i = nc.find(no)
    while i != -1:
        out.append((i, i + len(no)))
        i = nc.find(no, i + len(no))
    if out:
        return out, "typographic variants ignored"
    # Tier 3: ignore trailing whitespace on each line.
    want = [ln.rstrip().translate(_NORM) for ln in old.rstrip("\n").split("\n")]
    lines = content.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    k = 0
    while k + len(want) <= len(lines):
        if all(lines[k + j].rstrip("\r").rstrip().translate(_NORM) == want[j] for j in range(len(want))):
            end = starts[k + len(want) - 1] + len(lines[k + len(want) - 1].rstrip("\r"))
            out.append((starts[k], end))
            k += len(want)
        else:
            k += 1
    return out, "trailing whitespace ignored"


@ToolRegistry.register("edit")
class EditTool(BaseTool):
    tool_id = "edit"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._policy = _PathPolicy(allowed_dirs)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="edit",
            description=(
                "Edit a file by replacing exact text. old_string must identify ONE unique location"
                " (add surrounding lines to disambiguate) unless replace_all is true, which replaces"
                " every occurrence (e.g. renaming a variable). Preserve exact indentation and never"
                " include line-number prefixes from file_read. If there is no exact match, the edit"
                " retries ignoring typographic quotes/dashes/spaces, then trailing whitespace. The"
                " file's CRLF/LF endings are preserved. To create a file use file_write."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to edit."},
                    "old_string": {"type": "string", "description": "Exact text to find."},
                    "new_string": {"type": "string", "description": "Replacement (must differ)."},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
                },
                "required": ["path", "old_string", "new_string"],
            },
            category="filesystem",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        raw = str(params.get("path", ""))
        old = params.get("old_string", params.get("oldString"))
        new = params.get("new_string", params.get("newString"))
        replace_all = bool(params.get("replace_all", params.get("replaceAll", False)))
        if not isinstance(old, str) or not isinstance(new, str):
            return _fail("edit", "old_string and new_string are required.")
        if old == "":
            return _fail("edit", "old_string must not be empty. Use file_write to create a file.")
        if old == new:
            return _fail("edit", "No changes to apply: old_string and new_string are identical.")
        path, err = self._policy.resolve(raw)
        if path is None:
            return _fail("edit", err)
        if not path.is_file():
            return _fail("edit", f"File not found: {raw}")
        try:
            original = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _fail("edit", f"Cannot read {raw}: {exc}")

        crlf = original.count("\r\n") > original.count("\n") - original.count("\r\n")
        if crlf:  # match and write in the file's own line endings
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        spans, how = _spans(original, old)
        if not spans:
            return _fail("edit", f"old_string was not found in {raw}. Read the file again and copy the text exactly.")
        if len(spans) > 1 and not replace_all:
            return _fail(
                "edit",
                f"old_string matches {len(spans)} places in {raw}. Add more surrounding context to make it"
                " unique, or set replace_all to true to change every occurrence.",
            )
        updated = original
        for start, end in reversed(spans if replace_all else spans[:1]):
            updated = updated[:start] + new + updated[end:]
        try:
            tmp = path.with_name(path.name + ".jarvis-tmp")
            tmp.write_bytes(updated.encode("utf-8"))
            os.replace(tmp, path)
        except OSError as exc:
            return _fail("edit", f"Cannot write {raw}: {exc}")
        diff = "".join(list(difflib.unified_diff(
            original.splitlines(True), updated.splitlines(True), "before", "after", n=2))[:60])
        n = len(spans) if replace_all else 1
        note = "" if how == "exact" else f" (matched with {how})"
        try:
            from openjarvis.coding.workspace import WorkspaceManager

            WorkspaceManager.get_instance().record_change(str(path), diff)
        except Exception:
            pass
        return ToolResult(
            tool_name="edit", success=True,
            content=f"Edited {path}: {n} replacement(s){note}.\n{diff}",
            metadata={"path": str(path), "replacements": n},
        )


# --- grep -------------------------------------------------------------------


def _walk(root: Path, include: str = ""):
    if root.is_file():
        yield root
        return
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".git"))
        for f in sorted(files):
            if include and not fnmatch.fnmatch(f, include) and not fnmatch.fnmatch(os.path.join(dirpath, f), include):
                continue
            yield Path(dirpath) / f


@ToolRegistry.register("grep")
class GrepTool(BaseTool):
    tool_id = "grep"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._policy = _PathPolicy(allowed_dirs)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="grep",
            description=(
                "Search file contents with a regular expression (or literal text). Returns"
                " path:line: text. Skips .git, node_modules, virtualenvs and binary files."
                " Prefer this over shell grep for finding code."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex or literal text."},
                    "path": {"type": "string", "description": "File or directory to search."},
                    "include": {"type": "string", "description": 'Glob filter, e.g. "*.py" or "*.{ts,tsx}".'},
                    "literal": {"type": "boolean", "description": "Treat pattern as plain text."},
                    "case_sensitive": {"type": "boolean", "description": "Default true."},
                    "limit": {"type": "integer", "description": "Max matching lines (default 100)."},
                },
                "required": ["pattern"],
            },
            category="filesystem",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        pattern = str(params.get("pattern", ""))
        if not pattern:
            return _fail("grep", "pattern is required.")
        root, err = self._policy.resolve(str(params.get("path", "")))
        if root is None:
            return _fail("grep", err)
        flags = 0 if params.get("case_sensitive", True) else re.I
        try:
            rx = re.compile(re.escape(pattern) if params.get("literal") else pattern, flags)
        except re.error as exc:
            return _fail("grep", f"Invalid regular expression: {exc}")
        limit = max(1, int(params.get("limit") or 100))
        include = str(params.get("include") or "")
        brace = re.match(r"^(.*)\{([^}]*)\}(.*)$", include)
        pats = [brace.group(1) + a + brace.group(3) for a in brace.group(2).split(",")] if brace else [include]
        hits: List[str] = []
        for f in _walk(root):
            if include and not any(fnmatch.fnmatch(f.name, p) for p in pats):
                continue
            try:
                if f.stat().st_size > _MAX_FILE:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:1024]:
                continue
            for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f}:{n}: {line.strip()[:240]}")
                    if len(hits) >= limit:
                        return ToolResult(tool_name="grep", success=True,
                                          content="\n".join(hits) + f"\n[stopped at {limit} matches]")
        return ToolResult(tool_name="grep", success=True, content="\n".join(hits) or "No matches.")


# --- glob -------------------------------------------------------------------


@ToolRegistry.register("glob")
class GlobTool(BaseTool):
    tool_id = "glob"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._policy = _PathPolicy(allowed_dirs)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="glob",
            description='Find files by glob pattern (e.g. "**/*.py", "src/**/*.tsx"). Newest first.',
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern."},
                    "path": {"type": "string", "description": "Directory to search."},
                    "limit": {"type": "integer", "description": "Max files (default 200)."},
                },
                "required": ["pattern"],
            },
            category="filesystem",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        pattern = str(params.get("pattern", ""))
        if not pattern:
            return _fail("glob", "pattern is required.")
        root, err = self._policy.resolve(str(params.get("path", "")))
        if root is None:
            return _fail("glob", err)
        limit = max(1, int(params.get("limit") or 200))
        try:
            found = [p for p in root.glob(pattern)
                     if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & _SKIP_DIRS)]
        except (ValueError, OSError) as exc:
            return _fail("glob", f"Bad pattern: {exc}")
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        more = f"\n[{len(found) - limit} more not shown]" if len(found) > limit else ""
        return ToolResult(tool_name="glob", success=True,
                          content="\n".join(str(p) for p in found[:limit]) + more if found else "No files matched.")


# --- todo_write -------------------------------------------------------------

# Tools run on worker threads (context variables do not follow), so the active scope is a
# plain holder; shortcut: one goal at a time per server, concurrent goals would share it.
ACTIVE_SCOPE = {"id": "main"}
_TODOS: Dict[str, List[dict]] = {}
_STATUS = ("pending", "in_progress", "completed", "cancelled")


def get_todos(scope: Optional[str] = None) -> List[dict]:
    return list(_TODOS.get(scope or ACTIVE_SCOPE["id"], []))


def format_todos(todos: List[dict]) -> str:
    mark = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "cancelled": "[-]"}
    return "\n".join(f"{mark.get(t['status'], '[ ]')} {t['content']}" for t in todos)


@ToolRegistry.register("todo_write")
class TodoWriteTool(BaseTool):
    tool_id = "todo_write"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="todo_write",
            description=(
                "Create and update the todo list for the current task; the user sees it as your"
                " working plan. Use it when a task has 3+ distinct steps. Send the FULL list each"
                " time (it replaces the previous one). Mark an item in_progress when you start it and"
                " completed as soon as it is truly done. Skip it for single simple tasks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The complete updated todo list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "Brief task description."},
                                "status": {"type": "string", "description": "pending, in_progress, completed or cancelled."},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
            category="planning",
            timeout_seconds=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        raw = params.get("todos")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        if not isinstance(raw, list):
            return _fail("todo_write", "todos must be a list of {content, status}.")
        todos = []
        for item in raw:
            if not isinstance(item, dict) or not str(item.get("content", "")).strip():
                return _fail("todo_write", "Every todo needs non-empty content.")
            status = str(item.get("status", "pending"))
            todos.append({"content": str(item["content"]).strip(), "status": status if status in _STATUS else "pending"})
        _TODOS[ACTIVE_SCOPE["id"]] = todos
        done = sum(t["status"] == "completed" for t in todos)
        return ToolResult(tool_name="todo_write", success=True,
                          content=f"Todos updated ({done}/{len(todos)} done):\n{format_todos(todos)}")


# --- project_summary --------------------------------------------------------


@ToolRegistry.register("project_summary")
class ProjectSummaryTool(BaseTool):
    tool_id = "project_summary"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        self._policy = _PathPolicy(allowed_dirs)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="project_summary",
            description=(
                "Inspect the active workspace architecture, discovered test/build commands,"
                " language distribution, project instructions (AGENTS.md/CLAUDE.md), and"
                " session file modification ledger."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional subpath or workspace root."},
                    "refresh": {"type": "boolean", "description": "Force refresh the inventory cache."},
                },
            },
            category="project",
            timeout_seconds=20.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.coding.workspace import WorkspaceManager

        raw = str(params.get("path", ""))
        root, err = self._policy.resolve(raw)
        if root is None:
            return _fail("project_summary", err)

        mgr = WorkspaceManager.get_instance()
        if raw:
            try:
                mgr.set_workspace_root(root)
            except Exception as e:
                return _fail("project_summary", f"Cannot switch workspace root: {e}")

        refresh = bool(params.get("refresh", False))
        summary = mgr.get_project_summary(invalidate_cache=refresh)
        return ToolResult(
            tool_name="project_summary",
            success=True,
            content=json.dumps(summary, indent=2),
            metadata=summary,
        )

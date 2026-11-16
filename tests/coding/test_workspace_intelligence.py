"""
Tests for Phase 3: Workspace Intelligence and Autonomous Coding.

Verifies:
1. Workspace root resolution (canonical path normalization).
2. Path traversal attempts blocked outside workspace root.
3. Symlink escape attempts handled and blocked safely.
4. Ignore patterns honored (.gitignore and default skip dirs).
5. Large repository indexing stays bounded (max_files limit).
6. File search returns correct matches by filename, path, or extracted symbols.
7. Read/write/edit/patch operations work and update change ledger.
8. Stale patch with context mismatch is rejected.
9. User's pre-existing uncommitted changes preserved.
10. Git diff matches reported changed files in ledger.
11. Language-server / AST parsing failure degrades gracefully without crashing.
12. Incremental index updates after file change without full rescanning.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from openjarvis.coding.workspace import (
    PathTraversalError,
    SymlinkEscapeError,
    WorkspaceError,
    WorkspaceManager,
)
from openjarvis.tools.apply_patch import ApplyPatchTool
from openjarvis.tools.coding_tools import EditTool, GlobTool, GrepTool
from openjarvis.tools.file_read import FileReadTool
from openjarvis.tools.file_write import FileWriteTool
from openjarvis.tools.git_tool import GitDiffTool, GitStatusTool


@pytest.fixture
def temp_workspace():
    """Create a temporary git repository for testing workspace operations."""
    ws = Path(tempfile.mkdtemp(prefix="jarvis-ws-test-")).resolve()
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(ws), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Jarvis Test"],
        cwd=str(ws),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "jarvis@openjarvis.ai"],
        cwd=str(ws),
        capture_output=True,
        check=True,
    )

    # Create basic directory structure
    src_dir = ws / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "main.py").write_text(
        "def compute_total(items):\n    return sum(items)\n\nclass Calculator:\n    def add(self, a, b):\n        return a + b\n",
        encoding="utf-8",
    )
    (ws / "package.json").write_text(
        '{"name": "test-pkg", "scripts": {"test": "npm test", "build": "npm run build"}}',
        encoding="utf-8",
    )
    (ws / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nminversion = "7.0"\n',
        encoding="utf-8",
    )
    (ws / ".gitignore").write_text("*.log\nsecrets/\n", encoding="utf-8")

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=str(ws), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(ws),
        capture_output=True,
        check=True,
    )

    yield ws
    # Cleanup
    shutil.rmtree(ws, ignore_errors=True)


# --- 1. Workspace root resolution --------------------------------------------


def test_scenario_1_workspace_root_resolution(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    assert manager.root == temp_workspace
    assert manager.root.is_dir()

    # Relative path resolution inside workspace
    resolved = manager.resolve_path("src/main.py")
    assert resolved == (temp_workspace / "src" / "main.py").resolve()


# --- 2. Path traversal attempts blocked ---------------------------------------


def test_scenario_2_path_traversal_attempts_blocked(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)

    # Parent traversal escape
    with pytest.raises(PathTraversalError):
        manager.resolve_path("../../etc/passwd")

    with pytest.raises(PathTraversalError):
        manager.resolve_path("../outside.txt")

    # Sensitive file protection
    with pytest.raises(PathTraversalError):
        manager.resolve_path(".env")


# --- 3. Symlink escape attempts handled ---------------------------------------


def test_scenario_3_symlink_escape_attempts_handled(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)

    # Create external file outside workspace
    outside_dir = Path(tempfile.mkdtemp(prefix="jarvis-outside-")).resolve()
    outside_file = outside_dir / "target.txt"
    outside_file.write_text("outside confidential data", encoding="utf-8")

    link_path = temp_workspace / "escape_link.txt"
    try:
        os.symlink(outside_file, link_path)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    try:
        with pytest.raises((SymlinkEscapeError, PathTraversalError)):
            manager.resolve_path("escape_link.txt")
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


# --- 4. Ignore patterns honored -----------------------------------------------


def test_scenario_4_ignore_patterns_honored(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)

    # Create ignored files
    (temp_workspace / "app.log").write_text("log data", encoding="utf-8")
    secrets_dir = temp_workspace / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "api_key.txt").write_text("12345", encoding="utf-8")

    # Create node_modules and .venv directories
    nm_dir = temp_workspace / "node_modules" / "pkg"
    nm_dir.mkdir(parents=True, exist_ok=True)
    (nm_dir / "index.js").write_text("module.exports = {}", encoding="utf-8")

    inventory = manager.build_inventory(force_refresh=True)

    # Normal file should be indexed
    assert "src/main.py" in inventory.entries
    # Ignored files should NOT be indexed
    assert "app.log" not in inventory.entries
    assert "secrets/api_key.txt" not in inventory.entries
    assert "node_modules/pkg/index.js" not in inventory.entries


# --- 5. Large repository indexing stays bounded ------------------------------


def test_scenario_5_large_repository_indexing_stays_bounded(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    dummy_dir = temp_workspace / "generated"
    dummy_dir.mkdir(parents=True, exist_ok=True)

    # Generate 50 dummy files
    for i in range(50):
        (dummy_dir / f"file_{i:03d}.py").write_text(f"# file {i}", encoding="utf-8")

    # Enforce bound of 10 files
    inventory = manager.build_inventory(force_refresh=True, max_files=10)
    assert len(inventory.entries) == 10


# --- 6. File search returns correct matches ----------------------------------


def test_scenario_6_file_search_returns_correct_matches(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    inventory = manager.build_inventory(force_refresh=True)

    # Search by path
    matches_path = inventory.search("main")
    assert any(m.rel_path == "src/main.py" for m in matches_path)

    # Search by extracted symbol
    matches_symbol = inventory.search("compute_total")
    assert any(m.rel_path == "src/main.py" for m in matches_symbol)

    matches_class = inventory.search("Calculator")
    assert any(m.rel_path == "src/main.py" for m in matches_class)


# --- 7. Read/write/edit/patch operations work --------------------------------


def test_scenario_7_read_write_edit_patch_operations_work(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)

    # 1. file_write
    fw = FileWriteTool(allowed_dirs=[str(temp_workspace)])
    target_file = temp_workspace / "src" / "worker.py"
    res_w = fw.execute(
        path=str(target_file),
        content="def run_worker():\n    return 'idle'\n",
        mode="write",
    )
    assert res_w.success is True
    assert target_file.is_file()

    # 2. file_read
    fr = FileReadTool(allowed_dirs=[str(temp_workspace)])
    res_r = fr.execute(path=str(target_file))
    assert res_r.success is True
    assert "run_worker" in res_r.content

    # 3. edit
    ed = EditTool(allowed_dirs=[str(temp_workspace)])
    res_e = ed.execute(
        path=str(target_file),
        old_string="return 'idle'",
        new_string="return 'active'",
    )
    assert res_e.success is True
    assert "return 'active'" in target_file.read_text(encoding="utf-8")

    # 4. apply_patch
    patch_text = """--- a/src/worker.py
+++ b/src/worker.py
@@ -1,2 +1,3 @@
 def run_worker():
-    return 'active'
+    print('starting')
+    return 'active'
"""
    ap = ApplyPatchTool()
    res_p = ap.execute(path=str(target_file), patch=patch_text, backup=False)
    assert res_p.success is True
    assert "print('starting')" in target_file.read_text(encoding="utf-8")


# --- 8. Stale patch is rejected ----------------------------------------------


def test_scenario_8_stale_patch_is_rejected(temp_workspace: Path):
    target_file = temp_workspace / "src" / "main.py"
    # Stale patch with non-matching context
    stale_patch = """--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
-def non_existent_function_signature(x, y, z):
+def new_function():
     pass
"""
    ap = ApplyPatchTool()
    res = ap.execute(path=str(target_file), patch=stale_patch)
    assert res.success is False
    assert "hunk #1 failed" in res.content.lower() or "mismatch" in res.content.lower()


# --- 9. User's pre-existing uncommitted changes preserved --------------------


def test_scenario_9_user_preexisting_uncommitted_changes_preserved(
    temp_workspace: Path,
):
    user_file = temp_workspace / "user_notes.txt"
    user_file.write_text("User work in progress", encoding="utf-8")

    # Agent checks git status before making changes
    gs = GitStatusTool()
    res_s = gs.execute(repo_path=str(temp_workspace))
    assert res_s.success is True
    assert "user_notes.txt" in res_s.content

    # Agent works on a separate file
    fw = FileWriteTool(allowed_dirs=[str(temp_workspace)])
    fw.execute(
        path=str(temp_workspace / "agent_file.txt"),
        content="Agent work",
        mode="write",
    )

    # Verify user uncommitted file remains completely untouched
    assert user_file.read_text(encoding="utf-8") == "User work in progress"


# --- 10. Git diff matches reported changed files -----------------------------


def test_scenario_10_git_diff_matches_reported_changed_files(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    manager.clear_change_ledger()

    target = temp_workspace / "src" / "main.py"
    ed = EditTool(allowed_dirs=[str(temp_workspace)])
    res = ed.execute(
        path=str(target),
        old_string="def compute_total(items):",
        new_string="def compute_total(items, discount=0):",
    )
    assert res.success is True

    # Check git diff tool
    gd = GitDiffTool()
    diff_res = gd.execute(repo_path=str(temp_workspace))
    assert diff_res.success is True
    assert "compute_total(items, discount=0)" in diff_res.content

    # Verify change ledger recorded the change
    ledger = manager.get_change_ledger()
    rel_posix = target.relative_to(temp_workspace).as_posix()
    assert any(rel_posix in k for k in ledger.keys())


# --- 11. Language-server / AST failure degrades gracefully -------------------


def test_scenario_11_ast_failure_degrades_gracefully(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    bad_syntax_file = temp_workspace / "src" / "syntax_error.py"
    bad_syntax_file.write_text(
        "def broken_python_syntax(\n  this is invalid python syntax = = =",
        encoding="utf-8",
    )

    # build_inventory should not raise SyntaxError
    inventory = manager.build_inventory(force_refresh=True)
    entry = inventory.get("src/syntax_error.py")
    assert entry is not None
    # Symbols list is simply empty or partial; no agent crash
    assert isinstance(entry.symbols, list)


# --- 12. Incremental index updates after file change -------------------------


def test_scenario_12_incremental_index_updates_after_file_change(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    inv1 = manager.build_inventory(force_refresh=True)
    assert "src/new_feature.py" not in inv1.entries

    # Create a new file
    new_file = temp_workspace / "src" / "new_feature.py"
    new_file.write_text(
        "class NewFeature:\n    def execute(self):\n        return True\n",
        encoding="utf-8",
    )

    # Incremental update without scanning the whole workspace
    updated_entry = manager.update_file_in_inventory("src/new_feature.py")
    assert updated_entry is not None
    assert updated_entry.rel_path == "src/new_feature.py"
    assert "class NewFeature" in updated_entry.symbols
    assert "def execute" in updated_entry.symbols

    # In-memory inventory reflects the change
    assert "src/new_feature.py" in manager._inventory.entries


# --- 13. Project command discovery and summary -------------------------------


def test_scenario_13_project_command_discovery_and_summary(temp_workspace: Path):
    manager = WorkspaceManager(workspace_root=temp_workspace)
    commands = manager.discover_project_commands()
    assert commands["ecosystem"] in ("node", "python")
    assert commands["test_command"] in ("npm test", "pytest")

    summary = manager.get_project_summary(invalidate_cache=True)
    assert summary["workspace_root"] == str(temp_workspace)
    assert summary["total_files"] > 0
    assert "commands" in summary
    assert "language_distribution" in summary

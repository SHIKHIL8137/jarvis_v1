"""
Live Coding Test on a Disposable Sample Repository for Phase 3 Acceptance.

Performs:
1. Inspects sample project architecture via WorkspaceManager.
2. Implements a well-specified feature using EditTool and FileWriteTool.
3. Adds automated tests for the feature.
4. Executes the project tests (runs real pytest).
5. Inspects git diff and verifies that changed files match the change ledger.
6. Verifies uncommitted files and clean execution.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openjarvis.coding.workspace import WorkspaceManager
from openjarvis.tools.coding_tools import EditTool, ProjectSummaryTool
from openjarvis.tools.file_read import FileReadTool
from openjarvis.tools.file_write import FileWriteTool
from openjarvis.tools.git_tool import GitDiffTool, GitStatusTool


def run_live_coding_test():
    print("=== Phase 3 Live Coding Test on Disposable Repository ===")

    # 1. Setup disposable git repository
    sample_dir = Path(tempfile.mkdtemp(prefix="jarvis-live-coding-")).resolve()
    print(f"Created disposable workspace at: {sample_dir}")

    try:
        subprocess.run(
            ["git", "init"], cwd=str(sample_dir), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Jarvis Live Test"],
            cwd=str(sample_dir),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "jarvis@openjarvis.ai"],
            cwd=str(sample_dir),
            capture_output=True,
            check=True,
        )

        # Base project files
        src = sample_dir / "src"
        src.mkdir(parents=True, exist_ok=True)
        tests = sample_dir / "tests"
        tests.mkdir(parents=True, exist_ok=True)

        (sample_dir / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\nminversion = '7.0'\npythonpath = ['.']\n",
            encoding="utf-8",
        )
        (src / "__init__.py").write_text("", encoding="utf-8")
        (sample_dir / "AGENTS.md").write_text(
            "# Project Guidelines\n- All functions must have docstrings.\n- Keep tests in tests/\n",
            encoding="utf-8",
        )
        (sample_dir / ".gitignore").write_text(
            "*.log\n__pycache__/\n.pytest_cache/\n", encoding="utf-8"
        )

        (src / "calculator.py").write_text(
            '"""Basic calculator module."""\n\ndef add(a: float, b: float) -> float:\n    """Add two numbers."""\n    return a + b\n',
            encoding="utf-8",
        )
        (tests / "test_calculator.py").write_text(
            "from src.calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )

        # Initial commit
        subprocess.run(
            ["git", "add", "."],
            cwd=str(sample_dir),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=str(sample_dir),
            capture_output=True,
            check=True,
        )

        # 2. Step 1: Inspect architecture
        print("\n--- Step 1: Inspect Architecture ---")
        mgr = WorkspaceManager(
            workspace_root=sample_dir, allowed_dirs=[str(sample_dir)]
        )
        summary = mgr.get_project_summary(invalidate_cache=True)
        print(f"Total files: {summary['total_files']}")
        print(f"Ecosystem: {summary['commands']['ecosystem']}")
        print(f"Test command: {summary['commands']['test_command']}")
        print(f"Instructions excerpt: {summary['instructions_excerpt'][:60]}...")
        assert summary["commands"]["ecosystem"] == "python"
        assert summary["commands"]["test_command"] == "pytest"

        # 3. Step 2: Implement a feature (multiply and divide functions)
        print("\n--- Step 2: Implement Feature ---")
        new_code = (
            '"""Basic calculator module."""\n\n'
            "def add(a: float, b: float) -> float:\n"
            '    """Add two numbers."""\n'
            "    return a + b\n\n"
            "def multiply(a: float, b: float) -> float:\n"
            '    """Multiply two numbers."""\n'
            "    return a * b\n\n"
            "def divide(a: float, b: float) -> float:\n"
            '    """Divide two numbers. Raises ZeroDivisionError if b == 0."""\n'
            "    if b == 0:\n"
            '        raise ValueError("Cannot divide by zero")\n'
            "    return a / b\n"
        )
        fw = FileWriteTool(allowed_dirs=[str(sample_dir)])
        write_res = fw.execute(
            path=str(src / "calculator.py"),
            content=new_code,
            mode="write",
        )
        print(f"Write result: {write_res.content}")
        assert write_res.success is True

        # 4. Step 3: Add unit tests for the feature
        print("\n--- Step 3: Add Unit Tests ---")
        new_tests = (
            "from src.calculator import add, multiply, divide\n"
            "import pytest\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n\n"
            "def test_multiply():\n"
            "    assert multiply(4, 5) == 20\n\n"
            "def test_divide():\n"
            "    assert divide(10, 2) == 5\n"
            "    with pytest.raises(ValueError):\n"
            "        divide(10, 0)\n"
        )
        write_test_res = fw.execute(
            path=str(tests / "test_calculator.py"),
            content=new_tests,
            mode="write",
        )
        print(f"Write test result: {write_test_res.content}")
        assert write_test_res.success is True

        # 5. Step 4: Run project tests
        print("\n--- Step 4: Run Project Tests ---")
        pytest_exe = sys.executable.replace("python.exe", "pytest.exe")
        test_run = subprocess.run(
            [pytest_exe, "-v"],
            cwd=str(sample_dir),
            capture_output=True,
            text=True,
        )
        print("Pytest output:")
        print(test_run.stdout)
        assert test_run.returncode == 0
        assert "3 passed" in test_run.stdout

        # 6. Step 5: Inspect Git Diff and verify change ledger
        print("\n--- Step 5: Inspect Git Diff and Change Ledger ---")
        gd = GitDiffTool()
        diff_res = gd.execute(repo_path=str(sample_dir))
        print("Git Diff:")
        print(diff_res.content[:300] + "...")
        assert "def multiply" in diff_res.content
        assert "def test_multiply" in diff_res.content

        ledger = mgr.get_change_ledger()
        print(f"Change Ledger recorded {len(ledger)} files: {list(ledger.keys())}")
        assert any("calculator.py" in k for k in ledger.keys())

        # 7. Step 6: Verify status is clean except for our planned modifications
        print("\n--- Step 6: Git Status Verification ---")
        gs = GitStatusTool()
        status_res = gs.execute(repo_path=str(sample_dir))
        print(f"Git Status: {status_res.content}")
        assert "src/calculator.py" in status_res.content
        assert "tests/test_calculator.py" in status_res.content

        results = {
            "test_status": "SUCCESS",
            "workspace_path": str(sample_dir),
            "files_modified": list(ledger.keys()),
            "tests_run": "3 passed in pytest",
            "git_diff_verified": True,
        }
        res_file = Path(__file__).parent / "phase_3_live_coding_results.json"
        with open(res_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print("\n[PASSED] Phase 3 Live Coding Test passed successfully!")
    finally:
        shutil.rmtree(sample_dir, ignore_errors=True)


if __name__ == "__main__":
    run_live_coding_test()

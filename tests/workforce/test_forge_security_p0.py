"""Regression tests for P0 FORGE Execution Security requirements."""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openjarvis.workforce.forge_coding import (
    ForgeCodingEngine,
    VerificationStatus,
    WorkspaceLock,
)
from openjarvis.workforce.models import AgentReport


def test_req1_cross_process_workspace_locking(tmp_path):
    """Req 1: Workspace file locking locks across processes; second lock attempt times out."""
    workdir = tmp_path / "workspace_lock_test"
    workdir.mkdir()

    with WorkspaceLock.acquire(target_dir=workdir, timeout=1.0):
        # A second attempt to acquire while held must raise TimeoutError
        with pytest.raises(TimeoutError):
            with WorkspaceLock.acquire(target_dir=workdir, timeout=0.2):
                pass


def test_req2_pre_existing_dirty_files_ignored(tmp_path):
    """Req 2: Report only file modifications introduced by active task, ignoring pre-existing dirty files."""
    workdir = tmp_path / "dirty_test"
    workdir.mkdir()

    # Pre-existing file created BEFORE task starts
    old_file = workdir / "old_pre_existing.py"
    old_file.write_text("# Existing pre-task dirty file")

    engine = ForgeCodingEngine()

    # Mock git status to simulate pre-existing dirty file + new task file
    initial_git = {"old_pre_existing.py"}
    after_git = {"old_pre_existing.py", "newly_created_task_file.py"}

    with patch.object(engine, "_get_modified_git_files", side_effect=[initial_git, after_git]):
        with patch.object(engine, "_auto_verify_tests", return_value=None):
            report = engine.execute_coding_task(
                task_id="task-diff-1",
                objective="Create newly_created_task_file.py",
                workdir=str(workdir),
                use_opencode=False,
            )

            # Files changed MUST contain ONLY the new file, NEVER the pre-existing dirty file
            assert "newly_created_task_file.py" in report.files_changed
            assert "old_pre_existing.py" not in report.files_changed


def test_req3_and_req4_missing_tests_reports_not_run_never_success(tmp_path):
    """Req 3 & 4: Never report verification success if no tests were executed. Status must be not_run."""
    workdir = tmp_path / "no_tests_workspace"
    workdir.mkdir()

    engine = ForgeCodingEngine()

    with patch.object(engine, "_auto_verify_tests", return_value=None):
        with patch.object(engine, "_get_modified_git_files", return_value=set()):
            report = engine.execute_coding_task(
                task_id="task-no-tests",
                objective="Refactor helpers",
                workdir=str(workdir),
                use_opencode=False,
            )

            assert report.results["verification_status"] == VerificationStatus.NOT_RUN.value
            assert report.results["verified"] is False
            assert report.results["verification_status"] != VerificationStatus.PASSED.value


def test_req4_failed_tests_reports_failed(tmp_path):
    """Req 4: Test failures report explicit 'failed' status and verified=False."""
    workdir = tmp_path / "failing_tests_workspace"
    workdir.mkdir()

    engine = ForgeCodingEngine()

    mock_fail_test = {
        "command": "pytest",
        "passed": False,
        "status": "failed",
        "exit_code": 1,
        "output": "AssertionError: 1 != 2",
    }

    with patch.object(engine, "_auto_verify_tests", return_value=mock_fail_test):
        with patch.object(engine, "_get_modified_git_files", return_value=set()):
            report = engine.execute_coding_task(
                task_id="task-fail-tests",
                objective="Fix math bug",
                workdir=str(workdir),
                use_opencode=False,
            )

            assert report.results["verification_status"] == VerificationStatus.FAILED.value
            assert report.results["verified"] is False


def test_req5_and_req6_path_traversal_command_blocked(tmp_path):
    """Req 5 & 6: Verification commands attempting path traversal out of workspace are blocked."""
    workdir = tmp_path / "sandbox"
    workdir.mkdir()

    engine = ForgeCodingEngine()

    res = engine._run_command(cmd="cat ../../../etc/shadow", cwd=str(workdir))
    assert res["status"] == "blocked"
    assert res["passed"] is False
    assert "Security boundary violation" in res["output"]


def test_req5_destructive_shell_command_blocked(tmp_path):
    """Req 5: Destructive shell commands (e.g. rm -rf /) are blocked by security gateway."""
    workdir = tmp_path / "sandbox"
    workdir.mkdir()

    engine = ForgeCodingEngine()

    res = engine._run_command(cmd="rm -rf / --no-preserve-root", cwd=str(workdir))
    assert res["status"] == "blocked"
    assert res["passed"] is False
    assert "Security boundary violation" in res["output"]


def test_req7_verification_passed_status(tmp_path):
    """Req 7: When tests pass, report explicit 'passed' status and verified=True."""
    workdir = tmp_path / "passing_tests_workspace"
    workdir.mkdir()

    engine = ForgeCodingEngine()

    mock_pass_test = {
        "command": "pytest",
        "passed": True,
        "status": "passed",
        "exit_code": 0,
        "output": "3 passed in 0.05s",
    }

    with patch.object(engine, "_auto_verify_tests", return_value=mock_pass_test):
        with patch.object(engine, "_get_modified_git_files", return_value=set()):
            report = engine.execute_coding_task(
                task_id="task-pass-tests",
                objective="Add feature",
                workdir=str(workdir),
                use_opencode=False,
            )

            assert report.results["verification_status"] == VerificationStatus.PASSED.value
            assert report.results["verified"] is True

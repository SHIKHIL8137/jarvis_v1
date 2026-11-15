"""P0 Security Tests for FORGE Arbitrary Code Execution and Isolated Verification Runner.

Verifies:
1. Arbitrary inline code execution (python -c, python -m, node -e) is strictly blocked on bare host when container sandbox is unavailable.
2. Host secrets and credentials (API keys, tokens, passwords, JWT secrets) are completely scrubbed from subprocess environment.
3. Path traversal and symlink escapes outside the workspace are blocked.
4. Blocked operations, failed tests, and passed tests have distinct, accurate statuses.
5. Container isolation arguments (--network none, memory limit, cpu limit, pids limit, workspace mount) are applied when container is used.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.sandbox.verification_runner import (
    IsolatedVerificationRunner,
    VerificationOutcome,
)
from openjarvis.workforce.forge_coding import ForgeCodingEngine


def test_forge_blocks_inline_python_c_without_container(tmp_path):
    """'python -c' inline script must fail closed with status='blocked' when container sandbox is unavailable."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        res = runner.execute_verification_command('python -c "import os; print(os.name)"', cwd=str(tmp_path))
        assert res["passed"] is False
        assert res["status"] == VerificationOutcome.BLOCKED.value
        assert "Inline python script execution" in res["output"]
        assert res["sandbox_mode"] == "blocked_no_container"


def test_forge_blocks_inline_node_e_without_container(tmp_path):
    """'node -e' inline script must fail closed with status='blocked' when container sandbox is unavailable."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        res = runner.execute_verification_command('node -e "console.log(process.env)"', cwd=str(tmp_path))
        assert res["passed"] is False
        assert res["status"] == VerificationOutcome.BLOCKED.value
        assert "Inline node script execution" in res["output"]


def test_forge_blocks_python_m_without_container(tmp_path):
    """'python -m' dynamic module execution must fail closed with status='blocked' when container sandbox is unavailable."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        res = runner.execute_verification_command("python -m http.server", cwd=str(tmp_path))
        assert res["passed"] is False
        assert res["status"] == VerificationOutcome.BLOCKED.value
        assert "Dynamic python module execution" in res["output"]


def test_host_secrets_completely_scrubbed_from_subprocess_environment(tmp_path):
    """Subprocess environment must have ZERO host credentials, API keys, or JWT secrets."""
    runner = IsolatedVerificationRunner()

    fake_env = {
        "PATH": os.environ.get("PATH", ""),
        "OPENAI_API_KEY": "sk-secret-openai-key",
        "ANTHROPIC_API_KEY": "sk-ant-secret-key",
        "GEMINI_API_KEY": "gemini-secret-api-key",
        "DEEPSEEK_API_KEY": "deepseek-secret-api-key",
        "ATLASCLOUD_API_KEY": "atlas-secret-key",
        "N8N_API_KEY": "n8n-secret-token",
        "N8N_PASSWORD": "supersecretpassword",
        "GITHUB_TOKEN": "ghp_secrettoken12345",
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "JWT_SECRET": "my_jwt_signing_secret",
        "DATABASE_URL": "postgresql://user:pass@localhost/db",
    }

    with patch.dict(os.environ, fake_env, clear=True):
        sanitized = runner.sanitize_host_environment()

        # NONE of the sensitive keys should exist in sanitized environment
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
            "ATLASCLOUD_API_KEY",
            "N8N_API_KEY",
            "N8N_PASSWORD",
            "GITHUB_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "JWT_SECRET",
            "DATABASE_URL",
        ):
            assert key not in sanitized, f"Host secret '{key}' leaked into sanitized environment!"


def test_symlink_escape_outside_workspace_is_blocked(tmp_path):
    """A symlink in arguments pointing outside authorized workspace must be rejected with status='blocked'."""
    runner = IsolatedVerificationRunner()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir()
    secret_file = outside_dir / "passwords.txt"
    secret_file.write_text("secret_data")

    # Create symlink inside workspace pointing to outside secret file
    link_in_ws = workspace / "escape_link.txt"
    try:
        link_in_ws.symlink_to(secret_file)
    except (OSError, NotImplementedError):
        # On Windows without SeCreateSymbolicLinkPrivilege, test path traversal instead
        pytest.skip("Symlink creation not permitted in current OS user context")

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        res = runner.execute_verification_command(f"python {link_in_ws.name}", cwd=str(workspace))
        assert res["passed"] is False
        assert res["status"] == VerificationOutcome.BLOCKED.value
        assert "resolves outside authorized workspace" in res["output"] or "Security boundary violation" in res["output"]


def test_path_traversal_in_command_arguments_is_blocked(tmp_path):
    """Path traversal (..) in command arguments must be rejected immediately with status='blocked'."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        res = runner.execute_verification_command("pytest ../../../etc/shadow", cwd=str(tmp_path))
        assert res["passed"] is False
        assert res["status"] == VerificationOutcome.BLOCKED.value
        assert "path traversal prohibited" in res["output"]


def test_verification_statuses_are_distinct(tmp_path):
    """Verify passed, failed, blocked, and inconclusive produce different accurate statuses."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        # 1. Blocked (dangerous metacharacter)
        res_blocked = runner.execute_verification_command("pytest tests; rm -rf /", cwd=str(tmp_path))
        assert res_blocked["status"] == VerificationOutcome.BLOCKED.value
        assert res_blocked["passed"] is False

        # 2. Passed (standard version check exit 0)
        res_passed = runner.execute_verification_command("python --version", cwd=str(tmp_path))
        assert res_passed["status"] == VerificationOutcome.PASSED.value
        assert res_passed["passed"] is True

        # 3. Failed (non-zero exit code in trusted environment)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="Test failures: 2", stderr="")
            res_failed = runner.execute_verification_command("pytest tests/", cwd=str(tmp_path), trusted_environment=True)
            assert res_failed["status"] == VerificationOutcome.FAILED.value
            assert res_failed["passed"] is False

        # 4. Inconclusive (timeout in trusted environment)
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=5.0)):
            res_timeout = runner.execute_verification_command("pytest tests/", cwd=str(tmp_path), trusted_environment=True)
            assert res_timeout["status"] == VerificationOutcome.INCONCLUSIVE.value
            assert res_timeout["passed"] is False


def test_untrusted_project_test_fails_closed_without_container(tmp_path):
    """Untrusted project tests (e.g. pytest tests/) must fail closed with status='blocked' when container is unavailable."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        res = runner.execute_verification_command("pytest tests/", cwd=str(tmp_path))
        assert res["passed"] is False
        assert res["status"] == VerificationOutcome.BLOCKED.value
        assert res["sandbox_mode"] == "blocked_no_container"
        assert "Security policy violation" in res["output"]


def test_url_encoded_traversal_and_unc_escapes_blocked(tmp_path):
    """URL-encoded traversal (%2e%2e) and UNC paths (\\\\) must be blocked immediately."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=False):
        # 1. URL encoded traversal
        res_encoded = runner.execute_verification_command("pytest %2e%2e/%2e%2e/etc/passwd", cwd=str(tmp_path))
        assert res_encoded["passed"] is False
        assert res_encoded["status"] == VerificationOutcome.BLOCKED.value
        assert "path traversal prohibited" in res_encoded["output"]

        # 2. UNC path escape
        res_unc = runner.execute_verification_command("pytest \\\\attacker\\share\\test.py", cwd=str(tmp_path))
        assert res_unc["passed"] is False
        assert res_unc["status"] == VerificationOutcome.BLOCKED.value
        assert "UNC paths prohibited" in res_unc["output"]


def test_container_sandbox_isolation_flags(tmp_path):
    """When container sandbox is available, runner passes isolated network, non-root user, cap drops, and resource limits."""
    runner = IsolatedVerificationRunner()

    with patch.object(runner, "is_container_runtime_available", return_value=True):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="All tests passed", stderr="")
                res = runner.execute_verification_command("pytest tests/", cwd=str(tmp_path))

                assert res["passed"] is True
                assert res["sandbox_mode"] == "container_isolated"
                args = mock_run.call_args[0][0]
                assert "--network" in args
                assert "none" in args
                assert "--user" in args
                assert "1000:1000" in args
                assert "--cap-drop=ALL" in args
                assert "--security-opt" in args
                assert "no-new-privileges" in args
                assert "--read-only" in args
                assert "--tmpfs" in args
                assert "/tmp:rw,noexec,nosuid,size=64m" in args
                assert "--memory" in args
                assert "--cpus" in args
                assert "--pids-limit" in args
                assert f"{tmp_path.resolve()}:/workspace:rw" in args


def test_inline_python_with_unauthorized_cwd_blocked(tmp_path):
    """Inline Python execution with unauthorized cwd must be blocked even when container runtime is available."""
    runner = IsolatedVerificationRunner()
    authorized = tmp_path / "authorized"
    authorized.mkdir()
    unauthorized = tmp_path / "unauthorized"
    unauthorized.mkdir()

    with patch.object(runner, "is_container_runtime_available", return_value=True):
        with patch("openjarvis.sandbox.verification_runner.load_config") as mock_cfg:
            mock_cfg.return_value.security.allowed_dirs = [str(authorized)]
            with patch.object(runner, "_run_in_container") as mock_run_container:
                res = runner.execute_verification_command('python -c "print(1)"', cwd=str(unauthorized))
                assert res["passed"] is False
                assert res["status"] == VerificationOutcome.BLOCKED.value
                assert "outside authorized workspace" in res["output"]
                mock_run_container.assert_not_called()


def test_inline_node_with_unauthorized_cwd_blocked(tmp_path):
    """Inline Node execution with unauthorized cwd must be blocked even when container runtime is available."""
    runner = IsolatedVerificationRunner()
    authorized = tmp_path / "authorized"
    authorized.mkdir()
    unauthorized = tmp_path / "unauthorized"
    unauthorized.mkdir()

    with patch.object(runner, "is_container_runtime_available", return_value=True):
        with patch("openjarvis.sandbox.verification_runner.load_config") as mock_cfg:
            mock_cfg.return_value.security.allowed_dirs = [str(authorized)]
            with patch.object(runner, "_run_in_container") as mock_run_container:
                res = runner.execute_verification_command('node -e "console.log(1)"', cwd=str(unauthorized))
                assert res["passed"] is False
                assert res["status"] == VerificationOutcome.BLOCKED.value
                assert "outside authorized workspace" in res["output"]
                mock_run_container.assert_not_called()


def test_symlink_escape_with_container_available_blocked(tmp_path):
    """Symlink escape in arguments must be blocked before container invocation even if Docker is running."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()

    with patch.object(runner, "is_container_runtime_available", return_value=True):
        with patch.object(runner, "check_symlink_and_traversal_escapes", return_value="Security boundary violation: symlink escape detected"):
            with patch.object(runner, "_run_in_container") as mock_run_container:
                res = runner.execute_verification_command("pytest test_link.py", cwd=str(ws))
                assert res["passed"] is False
                assert res["status"] == VerificationOutcome.BLOCKED.value
                assert "symlink escape detected" in res["output"]
                mock_run_container.assert_not_called()


def test_windows_drive_root_escape_blocked(tmp_path):
    """Windows drive-root escape (e.g. C:\\ or D:\\) in command arguments must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()

    with patch("openjarvis.sandbox.verification_runner.load_config") as mock_cfg:
        mock_cfg.return_value.security.allowed_dirs = [str(ws)]
        res_c = runner.execute_verification_command("pytest C:\\", cwd=str(ws))
        assert res_c["passed"] is False
        assert res_c["status"] == VerificationOutcome.BLOCKED.value
        assert "drive-root escape prohibited" in res_c["output"] or "resolves outside authorized workspace" in res_c["output"]

        res_d = runner.execute_verification_command("pytest D:/", cwd=str(ws))
        assert res_d["passed"] is False
        assert res_d["status"] == VerificationOutcome.BLOCKED.value


def test_unc_path_in_cwd_or_args_blocked(tmp_path):
    """UNC path in cwd or argument must be rejected immediately."""
    runner = IsolatedVerificationRunner()

    # 1. UNC cwd
    res_cwd = runner.execute_verification_command("pytest tests/", cwd=r"\\server\share\repo")
    assert res_cwd["passed"] is False
    assert res_cwd["status"] == VerificationOutcome.BLOCKED.value
    assert "UNC paths prohibited" in res_cwd["output"]

    # 2. UNC argument
    res_arg = runner.execute_verification_command(r"pytest \\server\share\evil.py", cwd=str(tmp_path))
    assert res_arg["passed"] is False
    assert res_arg["status"] == VerificationOutcome.BLOCKED.value
    assert "UNC paths prohibited" in res_arg["output"]


def test_nonexistent_path_resolving_outside_workspace_blocked(tmp_path):
    """Nonexistent filesystem paths that resolve outside workspace must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()

    with patch("openjarvis.sandbox.verification_runner.load_config") as mock_cfg:
        mock_cfg.return_value.security.allowed_dirs = [str(ws)]
        # Nonexistent path on C:
        res_nonexistent = runner.execute_verification_command("pytest C:\\nonexistent_system_secret\\test.py", cwd=str(ws))
        assert res_nonexistent["passed"] is False
        assert res_nonexistent["status"] == VerificationOutcome.BLOCKED.value
        assert "resolves outside authorized workspace" in res_nonexistent["output"]


def test_windows_paths_simulated_on_posix_blocked(tmp_path):
    """Simulating execution on POSIX/Linux where backslashes could be consumed by shlex."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()

    with patch("sys.platform", "linux"):
        with patch("openjarvis.sandbox.verification_runner.load_config") as mock_cfg:
            mock_cfg.return_value.security.allowed_dirs = [str(ws)]

            # 1. Windows drive root escape simulated on POSIX
            res_drive = runner.execute_verification_command("pytest C:\\", cwd=str(ws))
            assert res_drive["passed"] is False
            assert res_drive["status"] == VerificationOutcome.BLOCKED.value
            assert "drive-root escape prohibited" in res_drive["output"]

            # 2. Windows UNC path simulated on POSIX
            res_unc = runner.execute_verification_command(r"pytest \\server\share\evil.py", cwd=str(ws))
            assert res_unc["passed"] is False
            assert res_unc["status"] == VerificationOutcome.BLOCKED.value
            assert "UNC paths prohibited" in res_unc["output"]

            # 3. Windows absolute path with backslashes on POSIX
            res_win_abs = runner.execute_verification_command(r"pytest C:\Windows\System32\evil.py", cwd=str(ws))
            assert res_win_abs["passed"] is False
            assert res_win_abs["status"] == VerificationOutcome.BLOCKED.value
            assert "resolves outside authorized workspace" in res_win_abs["output"]

            # 4. Traversal with backslashes on POSIX
            res_trav = runner.execute_verification_command(r"pytest tests\..\..\secret.py", cwd=str(ws))
            assert res_trav["passed"] is False
            assert res_trav["status"] == VerificationOutcome.BLOCKED.value
            assert "path traversal prohibited" in res_trav["output"]


def test_repeated_url_encoded_traversal_blocked(tmp_path):
    """Repeatedly URL-encoded traversal (%252e%252e) must be decoded and blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()

    res = runner.execute_verification_command("pytest %252e%252e/%252e%252e/etc/passwd", cwd=str(ws))
    assert res["passed"] is False
    assert res["status"] == VerificationOutcome.BLOCKED.value
    assert "path traversal prohibited" in res["output"]



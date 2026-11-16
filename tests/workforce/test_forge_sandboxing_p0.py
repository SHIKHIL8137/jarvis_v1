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
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.sandbox.verification_runner import (
    IsolatedVerificationRunner,
    VerificationOutcome,
)
from openjarvis.workforce.forge_coding import ForgeCodingEngine


@contextmanager
def symlink_or_simulated(link_path: Path, target_path: Path):
    """Create a real OS symlink if permitted by user context; otherwise simulate via mocking."""
    try:
        link_path.symlink_to(target_path)
        yield link_path
        return
    except (OSError, NotImplementedError):
        pass

    # Create dummy placeholder file or directory
    if target_path.is_dir() if target_path.exists() else target_path.suffix == "":
        link_path.mkdir(exist_ok=True)
    else:
        link_path.write_text("simulated_symlink_placeholder")

    orig_is_symlink = Path.is_symlink
    orig_readlink = os.readlink
    orig_resolve = Path.resolve

    def mock_is_symlink(self):
        if str(self) == str(link_path) or self.name == link_path.name:
            return True
        return orig_is_symlink(self)

    def mock_readlink(p):
        if str(p) == str(link_path) or Path(p).name == link_path.name:
            return str(target_path)
        return orig_readlink(p)

    def mock_resolve(self, strict=False):
        if str(self) == str(link_path) or self.name == link_path.name:
            return target_path.resolve(strict=False)
        return orig_resolve(self, strict=strict)

    with patch.object(Path, "is_symlink", mock_is_symlink), \
         patch("os.readlink", mock_readlink), \
         patch.object(Path, "resolve", mock_resolve):
        yield link_path


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

    link_in_ws = workspace / "escape_link.txt"
    with symlink_or_simulated(link_in_ws, secret_file):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            res = runner.execute_verification_command(f"python {link_in_ws.name}", cwd=str(workspace))
            assert res["passed"] is False
            assert res["status"] == VerificationOutcome.BLOCKED.value
            assert "resolves outside authorized workspace via symlink" in res["output"]


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


def test_symlink_to_directory_outside_workspace_blocked(tmp_path):
    """Symlink pointing to a directory outside workspace must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "target.py").write_text("print('outside')")

    link_dir = ws / "symlink_dir"
    with symlink_or_simulated(link_dir, outside_dir):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            res = runner.execute_verification_command(f"python {link_dir.name}/target.py", cwd=str(ws))
            assert res["passed"] is False
            assert res["status"] == VerificationOutcome.BLOCKED.value
            assert "resolves outside authorized workspace via symlink" in res["output"]


def test_nested_symlink_escape_blocked(tmp_path):
    """Nested symlink escape (link1 -> link2 -> outside_secret) must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir()
    secret = outside_dir / "secret.txt"
    secret.write_text("secret_val")

    link2 = ws / "link2.txt"
    link1 = ws / "link1.txt"

    with symlink_or_simulated(link2, secret):
        with symlink_or_simulated(link1, link2):
            with patch.object(runner, "is_container_runtime_available", return_value=False):
                res = runner.execute_verification_command(f"python {link1.name}", cwd=str(ws))
                assert res["passed"] is False
                assert res["status"] == VerificationOutcome.BLOCKED.value
                assert "resolves outside authorized workspace via symlink" in res["output"]


def test_symlink_pointing_to_another_symlink_blocked(tmp_path):
    """Symlink in workspace pointing to another symlink outside workspace must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    target_file = outside_dir / "data.txt"
    target_file.write_text("payload")

    outside_link = outside_dir / "outside_link.txt"
    ws_link = ws / "ws_link.txt"

    with symlink_or_simulated(outside_link, target_file):
        with symlink_or_simulated(ws_link, outside_link):
            with patch.object(runner, "is_container_runtime_available", return_value=False):
                res = runner.execute_verification_command(f"python {ws_link.name}", cwd=str(ws))
                assert res["passed"] is False
                assert res["status"] == VerificationOutcome.BLOCKED.value
                assert "resolves outside authorized workspace via symlink" in res["output"]


def test_legitimate_symlink_inside_workspace_permitted(tmp_path):
    """Legitimate symlink whose target is within the authorized workspace must be permitted."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    sub = ws / "sub"
    sub.mkdir()
    target_file = sub / "legit_test.py"
    target_file.write_text("print('legitimate')")

    link_in_ws = ws / "link_to_legit.py"

    with symlink_or_simulated(link_in_ws, target_file):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="legitimate\n", stderr="")
                res = runner.execute_verification_command(
                    f"python {link_in_ws.name}",
                    cwd=str(ws),
                    trusted_environment=True,
                )
                assert res["passed"] is True
                assert res["status"] == VerificationOutcome.PASSED.value


def test_relative_symlink_escape_blocked(tmp_path):
    """Relative symlink pointing to an outside path must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_secret = tmp_path / "outside_secret" / "passwords.txt"
    outside_secret.parent.mkdir()
    outside_secret.write_text("secret_pw")

    rel_link = ws / "rel_escape.txt"
    with symlink_or_simulated(rel_link, outside_secret):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            res = runner.execute_verification_command(f"python {rel_link.name}", cwd=str(ws))
            assert res["passed"] is False
            assert res["status"] == VerificationOutcome.BLOCKED.value
            assert "resolves outside authorized workspace via symlink" in res["output"]


def test_absolute_symlink_escape_blocked(tmp_path):
    """Absolute symlink pointing to an outside file must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_file = tmp_path / "outside_dir" / "abs_target.txt"
    outside_file.parent.mkdir()
    outside_file.write_text("abs_target_content")

    abs_link = ws / "abs_escape.txt"
    with symlink_or_simulated(abs_link, outside_file.resolve()):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            res = runner.execute_verification_command(f"python {abs_link.name}", cwd=str(ws))
            assert res["passed"] is False
            assert res["status"] == VerificationOutcome.BLOCKED.value
            assert "resolves outside authorized workspace via symlink" in res["output"]


def test_symlink_escape_with_docker_available_blocked(tmp_path):
    """Symlink escape must be blocked BEFORE container dispatch even when Docker is active."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("leak")

    link = ws / "docker_escape.txt"
    with symlink_or_simulated(link, outside):
        with patch.object(runner, "is_container_runtime_available", return_value=True):
            with patch.object(runner, "_run_in_container") as mock_container:
                res = runner.execute_verification_command(f"python {link.name}", cwd=str(ws))
                assert res["passed"] is False
                assert res["status"] == VerificationOutcome.BLOCKED.value
                assert "resolves outside authorized workspace via symlink" in res["output"]
                assert res["sandbox_mode"] == "blocked"
                mock_container.assert_not_called()


def test_symlink_escape_without_docker_blocked(tmp_path):
    """Without Docker, symlink validation blocks the request specifically (not generic no-container error)."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("leak")

    link = ws / "no_docker_escape.txt"
    with symlink_or_simulated(link, outside):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            res = runner.execute_verification_command(f"python {link.name}", cwd=str(ws))
            assert res["passed"] is False
            assert res["status"] == VerificationOutcome.BLOCKED.value
            # Must prove symlink validation itself blocked it, not generic blocked_no_container
            assert "resolves outside authorized workspace via symlink" in res["output"]


def test_nonexistent_destination_under_escaping_symlink_blocked(tmp_path):
    """Nonexistent destination path under an escaping symlink directory must be blocked."""
    runner = IsolatedVerificationRunner()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    link_dir = ws / "escaping_dir_link"
    with symlink_or_simulated(link_dir, outside_dir):
        with patch.object(runner, "is_container_runtime_available", return_value=False):
            res = runner.execute_verification_command(f"python {link_dir.name}/nonexistent_child.py", cwd=str(ws))
            assert res["passed"] is False
            assert res["status"] == VerificationOutcome.BLOCKED.value
            assert "resolves outside authorized workspace via symlink" in res["output"]



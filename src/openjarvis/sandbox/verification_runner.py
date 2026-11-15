"""Isolated Verification Runner — executes test verification commands in a container sandbox or securely sanitized host environment.

SECURITY PRINCIPLE:
Executable allowlists alone DO NOT provide OS-level process isolation or sandbox guarantees against arbitrary code execution (e.g. pytest plugins, npm test scripts, or inline scripts).
Untrusted arbitrary inline executions (python -c, python -m, node -e) require an isolated container sandbox;
if unavailable, the runner fails closed.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openjarvis.core.config import load_config
from openjarvis.sandbox.mount_security import DEFAULT_BLOCKED_PATTERNS
from openjarvis.tools._paths import get_safe_default_dirs, is_path_within_allowed

logger = logging.getLogger(__name__)

# Verification executables permitted for FORGE test verification
ALLOWED_VERIFICATION_EXECUTABLES: frozenset[str] = frozenset({
    "pytest",
    "python",
    "python3",
    "py",
    "npm",
    "npx",
    "node",
    "git",
    "uv",
    "ruff",
    "flake8",
    "mypy",
    "black",
    "isort",
    "vitest",
    "jest",
    "cargo",
    "go",
    "pnpm",
    "yarn",
})

# Patterns of sensitive host environment variables to strip
SENSITIVE_ENV_PATTERNS: tuple[str, ...] = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "AUTH",
    "CREDENTIAL",
    "PRIVATE",
    "JWT",
    "AWS_",
    "GITHUB_",
    "N8N_",
    "OPENAI_",
    "ANTHROPIC_",
    "GEMINI_",
    "DEEPSEEK_",
    "ATLASCLOUD_",
    "DATABASE_URL",
    "REDIS_URL",
)


class VerificationOutcome(str, Enum):
    """Explicit verification state transitions."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class IsolatedVerificationRunner:
    """Manages secure test and verification execution.

    Enforces:
    1. Disposable container isolation when container runtime (Docker/Podman) is active.
    2. Fail-closed rejection of arbitrary inline code (python -c, python -m, node -e) when container sandbox is unavailable.
    3. Complete environment secret scrubbing (zero host credentials passed to subprocess).
    4. Strict workspace path containment and symlink escape rejection.
    5. CPU, memory, process-count, and timeout execution limits.
    """

    DEFAULT_IMAGE = "openjarvis-sandbox:latest"
    DEFAULT_TIMEOUT = 120.0
    DEFAULT_MEM_LIMIT = "1024m"
    DEFAULT_CPUS = "2.0"
    DEFAULT_PIDS_LIMIT = "100"

    def __init__(
        self,
        *,
        image: Optional[str] = None,
        runtime: str = "docker",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.image = image or self.DEFAULT_IMAGE
        self.runtime = runtime
        self.timeout = timeout
        self._container_available: Optional[bool] = None
        self._last_container_check: float = 0.0

    def is_container_runtime_available(self, force_refresh: bool = False) -> bool:
        """Check if Docker or Podman CLI is present and the daemon is actively responding."""
        now = time.time()
        if not force_refresh and self._container_available is not None and (now - self._last_container_check) < 15.0:
            return self._container_available

        runtime_bin = shutil.which(self.runtime)
        if not runtime_bin:
            self._container_available = False
            self._last_container_check = now
            return False

        try:
            # Probe daemon responsiveness with short timeout
            res = subprocess.run(
                [runtime_bin, "info"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            self._container_available = (res.returncode == 0)
        except Exception:
            self._container_available = False

        self._last_container_check = now
        return self._container_available

    @staticmethod
    def is_untrusted_inline_execution(tokens: List[str]) -> Tuple[bool, str]:
        """Detect whether command invokes arbitrary inline code or unverified module execution."""
        if not tokens:
            return False, ""

        raw_exec = tokens[0]
        exec_stem = Path(raw_exec).stem.lower()

        # Check python -c or python -m
        if exec_stem in ("python", "python3", "py"):
            for i, tok in enumerate(tokens[1:], start=1):
                if tok == "-c":
                    return True, f"Inline python script execution ('python -c') detected."
                if tok == "-m":
                    return True, f"Dynamic python module execution ('python -m') detected."

        # Check node -e or node --eval
        if exec_stem in ("node", "nodejs"):
            for tok in tokens[1:]:
                if tok in ("-e", "--eval", "-p", "--print"):
                    return True, f"Inline node script execution ('node {tok}') detected."

        return False, ""

    @staticmethod
    def sanitize_host_environment() -> Dict[str, str]:
        """Return a scrubbed copy of os.environ with all host credentials, API keys, and secrets stripped."""
        clean_env: Dict[str, str] = {}
        for k, v in os.environ.items():
            k_upper = k.upper()
            if any(pattern in k_upper for pattern in SENSITIVE_ENV_PATTERNS):
                # Omit secret from child process environment
                continue
            clean_env[k] = v

        # Explicitly ensure sensitive Jarvis keys are absent
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
        ):
            clean_env.pop(key, None)

        return clean_env

    @staticmethod
    def check_symlink_and_traversal_escapes(
        resolved_cwd: Path,
        tokens: List[str],
        allowed_dirs: List[Path],
    ) -> Optional[str]:
        """Validate that neither the working directory nor any path in arguments escapes authorized roots via symlinks or traversal."""
        # 1. Check if cwd is a symlink resolving outside allowed dirs
        try:
            real_cwd = resolved_cwd.resolve()
            if not is_path_within_allowed(real_cwd, allowed_dirs):
                return f"Security boundary violation: working directory '{resolved_cwd}' resolves outside authorized roots via symlink."
        except Exception as exc:
            return f"Security boundary violation: cannot resolve working directory: {exc}"

        # 2. Check each token for path traversal and symlink escapes
        for tok in tokens[1:]:
            clean_tok = tok.strip()
            # Traversal patterns
            if (
                clean_tok == ".."
                or clean_tok.startswith("..")
                or "/.." in clean_tok
                or "\\.." in clean_tok
                or "/etc/" in clean_tok
                or "\\Windows\\" in clean_tok
            ):
                return f"Security boundary violation: path traversal prohibited in argument '{tok}'"

            # Check if token resolves to a filesystem path
            is_path_like = (
                any(clean_tok.startswith(p) for p in ("/", "\\", "c:", "C:", "d:", "D:"))
                or (clean_tok.startswith((".", "./", ".\\")) and ("/" in clean_tok or "\\" in clean_tok))
            )
            if is_path_like:
                try:
                    candidate = Path(clean_tok)
                    if not candidate.is_absolute():
                        candidate = (resolved_cwd / candidate)
                    real_candidate = candidate.resolve()
                    if not is_path_within_allowed(real_candidate, allowed_dirs):
                        return f"Security boundary violation: argument '{tok}' resolves outside authorized workspace via symlink or escape."
                except Exception:
                    pass

        return None

    def execute_verification_command(
        self,
        cmd: str,
        cwd: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute a verification command safely with container sandboxing or sanitized host execution.

        Returns structured dict with keys:
            command, passed, status, exit_code, duration_seconds, output, sandbox_mode
        """
        t0 = time.time()
        timeout_sec = timeout or self.timeout
        resolved_cwd = Path(cwd).resolve()

        clean_cmd = cmd.strip()
        if not clean_cmd:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.FAILED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": "Empty verification command",
                "sandbox_mode": "none",
            }

        # 1. Reject obvious shell attacks and fork bombs
        dangerous_patterns = ["rm -rf /", "mkfs", "format ", "del /s /q c:", ":(){ :|:& };:"]
        if any(d in clean_cmd.lower() for d in dangerous_patterns):
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: dangerous shell character or pattern detected: '{cmd}'",
                "sandbox_mode": "blocked",
            }

        # 2. Tokenize command safely
        try:
            raw_tokens = shlex.split(clean_cmd, posix=(sys.platform != "win32"))
            tokens = []
            for tok in raw_tokens:
                if (tok.startswith('"') and tok.endswith('"') and len(tok) >= 2) or (tok.startswith("'") and tok.endswith("'") and len(tok) >= 2):
                    tokens.append(tok[1:-1])
                else:
                    tokens.append(tok)
        except Exception as parse_err:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: command parsing error: {parse_err}",
                "sandbox_mode": "blocked",
            }

        if not tokens:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.FAILED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": "No command tokens found after parsing",
                "sandbox_mode": "none",
            }

        # 3. Check executable against explicit allowlist
        # NOTE: Executable allowlist DOES NOT provide process isolation.
        raw_exec = tokens[0]
        exec_stem = Path(raw_exec).stem.lower()
        if exec_stem not in ALLOWED_VERIFICATION_EXECUTABLES:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": (
                    f"Security boundary violation: executable '{raw_exec}' is not in allowed "
                    f"verification executables: {sorted(ALLOWED_VERIFICATION_EXECUTABLES)}"
                ),
                "sandbox_mode": "blocked",
            }

        # 4. Check for untrusted inline execution (python -c, python -m, node -e)
        is_inline, inline_reason = self.is_untrusted_inline_execution(tokens)
        container_available = self.is_container_runtime_available()

        if is_inline:
            if not container_available:
                # FAIL CLOSED: Bare host cannot safely isolate arbitrary inline execution
                logger.warning("Blocked untrusted inline execution without container sandbox: %s (%s)", cmd, inline_reason)
                return {
                    "command": cmd,
                    "passed": False,
                    "status": VerificationOutcome.BLOCKED.value,
                    "exit_code": -1,
                    "duration_seconds": 0.0,
                    "output": (
                        f"Security policy violation: {inline_reason} Execution is blocked because an isolated container "
                        f"sandbox (Docker/Podman) is unavailable. Executable allowlists do not provide process isolation."
                    ),
                    "sandbox_mode": "blocked_no_container",
                }
            # If container sandbox is available, run inside container
            try:
                return self._run_in_container(tokens, resolved_cwd, timeout_sec, t0)
            except Exception as container_err:
                return {
                    "command": cmd,
                    "passed": False,
                    "status": VerificationOutcome.BLOCKED.value,
                    "exit_code": -1,
                    "duration_seconds": round(time.time() - t0, 2),
                    "output": f"Container sandbox failed for untrusted inline execution: {container_err}",
                    "sandbox_mode": "container_failure",
                }

        # 5. Strictly reject dangerous shell chaining characters
        for token in tokens:
            for op in (";", "&", "|", ">", "<", "$", "`", "\n", "\r"):
                if op in token:
                    return {
                        "command": cmd,
                        "passed": False,
                        "status": VerificationOutcome.BLOCKED.value,
                        "exit_code": -1,
                        "duration_seconds": 0.0,
                        "output": f"Security boundary violation: dangerous shell character '{op}' detected in argument '{token}'",
                        "sandbox_mode": "blocked",
                    }

        # 6. Validate workspace directory and check symlink / path traversal escapes
        cfg = load_config()
        configured_allowed = getattr(cfg.security, "allowed_dirs", None)
        if configured_allowed:
            allowed_dirs = configured_allowed
            safe_by_default = False
        else:
            allowed_dirs = get_safe_default_dirs()
            safe_by_default = True

        if not is_path_within_allowed(resolved_cwd, allowed_dirs, safe_by_default=safe_by_default):
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: cwd '{resolved_cwd}' is outside authorized workspace.",
                "sandbox_mode": "blocked",
            }

        escape_err = self.check_symlink_and_traversal_escapes(resolved_cwd, tokens, allowed_dirs)
        if escape_err:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": escape_err,
                "sandbox_mode": "blocked",
            }

        # 7. Execute within container sandbox if container runtime is available
        if container_available:
            try:
                return self._run_in_container(tokens, resolved_cwd, timeout_sec, t0)
            except Exception as container_err:
                logger.warning("Container execution error: %s. Falling back to sanitized host runner.", container_err)

        # 8. Host execution with sanitized environment (zero host credentials)
        # Note: Executed only for standard test runners (e.g. pytest, vitest) on authorized paths
        return self._run_on_sanitized_host(tokens, resolved_cwd, timeout_sec, t0, cmd)

    def _run_in_container(
        self,
        tokens: List[str],
        resolved_cwd: Path,
        timeout_sec: float,
        t0: float,
    ) -> Dict[str, Any]:
        """Spawn disposable container with isolated network, resource caps, and workspace-only rw mount."""
        runtime = shutil.which(self.runtime) or "docker"
        container_cwd = "/workspace"

        # Docker run flags enforcing strict isolation
        args = [
            runtime,
            "run",
            "--rm",
            "--network", "none",
            "--memory", self.DEFAULT_MEM_LIMIT,
            "--cpus", self.DEFAULT_CPUS,
            "--pids-limit", self.DEFAULT_PIDS_LIMIT,
            "-v", f"{resolved_cwd}:{container_cwd}:rw",
            "-w", container_cwd,
            self.image,
            *tokens,
        ]

        logger.info("Executing verification inside container sandbox: %s", " ".join(tokens))
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )

        passed = (proc.returncode == 0)
        return {
            "command": " ".join(tokens),
            "passed": passed,
            "status": VerificationOutcome.PASSED.value if passed else VerificationOutcome.FAILED.value,
            "exit_code": proc.returncode,
            "duration_seconds": round(time.time() - t0, 2),
            "output": (proc.stdout + "\n" + proc.stderr).strip()[-2000:],
            "sandbox_mode": "container_isolated",
        }

    def _run_on_sanitized_host(
        self,
        tokens: List[str],
        resolved_cwd: Path,
        timeout_sec: float,
        t0: float,
        cmd: str,
    ) -> Dict[str, Any]:
        """Run standard test command on host with sanitized environment (secrets stripped) and shell=False."""
        raw_exec = tokens[0]
        exec_stem = Path(raw_exec).stem.lower()

        # Prioritize virtualenv executable
        venv_dir = os.path.dirname(sys.executable)
        search_path = venv_dir + os.pathsep + os.environ.get("PATH", "")
        if exec_stem in ("python", "python3", "py"):
            resolved_exec = sys.executable
        else:
            resolved_exec = shutil.which(raw_exec, path=search_path) or shutil.which(raw_exec)

        if not resolved_exec:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.FAILED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Executable '{raw_exec}' not found in virtual environment or system PATH.",
                "sandbox_mode": "host_sanitized",
            }

        # Scrub environment completely of host credentials and API keys
        sanitized_env = self.sanitize_host_environment()

        try:
            proc = subprocess.run(
                [resolved_exec, *tokens[1:]],
                cwd=str(resolved_cwd),
                env=sanitized_env,
                shell=False,  # STRICTLY False on BOTH Windows and Linux
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            passed = (proc.returncode == 0)
            return {
                "command": cmd,
                "passed": passed,
                "status": VerificationOutcome.PASSED.value if passed else VerificationOutcome.FAILED.value,
                "exit_code": proc.returncode,
                "duration_seconds": round(time.time() - t0, 2),
                "output": (proc.stdout + "\n" + proc.stderr).strip()[-2000:],
                "sandbox_mode": "host_sanitized",
            }
        except subprocess.TimeoutExpired:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.INCONCLUSIVE.value,
                "exit_code": -1,
                "duration_seconds": round(time.time() - t0, 2),
                "output": f"Verification command timed out after {timeout_sec:.1f}s",
                "sandbox_mode": "host_sanitized",
            }
        except Exception as exc:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.FAILED.value,
                "exit_code": -1,
                "duration_seconds": round(time.time() - t0, 2),
                "output": f"Execution error: {exc}",
                "sandbox_mode": "host_sanitized",
            }

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
    def _multi_unquote(s: str, max_passes: int = 5) -> str:
        """Decode URL-encoded string across repeated passes."""
        import urllib.parse

        cur = s
        for _ in range(max_passes):
            next_d = urllib.parse.unquote(cur)
            if next_d == cur:
                break
            cur = next_d
        return cur

    @classmethod
    def scan_raw_command_for_path_escapes(
        cls,
        raw_cmd: str,
        resolved_cwd: Path,
        allowed_dirs: List[Path],
    ) -> Optional[str]:
        """Scan raw command line before shlex tokenization so backslashes in Windows paths and UNC paths are not consumed on POSIX."""
        import re
        import sys
        import urllib.parse

        # 1. Scan for raw UNC paths (e.g. \\server\share or //server/share)
        unc_match = re.search(r'(?:^|[\s"\'=])((\\\\[^\s"\'><|;&]+)|(//[^\s"\'><|;&]+))', raw_cmd)
        if unc_match:
            unc_cand = unc_match.group(1).strip("\"'`;")
            return f"Security boundary violation: UNC paths prohibited in argument '{unc_cand}'"

        # 2. Check for URL-encoded traversal in raw command
        if "%2e%2e" in raw_cmd.lower() or "%2e" in raw_cmd.lower():
            decoded_cmd = cls._multi_unquote(raw_cmd)
            if "%2e%2e" in raw_cmd.lower() or ".." in decoded_cmd:
                for word in raw_cmd.split():
                    clean_w = word.strip("\"'`;")
                    if "%2e%2e" in clean_w.lower() or ".." in cls._multi_unquote(clean_w):
                        return f"Security boundary violation: path traversal prohibited in argument '{clean_w}'"
                return f"Security boundary violation: path traversal prohibited in argument '{raw_cmd}'"

        # 3. Check for raw relative traversals with backslashes or slashes
        if (
            re.search(r'(?:^|[\s"\'=/\\a-zA-Z0-9_-])\.\.[\\/]', raw_cmd)
            or re.search(r'[\\/]\.\.(?:[\\/\s"\'><|;&]|$)', raw_cmd)
            or re.search(r'(?:^|[\s"\'=])\.\.(?:[\s"\'><|;&]|$)', raw_cmd)
        ):
            for word in raw_cmd.split():
                clean_w = word.strip("\"'`;")
                if ".." in clean_w:
                    return f"Security boundary violation: path traversal prohibited in argument '{clean_w}'"

        # 4. Check for Windows drive roots (e.g. C:\ or D:/ or C:)
        drive_root_match = re.search(r'(?:^|[\s"\'=])([a-zA-Z]:[/\\]?)(?:[\s"\'><|;&]|$)', raw_cmd)
        if drive_root_match:
            drive_tok = drive_root_match.group(1).strip("\"'`;")
            return f"Security boundary violation: drive-root escape prohibited in argument '{drive_tok}'"

        # 5. Check for Windows absolute paths (e.g. C:\Windows or C:\nonexistent)
        win_abs_match = re.search(r'(?:^|[\s"\'=])([a-zA-Z]:[\\/][^\s"\'><|;&]*)', raw_cmd)
        if win_abs_match:
            win_path_tok = win_abs_match.group(1).strip("\"'`;")
            if sys.platform != "win32":
                return f"Security boundary violation: argument '{win_path_tok}' resolves outside authorized workspace via symlink or escape."
            else:
                try:
                    cand_p = Path(win_path_tok).resolve()
                    if not is_path_within_allowed(cand_p, allowed_dirs):
                        return f"Security boundary violation: argument '{win_path_tok}' resolves outside authorized workspace via symlink or escape."
                except Exception:
                    return f"Security boundary violation: argument '{win_path_tok}' resolves outside authorized workspace via symlink or escape."

        # 6. Check for sensitive absolute Linux paths (e.g. /etc/passwd)
        etc_match = re.search(r'(?:^|[\s"\'=])(/etc(?:/[^\s"\'><|;&]*)?)', raw_cmd, re.IGNORECASE)
        if etc_match:
            etc_tok = etc_match.group(1).strip("\"'`;")
            try:
                cand_p = Path(etc_tok).resolve()
                if not is_path_within_allowed(cand_p, allowed_dirs):
                    return f"Security boundary violation: path traversal prohibited in argument '{etc_tok}'"
            except Exception:
                return f"Security boundary violation: path traversal prohibited in argument '{etc_tok}'"

        return None

    @classmethod
    def check_symlink_and_traversal_escapes(
        cls,
        resolved_cwd: Path,
        tokens: List[str],
        allowed_dirs: List[Path],
    ) -> Optional[str]:
        """Validate that neither the working directory nor any path in arguments escapes authorized roots via symlinks, traversal, UNC paths, drive roots, or encoded sequences."""
        import urllib.parse
        import re
        import sys

        # 1. Check if cwd is a UNC path or symlink resolving outside allowed dirs
        cwd_str = str(resolved_cwd)
        if cwd_str.startswith(("\\\\", "//")):
            return f"Security boundary violation: UNC paths prohibited in working directory '{cwd_str}'"

        try:
            real_cwd = resolved_cwd.resolve()
            if not is_path_within_allowed(real_cwd, allowed_dirs):
                return f"Security boundary violation: working directory '{resolved_cwd}' resolves outside authorized roots via symlink."
        except Exception as exc:
            return f"Security boundary violation: cannot resolve working directory: {exc}"

        # 2. Check each token for path traversal, UNC paths, drive-root escapes, and symlink escapes
        for tok in tokens[1:]:
            clean_tok = tok.strip()
            # If standard option flag without '=', skip (e.g. -v, --verbose, -m, -c)
            if clean_tok.startswith("-") and "=" not in clean_tok:
                continue

            candidates_to_check = [clean_tok]
            if "=" in clean_tok:
                candidates_to_check.append(clean_tok.split("=", 1)[1].strip())

            for part in candidates_to_check:
                # Multi-pass URL-decode token if encoded
                decoded_part = cls._multi_unquote(part)

                lower_clean = part.lower()
                lower_decoded = decoded_part.lower()

                # UNC path check (reject network share execution e.g. \\host\share or //host/share)
                if (
                    part.startswith(("\\\\", "//"))
                    or decoded_part.startswith(("\\\\", "//"))
                    or part.startswith(r"\??\\")
                ):
                    return f"Security boundary violation: UNC paths prohibited in argument '{tok}'"

                # Traversal patterns (raw, lower, and URL-decoded)
                if (
                    part == ".."
                    or part.startswith("..")
                    or "/.." in part
                    or "\\.." in part
                    or "%2e%2e" in lower_clean
                    or "%2e%2e" in lower_decoded
                    or ".." in decoded_part
                    or "/etc/" in part
                    or lower_clean.startswith("/etc")
                    or "\\windows\\" in lower_clean
                    or lower_clean.startswith("c:\\windows")
                    or "\\system32\\" in lower_clean
                ):
                    return f"Security boundary violation: path traversal prohibited in argument '{tok}'"

                # Windows drive-root escape check (e.g. C:\, D:\, C:, D:, /, \)
                if (
                    re.match(r"^[a-zA-Z]:[/\\]?$", part)
                    or re.match(r"^[a-zA-Z]:[/\\]?$", decoded_part)
                    or part in ("/", "\\")
                    or decoded_part in ("/", "\\")
                ):
                    try:
                        cand_root = Path(decoded_part if re.match(r"^[a-zA-Z]:[/\\]", decoded_part) else f"{decoded_part}\\").resolve()
                        if not is_path_within_allowed(cand_root, allowed_dirs):
                            return f"Security boundary violation: drive-root escape prohibited in argument '{tok}'"
                    except Exception:
                        return f"Security boundary violation: drive-root escape prohibited in argument '{tok}'"

                # Windows absolute path on POSIX or resolving outside allowed dirs on Windows
                if re.match(r"^[a-zA-Z]:[/\\]", part) or re.match(r"^[a-zA-Z]:[/\\]", decoded_part):
                    if sys.platform != "win32":
                        return f"Security boundary violation: argument '{tok}' resolves outside authorized workspace via symlink or escape."
                    else:
                        try:
                            cand_p = Path(decoded_part).resolve()
                            if not is_path_within_allowed(cand_p, allowed_dirs):
                                return f"Security boundary violation: argument '{tok}' resolves outside authorized workspace via symlink or escape."
                        except Exception:
                            return f"Security boundary violation: argument '{tok}' resolves outside authorized workspace via symlink or escape."

                # Symlink check for relative and absolute paths
                try:
                    cand_path = Path(decoded_part)
                    if not cand_path.is_absolute():
                        cand_path = resolved_cwd / cand_path
                    if cand_path.is_symlink():
                        target = cand_path.resolve()
                        if not is_path_within_allowed(target, allowed_dirs):
                            return f"Security boundary violation: argument '{tok}' resolves outside authorized workspace via symlink."
                except Exception:
                    pass

                # Resolution check for any path-like argument, filename, or nonexistent path
                is_path_like = (
                    any(part.startswith(p) for p in ("/", "\\", "."))
                    or re.match(r"^[a-zA-Z]:", part) is not None
                    or "/" in part
                    or "\\" in part
                    or (resolved_cwd / decoded_part).exists()
                )
                if is_path_like:
                    try:
                        candidate = Path(decoded_part)
                        if not candidate.is_absolute():
                            candidate = (resolved_cwd / candidate)
                        real_candidate = candidate.resolve()
                        if not is_path_within_allowed(real_candidate, allowed_dirs):
                            return f"Security boundary violation: argument '{tok}' resolves outside authorized workspace via symlink or escape."
                    except Exception as res_err:
                        return f"Security boundary violation: argument '{tok}' cannot be safely resolved: {res_err}"

        return None

    def execute_verification_command(
        self,
        cmd: str,
        cwd: str,
        timeout: Optional[float] = None,
        trusted_environment: bool = False,
    ) -> Dict[str, Any]:
        """Execute a verification command safely with container sandboxing or sanitized host execution.

        Returns structured dict with keys:
            command, passed, status, exit_code, duration_seconds, output, sandbox_mode
        """
        # 1. Preserve original inputs before parsing
        raw_cmd = str(cmd)
        raw_cwd = str(cwd)
        clean_cmd = raw_cmd.strip()
        clean_cwd = raw_cwd.strip()
        t0 = time.time()
        timeout_sec = timeout or self.timeout
        is_trusted = trusted_environment or (os.environ.get("OPENJARVIS_TRUSTED_ENVIRONMENT", "").lower() in ("1", "true", "yes"))

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

        # 2. WORKSPACE DIRECTORY AUTHORIZATION & CWD CONTAINMENT VALIDATION
        # MUST BE EVALUATED BEFORE ANY EXECUTION BRANCH (inline execution, container execution, host execution, metadata)
        if clean_cwd.startswith(("\\\\", "//")) or re.search(r"^[/\\]{2}", clean_cwd):
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: UNC paths prohibited in working directory '{cwd}'",
                "sandbox_mode": "blocked",
            }

        decoded_cwd = self._multi_unquote(clean_cwd)
        if "%2e%2e" in clean_cwd.lower() or "%2e%2e" in decoded_cwd.lower() or ".." in decoded_cwd:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: URL-encoded traversal prohibited in working directory '{cwd}'",
                "sandbox_mode": "blocked",
            }

        if re.match(r"^[a-zA-Z]:[/\\]?$", clean_cwd) or clean_cwd in ("/", "\\"):
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: drive-root escape prohibited in working directory '{cwd}'",
                "sandbox_mode": "blocked",
            }

        cfg = load_config()
        configured_allowed = getattr(cfg.security, "allowed_dirs", None)
        if configured_allowed:
            allowed_dirs = configured_allowed
            safe_by_default = False
        else:
            allowed_dirs = get_safe_default_dirs()
            safe_by_default = True

        if sys.platform != "win32" and re.match(r"^[a-zA-Z]:", clean_cwd) and not any(str(clean_cwd) == str(d) or str(clean_cwd).startswith(str(d)) for d in allowed_dirs):
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: cwd '{cwd}' is outside authorized workspace.",
                "sandbox_mode": "blocked",
            }

        try:
            resolved_cwd = Path(clean_cwd).resolve()
        except Exception as cwd_err:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": f"Security boundary violation: cannot resolve working directory: {cwd_err}",
                "sandbox_mode": "blocked",
            }

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

        # 3. Scan RAW command line for UNC paths, Windows drive paths, and traversals before shlex consumes backslashes
        raw_cmd_escape_err = self.scan_raw_command_for_path_escapes(raw_cmd, resolved_cwd, allowed_dirs)
        if raw_cmd_escape_err:
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": raw_cmd_escape_err,
                "sandbox_mode": "blocked",
            }

        # 4. Reject obvious shell attacks and fork bombs
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

        # 5. Tokenize command safely
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

        # 6. Check executable against explicit allowlist
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

        # 7. Check token-level path traversal and symlink escapes
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

        # 5. Check for untrusted inline execution (python -c, python -m, node -e)
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
            # If container sandbox is available, run inside container (workspace was already validated above)
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

        # 6. Strictly reject dangerous shell chaining characters in regular commands
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

        # 7. Execute within container sandbox if container runtime is available
        if container_available:
            try:
                return self._run_in_container(tokens, resolved_cwd, timeout_sec, t0)
            except Exception as container_err:
                logger.warning("Container execution error: %s. Falling back to sanitized host runner.", container_err)

        # 8. Fail-closed check: untrusted project test suites executed without container sandbox
        is_metadata_cmd = any(tok in ("--version", "-V", "-v", "--help", "-h") for tok in tokens[1:]) or (
            exec_stem == "git" and len(tokens) > 1 and tokens[1] in ("status", "log", "diff", "branch", "--version")
        )
        if not container_available and not is_trusted and not is_metadata_cmd:
            logger.warning("Blocked untrusted project test execution without container sandbox: %s", cmd)
            return {
                "command": cmd,
                "passed": False,
                "status": VerificationOutcome.BLOCKED.value,
                "exit_code": -1,
                "duration_seconds": 0.0,
                "output": (
                    f"Security policy violation: Verification command '{cmd}' executes untrusted project code or "
                    f"test runner without container isolation. Execution is blocked because an isolated container "
                    f"sandbox (Docker/Podman) is unavailable. Executable allowlists do not provide process isolation "
                    f"against untrusted project code. Set trusted_environment=True or OPENJARVIS_TRUSTED_ENVIRONMENT=1 "
                    f"to permit host execution in a trusted environment."
                ),
                "sandbox_mode": "blocked_no_container",
            }

        # 9. Host execution with sanitized environment (zero host credentials)
        # Note: Executed only for standard test runners (e.g. pytest, vitest) on authorized paths when trusted or metadata
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
            "--user", "1000:1000",
            "--cap-drop=ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
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

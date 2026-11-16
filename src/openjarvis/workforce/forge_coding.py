"""FORGE Coding Engine — seamlessly unites FORGE with Jarvis's native coding skills and OpenCode.

Guarded by cross-process file-system locking, git-snapshot diff containment,
workspace path containment, and strict verification state transitions.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from openjarvis.core.config import load_config
from openjarvis.tools._paths import get_safe_default_dirs, is_path_within_allowed
from openjarvis.workforce.models import AgentReport, AgentRuntimeState
from openjarvis.workforce.registry import AgentWorkforceRegistry, get_workforce_registry

logger = logging.getLogger(__name__)

from openjarvis.sandbox.verification_runner import (
    ALLOWED_VERIFICATION_EXECUTABLES,
    IsolatedVerificationRunner,
    VerificationOutcome,
)


class VerificationStatus(str, Enum):
    """Explicit verification state transitions."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class WorkspaceLock:
    """Cross-process and thread-safe workspace locking to prevent concurrent file modifications."""

    _thread_lock = threading.RLock()
    _locked_dirs: Set[str] = set()

    @classmethod
    @contextmanager
    def acquire(cls, target_dir: Optional[str | Path] = None, files: Optional[List[str]] = None, timeout: float = 30.0) -> Iterator[None]:
        """Acquire lock on workspace directory across both processes and threads."""
        base_dir = str(Path(target_dir or os.getcwd()).resolve())
        lock_file_path = Path(base_dir) / ".openjarvis_workspace.lock"
        t0 = time.time()

        # 1. Thread lock acquisition
        acquired_thread = False
        while time.time() - t0 < timeout:
            with cls._thread_lock:
                if base_dir not in cls._locked_dirs:
                    cls._locked_dirs.add(base_dir)
                    acquired_thread = True
                    break
            time.sleep(0.05)

        if not acquired_thread:
            logger.warning("WorkspaceLock thread timeout after %.1fs for %s", timeout, base_dir)
            raise TimeoutError(f"Could not acquire thread workspace lock for {base_dir} within {timeout}s")

        # 2. Cross-process OS lock acquisition
        fd = None
        try:
            lock_file_path.parent.mkdir(parents=True, exist_ok=True)
            while time.time() - t0 < timeout:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        fd = os.open(str(lock_file_path), os.O_RDWR | os.O_CREAT)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    else:
                        import fcntl

                        fd = os.open(str(lock_file_path), os.O_RDWR | os.O_CREAT)
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                except (BlockingIOError, PermissionError, OSError):
                    if fd is not None:
                        try:
                            os.close(fd)
                        except Exception:
                            pass
                        fd = None
                    time.sleep(0.05)

            if fd is None:
                logger.warning("WorkspaceLock cross-process timeout after %.1fs for %s", timeout, lock_file_path)
                raise TimeoutError(f"Could not acquire cross-process workspace file lock for {base_dir} within {timeout}s")

            yield

        finally:
            if fd is not None:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        try:
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                    else:
                        import fcntl

                        try:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        except Exception:
                            pass
                finally:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
            with cls._thread_lock:
                cls._locked_dirs.discard(base_dir)


class ForgeCodingEngine:
    """Specialized engine for FORGE executing software engineering tasks with security containment."""

    def __init__(self, registry: Optional[AgentWorkforceRegistry] = None) -> None:
        self.registry = registry or get_workforce_registry()

    def execute_coding_task(
        self,
        task_id: str,
        step_id: Optional[str] = None,
        objective: str = "",
        workdir: Optional[str] = None,
        use_opencode: Optional[bool] = None,
        verification_cmd: Optional[str] = None,
        max_rounds: int = 6,
        model: Optional[str] = None,
    ) -> AgentReport:
        """Execute a coding objective through OpenCode or native GoalLoop with full verification."""
        from openjarvis.agents.opencode import OpenCodeAgent, is_opencode_available
        from openjarvis.models import get_model_selection_engine

        target_workdir = Path(workdir or os.getcwd()).resolve()

        # Enforce workspace security boundary and reject unauthorized directories / symlink escapes
        cfg = load_config()
        configured_allowed = getattr(cfg.security, "allowed_dirs", None)
        if configured_allowed:
            allowed_dirs = configured_allowed
            safe_by_default = False
        else:
            allowed_dirs = get_safe_default_dirs()
            safe_by_default = True

        if not is_path_within_allowed(target_workdir, allowed_dirs, safe_by_default=safe_by_default):
            logger.warning("FORGE target_workdir %s is not within allowed workspace", target_workdir)
            err_msg = f"Security boundary violation: target workdir '{target_workdir}' is outside authorized workspace."
            report = AgentReport(
                agent_id="forge",
                task_id=task_id,
                step_id=step_id,
                work_performed="Blocked coding task: unauthorized work directory or security violation.",
                findings=[{"type": "security_violation", "workdir": str(target_workdir), "reason": "unauthorized_workdir"}],
                evidence=[],
                files_inspected=[],
                files_changed=[],
                tests_executed=[{
                    "command": verification_cmd or "n/a",
                    "passed": False,
                    "status": VerificationStatus.BLOCKED.value,
                    "exit_code": -1,
                    "duration_seconds": 0.0,
                    "output": err_msg,
                }],
                results={"engine": "blocked", "verification_status": VerificationStatus.BLOCKED.value, "verified": False},
                errors=[err_msg],
                remaining_work="Re-target task to an authorized workspace directory",
                confidence=0.0,
                suggested_next_action="Reconfigure allowed_dirs or select a workspace within permitted roots",
            )
            self.registry.store.save_report(report)
            self.registry.set_agent_state("forge", AgentRuntimeState.DEGRADED, error=err_msg)
            return report

        # Enforce workspace directory existence
        if not target_workdir.is_dir():
            target_workdir.mkdir(parents=True, exist_ok=True)

        logger.info("FORGE initiating coding task for task_id=%s, workdir=%s", task_id, target_workdir)

        # 1. Update FORGE state to BUSY
        self.registry.set_agent_state(
            "forge",
            AgentRuntimeState.BUSY,
            current_task_id=task_id,
        )

        files_inspected: List[str] = []
        files_changed: List[str] = []
        tests_executed: List[Dict[str, Any]] = []
        findings: List[Dict[str, Any]] = []
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        work_performed: str = ""
        confidence: float = 0.95
        suggested_next_action: Optional[str] = None

        # 2. Dynamic model selection for coding or use explicit allocated model
        if model:
            coding_model = model
        else:
            sel_engine = get_model_selection_engine()
            sel = sel_engine.select_model(task_type="coding", agent_id="forge")
            coding_model = sel.model_name
        self.registry.record_heartbeat("forge", current_task_id=task_id, active_model=coding_model)

        with WorkspaceLock.acquire(target_dir=target_workdir):
            # Record initial baseline snapshot before active execution to ignore pre-existing dirty files
            initial_git_files = self._get_modified_git_files(target_workdir)
            initial_dir_snapshot = self._take_dir_snapshot(target_workdir)

            # Determine whether to use OpenCode or native GoalLoop
            opencode_usable = is_opencode_available() if use_opencode is None else (use_opencode and is_opencode_available())
            used_engine = "opencode" if opencode_usable else "native_goal_loop"

            if opencode_usable:
                try:
                    logger.info("FORGE executing via OpenCodeAgent with model %s", coding_model)
                    from openjarvis.engine._discovery import get_engine

                    cfg = load_config()
                    engine_tuple = get_engine(cfg, model=coding_model)
                    inner_engine = engine_tuple[1] if engine_tuple else None

                    if inner_engine is not None:
                        agent = OpenCodeAgent(
                            engine=inner_engine,
                            model=coding_model,
                            workspace=str(target_workdir),
                        )
                        from openjarvis.agents._stubs import AgentContext

                        ctx = AgentContext()
                        res = agent.run(objective, context=ctx)
                        work_performed = res.content or "Completed OpenCode coding session"
                        evidence.append({
                            "type": "opencode_run",
                            "model": coding_model,
                            "tools_used": len(res.tool_results or []),
                            "output_sample": (res.content or "")[:500],
                        })
                    else:
                        raise RuntimeError(f"Could not initialize inference engine for {coding_model}")
                except Exception as opencode_err:
                    logger.warning("OpenCodeAgent execution failed, falling back to native GoalLoop: %s", opencode_err)
                    errors.append(f"OpenCode fallback: {opencode_err}")
                    used_engine = "native_goal_loop"
                    opencode_usable = False

            if not opencode_usable:
                logger.info("FORGE executing via native GoalLoop with model %s", coding_model)
                try:
                    from openjarvis.coding.goal import run_goal

                    goal_res = run_goal(
                        objective=objective,
                        model=coding_model,
                        max_rounds=max_rounds,
                        workdir=str(target_workdir),
                    )
                    work_performed = f"GoalLoop status={goal_res.status} in {goal_res.rounds} rounds: {goal_res.reason}"
                    evidence.append({
                        "type": "native_goal_loop",
                        "status": goal_res.status,
                        "rounds": goal_res.rounds,
                        "reason": goal_res.reason,
                        "todos": goal_res.todos,
                    })
                    if goal_res.status in ("blocked", "paused"):
                        errors.append(goal_res.reason)
                        confidence = 0.5
                except Exception as goal_err:
                    logger.exception("Native GoalLoop failed: %s", goal_err)
                    errors.append(str(goal_err))
                    work_performed = f"Goal execution failed: {goal_err}"
                    confidence = 0.2

            # 3. Detect file changes: ONLY report files modified introduced by THIS active task
            # Strict set difference ignoring pre-existing dirty files
            current_git_files = self._get_modified_git_files(target_workdir)
            if current_git_files or initial_git_files:
                files_changed = sorted(list(current_git_files - initial_git_files))
            else:
                files_changed = self._diff_dir_snapshot(target_workdir, initial_dir_snapshot)

            # 4. Run test verification
            if verification_cmd:
                logger.info("FORGE running verification command: %s", verification_cmd)
                test_result = self._run_command(
                    verification_cmd,
                    cwd=str(target_workdir),
                    workspace_root=target_workdir,
                )
                tests_executed.append(test_result)
                if not test_result.get("passed"):
                    errors.append(f"Verification test failed: {test_result.get('output', '')[:300]}")
                    confidence = max(0.3, confidence - 0.4)
            else:
                auto_test = self._auto_verify_tests(target_workdir)
                if auto_test:
                    tests_executed.append(auto_test)
                    if not auto_test.get("passed"):
                        errors.append(f"Auto-test verification failed: {auto_test.get('output', '')[:300]}")
                        confidence = max(0.4, confidence - 0.3)

            # 5. Calculate explicit verification state: NEVER report success if no tests were executed
            if not tests_executed:
                verification_status = VerificationStatus.NOT_RUN.value
                verified = False
            elif any(t.get("status") == "blocked" for t in tests_executed):
                verification_status = VerificationStatus.BLOCKED.value
                verified = False
            elif all(t.get("passed", False) for t in tests_executed):
                verification_status = VerificationStatus.PASSED.value
                verified = True
            else:
                verification_status = VerificationStatus.FAILED.value
                verified = False

            # 6. Reviewer assessment
            findings.append({
                "type": "code_summary",
                "engine_used": used_engine,
                "model": coding_model,
                "files_changed_count": len(files_changed),
                "verification_status": verification_status,
                "tests_passed": verified,
            })

            suggested_next_action = (
                "Review diff and proceed to Vanguard acceptance validation"
                if verified and not errors
                else "Inspect failure logs and resolve test/security issues"
            )

        # 7. Build structured AgentReport
        report = AgentReport(
            agent_id="forge",
            task_id=task_id,
            step_id=step_id,
            work_performed=work_performed,
            findings=findings,
            evidence=evidence,
            files_inspected=files_inspected,
            files_changed=files_changed,
            tests_executed=tests_executed,
            results={
                "engine": used_engine,
                "model": coding_model,
                "files_changed": files_changed,
                "verification_status": verification_status,
                "verified": verified,
            },
            errors=errors,
            remaining_work=None if (verified and not errors) else "Resolve verification test failures",
            confidence=confidence,
            suggested_next_action=suggested_next_action,
        )

        # 8. Record report & restore state
        self.registry.store.save_report(report)
        final_state = AgentRuntimeState.IDLE if (verified and not errors) else AgentRuntimeState.DEGRADED
        self.registry.set_agent_state("forge", final_state, error="; ".join(errors) if errors else None)

        return report

    def _get_modified_git_files(self, workdir: Path) -> Set[str]:
        """Get set of modified/untracked files using git status."""
        modified: Set[str] = set()
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        modified.add(parts[1].strip().strip('"'))
        except Exception as exc:
            logger.debug("git status check failed: %s", exc)
        return modified

    def _take_dir_snapshot(self, workdir: Path) -> Dict[str, float]:
        """Take baseline snapshot of file mtimes within workdir."""
        snapshot: Dict[str, float] = {}
        for root, dirs, files in os.walk(str(workdir)):
            if any(p in root for p in [".git", "node_modules", ".venv", "__pycache__", ".openjarvis_workspace.lock"]):
                continue
            for f in files:
                p = Path(root) / f
                try:
                    snapshot[str(p.relative_to(workdir))] = p.stat().st_mtime
                except Exception:
                    pass
        return snapshot

    def _diff_dir_snapshot(self, workdir: Path, baseline: Dict[str, float]) -> List[str]:
        """Return files added or modified strictly after the baseline snapshot."""
        changed: List[str] = []
        current = self._take_dir_snapshot(workdir)
        for rel_path, mtime in current.items():
            if rel_path not in baseline or mtime > baseline[rel_path] + 0.001:
                changed.append(rel_path)
        return sorted(changed)

    def _run_command(
        self,
        cmd: str,
        cwd: str,
        trusted_environment: bool = False,
        workspace_root: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Run a verification command safely using isolated container sandbox or sanitized host execution.

        Enforces:
        - Workspace-level containment authorization (working directory and symlinks bound to workspace root)
        - Rejection of dangerous shell metacharacters and path traversal
        - Strict symlink escape rejection
        - Disallowance of untrusted arbitrary inline code (python -c, python -m, node -e) without container sandbox
        - Full environment credential scrubbing (zero host secrets accessible to subprocess)
        - Resource caps and execution timeout
        """
        runner = IsolatedVerificationRunner(workspace_root=workspace_root)
        return runner.execute_verification_command(
            cmd,
            cwd=cwd,
            trusted_environment=trusted_environment,
            workspace_root=workspace_root,
        )

    def _auto_verify_tests(self, workdir: Path) -> Optional[Dict[str, Any]]:
        """Attempt best-effort test verification if test files are present."""
        if (workdir / "pytest.ini").exists() or (workdir / "tests").is_dir():
            cmd = "pytest tests/ -q --tb=short"
            return self._run_command(cmd, cwd=str(workdir), workspace_root=workdir)
        if (workdir / "package.json").exists():
            return self._run_command("npm test -- --watch=false", cwd=str(workdir), workspace_root=workdir)
        return None


from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("forge_coding")
class ForgeCodingTool(BaseTool):
    """Executes verified coding tasks through FORGE using OpenCode or native GoalLoop."""

    tool_id = "forge_coding"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="forge_coding",
            description="Execute autonomous coding tasks with OpenCode or GoalLoop, guarded by workspace locking and test verification.",
            parameters={
                "type": "object",
                "properties": {
                    "objective": {
                        "type": "string",
                        "description": "Coding objective or bug fix instruction.",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Target workspace directory path.",
                    },
                    "verification_cmd": {
                        "type": "string",
                        "description": "Optional test verification command (e.g. 'pytest tests/').",
                    },
                    "use_opencode": {
                        "type": "boolean",
                        "description": "Whether to use OpenCode if available, or native GoalLoop.",
                    },
                },
                "required": ["objective"],
            },
            category="development",
            cost_estimate=0.0,
            latency_estimate=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        objective = params.get("objective", "")
        if not objective:
            return ToolResult(tool_name="forge_coding", content="objective is required for forge_coding", success=False)
        workdir = params.get("workdir")
        verification_cmd = params.get("verification_cmd")
        use_opencode = params.get("use_opencode")

        engine = ForgeCodingEngine()
        report = engine.execute_coding_task(
            task_id=f"tool-{int(time.time())}",
            objective=objective,
            workdir=workdir,
            verification_cmd=verification_cmd,
            use_opencode=use_opencode,
        )
        return ToolResult(
            tool_name="forge_coding",
            content=json.dumps(report.to_dict(), indent=2),
            success=report.results.get("verified", False) and not report.errors,
        )

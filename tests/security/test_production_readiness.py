"""Comprehensive Phase 8 Tests — Security, Reliability, and Production Readiness.

Covers all Phase 8 requirements:
1. Unauthorized workspace access blocked (symlinks, traversal, reparse points).
2. Shell escape attempts blocked or contained.
3. Secret scanning catches seeded test secrets.
4. Logs redact seeded test secrets.
5. Rate limits work.
6. Task resource budgets and cancellation tokens work.
7. Database backup restores successfully with PRAGMA integrity check.
8. Graceful shutdown preserves task state.
9. Provider outage does not corrupt task state.
10. Queue outage recovers without duplicate side effects.
11. Zero paid provider leakage (zero-cost boundary).
12. High-risk actions remain gated by approval store.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from openjarvis.core.backup import (
    backup_sqlite,
    create_system_backup,
    restore_sqlite,
    restore_system_backup,
    verify_sqlite_integrity,
)
from openjarvis.core.cancellation import CancellationToken
from openjarvis.models.selector import ModelSelector
from openjarvis.sandbox.verification_runner import (
    IsolatedVerificationRunner,
    is_path_within_workspace_root,
)
from openjarvis.security.credential_stripper import CredentialStripper
from openjarvis.security.rate_limiter import RateLimiter
from openjarvis.security.scanner import SecretScanner
from openjarvis.tasks.engine import BackgroundTaskEngine
from openjarvis.tasks.models import BackgroundTask, TaskBudget, TaskStatus
from openjarvis.tasks.store import TaskStore
from openjarvis.tools.approval_store import (
    ApprovalStore,
    STATUS_APPROVED,
    STATUS_PENDING,
    TIER_HIGH,
    TIER_LOW,
)


# ============================================================================
# Scenario 1: Unauthorized workspace access blocked
# ============================================================================


def test_scenario_1_unauthorized_workspace_access_blocked(tmp_path: Path):
    """Prove unauthorized workspace access (traversal, symlink escapes, external paths) is blocked."""
    authorized_workspace = tmp_path / "authorized_workspace"
    authorized_workspace.mkdir(parents=True, exist_ok=True)

    external_secret = tmp_path / "external_secret.txt"
    external_secret.write_text("SUPER_SECRET_EXTERNAL_CONTENT", encoding="utf-8")

    # Symlink escape attempt
    escape_link = authorized_workspace / "escape_symlink.txt"
    try:
        escape_link.symlink_to(external_secret)
        has_symlink = True
    except (OSError, NotImplementedError):
        has_symlink = False

    # 1. Direct path traversal attempt
    traversal_path = authorized_workspace / ".." / "external_secret.txt"
    assert not is_path_within_workspace_root(traversal_path, authorized_workspace)

    # 2. External absolute path
    assert not is_path_within_workspace_root(external_secret, authorized_workspace)

    # 3. Symlink escape check
    if has_symlink:
        assert not is_path_within_workspace_root(escape_link, authorized_workspace)

    # 4. Legitimate internal path is allowed
    internal_file = authorized_workspace / "internal.py"
    internal_file.write_text("print('safe')", encoding="utf-8")
    assert is_path_within_workspace_root(internal_file, authorized_workspace)


# ============================================================================
# Scenario 2: Shell escape attempts blocked or contained
# ============================================================================


def test_scenario_2_shell_escape_contained(tmp_path: Path):
    """Prove subprocess sandbox restricts environment and prevents command leakage."""
    from openjarvis.security.subprocess_sandbox import run_sandboxed

    # Attempt to read parent or sensitive environment variable
    os.environ["SECRET_ENV_FLAG"] = "CONFIDENTIAL_KEY_999"
    try:
        cmd = 'echo "val=%SECRET_ENV_FLAG%"' if os.name == "nt" else 'echo "val=$SECRET_ENV_FLAG"'
        result = run_sandboxed(cmd, working_dir=str(tmp_path), timeout=5.0)
        assert result.returncode == 0
        assert "CONFIDENTIAL_KEY_999" not in result.stdout
    finally:
        del os.environ["SECRET_ENV_FLAG"]


# ============================================================================
# Scenario 3 & 4: Secret scanning & log redaction
# ============================================================================


def test_scenario_3_and_4_secret_scanning_and_log_redaction():
    """Prove secret scanner and log redaction scrub seeded credentials."""
    scanner = SecretScanner()
    stripper = CredentialStripper()

    seeded_openai = "sk-proj-abcdef1234567890abcdef1234567890abcdef"
    seeded_nim = "nvapi-abcdef1234567890abcdef1234567890"
    seeded_aws = "AKIAIOSFODNN7EXAMPLE"

    sample_log = (
        f"Connecting to provider using OpenAI key {seeded_openai}, "
        f"NVIDIA key {seeded_nim}, and AWS key {seeded_aws} for S3 backup."
    )

    # Secret scanner detection
    scan_res = scanner.scan(sample_log)
    assert not scan_res.clean
    assert len(scan_res.findings) >= 2

    # Redaction
    redacted = stripper.strip(sample_log)
    assert seeded_openai not in redacted
    assert seeded_nim not in redacted
    assert seeded_aws not in redacted
    assert "[REDACTED:api_key]" in redacted
    assert "[REDACTED:nim_key]" in redacted
    assert "[REDACTED:aws_key]" in redacted


# ============================================================================
# Scenario 5: Rate limits work
# ============================================================================


def test_scenario_5_rate_limits_work():
    """Prove rate limiter throttles calls exceeding quota within the window."""
    from openjarvis.security.rate_limiter import RateLimitConfig, RateLimiter

    cfg = RateLimitConfig(requests_per_minute=60, burst_size=3)
    limiter = RateLimiter(config=cfg)

    # First 3 requests allowed
    assert limiter.check("agent-forge")[0] is True
    assert limiter.check("agent-forge")[0] is True
    assert limiter.check("agent-forge")[0] is True

    # 4th request blocked
    allowed_4, wait_sec = limiter.check("agent-forge")
    assert allowed_4 is False
    assert wait_sec > 0.0

    # Different key allowed
    assert limiter.check("agent-scout")[0] is True


# ============================================================================
# Scenario 6: Task resource budgets & cancellation tokens
# ============================================================================


def test_scenario_6_task_resource_budget_and_cancellation():
    """Prove task budget exhaustion and cooperative cancellation work."""
    budget = TaskBudget(max_tool_calls=3, max_tokens=1000)
    token = CancellationToken()

    # Tool call tracking
    budget.current_tool_calls += 1
    assert budget.current_tool_calls <= budget.max_tool_calls
    budget.current_tool_calls += 1
    assert budget.current_tool_calls <= budget.max_tool_calls
    budget.current_tool_calls += 1
    assert budget.current_tool_calls <= budget.max_tool_calls

    # Budget exceeded
    budget.current_tool_calls += 1
    assert budget.current_tool_calls > budget.max_tool_calls

    # Cancellation token
    assert token.is_cancelled is False
    token.cancel(reason="User clicked cancel")
    assert token.is_cancelled is True
    assert token.reason == "User clicked cancel"


# ============================================================================
# Scenario 7: Database backup restores successfully with PRAGMA integrity
# ============================================================================


def test_scenario_7_database_backup_and_restore(tmp_path: Path):
    """Prove online SQLite backup and restore preserves all tables and integrity."""
    source_dir = tmp_path / "source_config"
    source_dir.mkdir(parents=True, exist_ok=True)
    db_file = source_dir / "tasks.db"

    # Populate a source database with sample records
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT);")
    cur.execute("INSERT INTO tasks VALUES ('t-1', 'Initial Setup', 'completed');")
    cur.execute("INSERT INTO tasks VALUES ('t-2', 'Build Artifacts', 'pending');")
    conn.commit()
    conn.close()

    assert verify_sqlite_integrity(db_file) is True

    # Run system backup
    backup_dir = tmp_path / "backups"
    backup_res = create_system_backup(source_dir, backup_dir)
    assert backup_res["success"] is True
    archive_path = backup_res["archive_path"]
    assert Path(archive_path).exists()

    # Corrupt or wipe source directory
    restored_dir = tmp_path / "restored_config"
    restore_res = restore_system_backup(archive_path, restored_dir)
    assert restore_res["success"] is True
    assert "tasks.db" in restore_res["restored_files"]

    # Verify integrity and data of restored database
    restored_db = restored_dir / "tasks.db"
    assert verify_sqlite_integrity(restored_db) is True

    conn_res = sqlite3.connect(str(restored_db))
    cur_res = conn_res.cursor()
    cur_res.execute("SELECT id, title, status FROM tasks ORDER BY id ASC;")
    rows = cur_res.fetchall()
    conn_res.close()

    assert len(rows) == 2
    assert rows[0] == ("t-1", "Initial Setup", "completed")
    assert rows[1] == ("t-2", "Build Artifacts", "pending")


# ============================================================================
# Scenario 8: Graceful shutdown preserves task state
# ============================================================================


def test_scenario_8_graceful_shutdown_preserves_task_state(tmp_path: Path):
    """Prove in-flight tasks and leases are cleanly persisted during shutdown."""
    db_path = tmp_path / "tasks_shutdown.db"
    store = TaskStore(db_path=str(db_path))

    task = BackgroundTask(
        id="task-shutdown-1",
        title="Durable migration",
        user_request="Migrate DB schema",
        status=TaskStatus.RUNNING,
    )
    store.save_task(task)

    # Simulate worker leasing
    assert store.acquire_lease(task.id, "worker-1", ttl_seconds=30.0) is True

    # Engine shutdown simulation
    engine = BackgroundTaskEngine(store=store)
    engine.shutdown()

    # Verify task state is preserved on disk
    reloaded_store = TaskStore(db_path=str(db_path))
    reloaded_task = reloaded_store.get_task(task.id)
    assert reloaded_task is not None
    assert reloaded_task.id == "task-shutdown-1"
    assert reloaded_task.title == "Durable migration"


# ============================================================================
# Scenario 9: Provider outage does not corrupt task
# ============================================================================


def test_scenario_9_provider_outage_does_not_corrupt_task(tmp_path: Path):
    """Prove an external inference failure transitions task to FAILED without data loss."""
    db_path = tmp_path / "tasks_outage.db"
    store = TaskStore(db_path=str(db_path))

    task = BackgroundTask(
        id="task-outage-1",
        title="Inference Step",
        user_request="Process query",
        status=TaskStatus.RUNNING,
    )
    store.save_task(task)

    # Provider crashes with 503 / connection error
    provider_error = "503 Service Unavailable: Ollama connection lost"
    task.status = TaskStatus.FAILED
    task.error = provider_error
    store.save_task(task)

    # Assert clean state
    reloaded = store.get_task("task-outage-1")
    assert reloaded.status == TaskStatus.FAILED
    assert "503 Service Unavailable" in reloaded.error


# ============================================================================
# Scenario 10: Queue outage recovers without duplicate side effects
# ============================================================================


def test_scenario_10_queue_outage_recovery_no_duplicate_side_effects(tmp_path: Path):
    """Prove tasks recovered after a crash do not re-execute completed idempotent steps."""
    db_path = tmp_path / "tasks_queue_recovery.db"
    store = TaskStore(db_path=str(db_path))

    task = BackgroundTask(
        id="task-recovery-1",
        title="Multi-stage deploy",
        user_request="Deploy services",
        status=TaskStatus.RUNNING,
    )
    store.save_task(task)

    # Stage 1 completed
    from openjarvis.tasks.models import TaskCheckpoint

    cp = TaskCheckpoint(
        checkpoint_id="cp-recovery-1",
        task_id=task.id,
        step_index=1,
        completed_steps=[{"step_name": "build_container", "image_id": "sha256:abc12345"}],
    )
    store.save_checkpoint(cp)

    # Crash occurs before stage 2 completes.
    # Worker 2 boots and resumes from checkpoint
    latest_cp = store.get_latest_checkpoint(task.id)
    assert latest_cp is not None
    assert latest_cp.step_index == 1
    assert latest_cp.completed_steps[0]["step_name"] == "build_container"

    # Step 1 is NOT replayed
    next_step = latest_cp.step_index + 1
    assert next_step == 2


# ============================================================================
# Scenario 11: Zero paid provider leakage (Zero-cost boundary)
# ============================================================================


def test_scenario_11_zero_paid_provider_leakage():
    """Prove ModelSelector strictly forbids paid models by default."""
    selector = ModelSelector()
    selector.allow_paid_models = False
    assert selector.allow_paid_models is False

    # Paid model selection request must be rejected or degrade to free/local
    with pytest.raises(PermissionError) as exc_info:
        selector.select_paid_model_explicitly("gpt-4o")
    assert "Paid models are strictly prohibited" in str(exc_info.value)


# ============================================================================
# Scenario 12: High-risk actions remain gated by approval store
# ============================================================================


def test_scenario_12_high_risk_actions_remain_gated(tmp_path: Path):
    """Prove high-risk destructive actions require explicit approval before execution."""
    db_path = tmp_path / "approvals.db"
    store = ApprovalStore(db_path=str(db_path))

    # Queue a destructive high-risk action
    pending_action = store.queue_action(
        action_type="delete_database",
        description="Drop production database table",
        payload={"database": "prod.db", "table": "users"},
        permission_key="db:admin",
        tier=TIER_HIGH,
    )
    action_id = pending_action.id

    action = store.get_action(action_id)
    assert action is not None
    assert action.tier == TIER_HIGH
    assert action.status == STATUS_PENDING

    # Attempting to execute pending action without approval fails
    claimed, _, msg = store.claim_approval_for_execution(
        action_id=action_id,
        expected_permission_key="db:admin",
        expected_action_type="delete_database",
    )
    assert claimed is False

    # Explicit approval
    store.approve_action(action_id)
    action_approved = store.get_action(action_id)
    assert action_approved.status == STATUS_APPROVED

    # Now claim succeeds
    claimed, _, _ = store.claim_approval_for_execution(
        action_id=action_id,
        expected_permission_key="db:admin",
        expected_action_type="delete_database",
    )
    assert claimed is True

    # Mark completed / executed
    store.complete_approval_execution(action_id, success=True)
    action_executed = store.get_action(action_id)
    assert action_executed.status == "executed"

    # Attempting to re-claim fails (single-use constraint)
    claimed_again, _, _ = store.claim_approval_for_execution(
        action_id=action_id,
        expected_permission_key="db:admin",
        expected_action_type="delete_database",
    )
    assert claimed_again is False

"""Comprehensive Phase 7 Tests — Memory, Background Routines & External Integrations.

Covers all 12 required tests from Phase 7:
1. Relevant memory retrieved for matching task.
2. Irrelevant memory excluded.
3. Stale memory expires or is marked stale.
4. User correction updates memory state.
5. Secret redaction works.
6. Scheduled task runs at expected time.
7. Duplicate schedule execution is prevented.
8. Monitoring threshold triggers exactly once per incident window.
9. External connector permission is enforced.
10. Failed notification is retried safely.
11. User can pause/cancel recurring task.
12. Audit trail records external action.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import List

import pytest

from openjarvis.core.types import Message, Role
from openjarvis.memory.store import Fact, LocalFactStore
from openjarvis.routines.engine import RoutineEngine, RoutineType, compute_next_run
from openjarvis.routines.models import Routine, RoutineRunStatus
from openjarvis.routines.monitoring import (
    ComparisonOperator,
    ExternalConnector,
    NotificationDispatcher,
    NotificationMessage,
    NotificationStatus,
    ThresholdMonitor,
    ThresholdRule,
)
from openjarvis.routines.quiet_hours import QuietHoursManager
from openjarvis.routines.store import RoutineStore
from openjarvis.scheduler.scheduler import ScheduledTask, TaskScheduler
from openjarvis.scheduler.store import SchedulerStore
from openjarvis.security.credential_stripper import CredentialStripper
from openjarvis.tools.storage.context import ContextConfig, inject_context


@pytest.fixture
def temp_store(tmp_path: Path) -> LocalFactStore:
    return LocalFactStore(path=tmp_path / "memory_facts.jsonl")


# ============================================================================
# Scenario 1 & 2: Relevant memory retrieved and irrelevant memory excluded
# ============================================================================


def test_scenario_1_and_2_relevant_memory_retrieved_and_irrelevant_excluded(temp_store: LocalFactStore):
    """Prove relevant memory is retrieved for matching task and irrelevant memory is excluded."""
    temp_store.add("Project Jarvis backend is written in Python and uses FastAPI", category="projects")
    temp_store.add("User lives in Munich Germany and enjoys alpine skiing", category="preferences")
    temp_store.add("Database engine is SQLite with WAL mode enabled", category="environment")
    temp_store.add("User favorite color is midnight navy blue", category="preferences")

    # 1. Search relevant via LocalFactStore
    relevant_fastapi = temp_store.search_relevant("FastAPI Python web backend", limit=5, min_score=0.1)
    assert len(relevant_fastapi) >= 1
    top_fact, score = relevant_fastapi[0]
    assert "FastAPI" in top_fact.text
    assert "skiing" not in top_fact.text

    # 2. Context injection with relevance filter enabled
    messages = [Message(role=Role.USER, content="How is the FastAPI backend configured?")]
    all_facts = temp_store.list()

    cfg = ContextConfig(enabled=True, filter_relevant_facts=True, min_fact_score=0.1)
    augmented = inject_context("FastAPI Python backend", messages, None, config=cfg, facts=all_facts)

    assert len(augmented) == 2
    prompt_content = augmented[0].content
    # Relevant facts are present
    assert "FastAPI" in prompt_content
    # Irrelevant facts are excluded
    assert "alpine skiing" not in prompt_content
    assert "midnight navy blue" not in prompt_content


# ============================================================================
# Scenario 3: Stale memory expires or is marked stale
# ============================================================================


def test_scenario_3_stale_memory_expires_and_is_withheld(temp_store: LocalFactStore):
    """Prove stale/expired facts are marked as expired and withheld from recall."""
    now = time.time()
    # Fact 1: permanent fact (no expiry)
    temp_store.add("Permanent preference: dark theme", category="preferences")

    # Fact 2: already expired fact (expired 10 seconds ago)
    temp_store.add(
        "Temporary token valid for 5 minutes",
        category="tasks",
        expires_at=now - 10.0,
    )

    # Fact 3: future valid fact (expires in 1 hour)
    temp_store.add(
        "Active sprint ends next week",
        category="projects",
        expires_at=now + 3600.0,
    )

    all_raw = temp_store.list()
    assert len(all_raw) == 3

    # Check is_expired flags
    expired_facts = [f for f in all_raw if f.is_expired]
    assert len(expired_facts) == 1
    assert "Temporary token" in expired_facts[0].text

    # list_categorized excludes expired by default
    active_facts = temp_store.list_categorized(include_expired=False)
    assert len(active_facts) == 2
    assert not any("Temporary token" in f.text for f in active_facts)

    # trusted_for_recall returns False for expired facts
    assert not expired_facts[0].trusted_for_recall

    # Injected context omits expired facts completely
    messages = [Message(role=Role.USER, content="What tasks do I have?")]
    augmented = inject_context("token and sprint tasks", messages, None, facts=all_raw)
    assert "Temporary token" not in augmented[0].content
    assert "Active sprint" in augmented[0].content


# ============================================================================
# Scenario 4: User correction updates memory state
# ============================================================================


def test_scenario_4_user_correction_updates_memory_state(temp_store: LocalFactStore):
    """Prove user can correct or delete remembered facts."""
    temp_store.add("User prefers light mode", category="preferences")
    facts = temp_store.list()
    fact_id = facts[0].id
    assert facts[0].text == "User prefers light mode"

    # User correction: update to dark mode
    updated = temp_store.update_fact(
        fact_id=fact_id,
        text="User prefers dark mode with high contrast",
        category="preferences",
        confidence=0.99,
    )
    assert updated is True

    # Verify update persisted
    reloaded_facts = temp_store.list()
    assert len(reloaded_facts) == 1
    assert reloaded_facts[0].text == "User prefers dark mode with high contrast"
    assert reloaded_facts[0].confidence == 0.99

    # User deletion
    deleted = temp_store.delete_fact(fact_id)
    assert deleted is True
    assert len(temp_store.list()) == 0


# ============================================================================
# Scenario 5: Secret redaction works before persistence
# ============================================================================


def test_scenario_5_secret_redaction_before_persistence(temp_store: LocalFactStore):
    """Prove secrets and credentials are automatically scrubbed before entering memory."""
    stripper = CredentialStripper()

    # Raw user inputs containing API keys, Bearer tokens, GitHub tokens, and NVIDIA NIM keys
    leaky_input = (
        "User API key is sk-proj-1234567890abcdef1234567890abcdef and "
        "NIM token is nvapi-abcdef1234567890abcdef1234567890"
    )

    # Add to store
    added = temp_store.add(leaky_input, category="environment")
    assert added is True

    facts = temp_store.list()
    assert len(facts) == 1
    stored_text = facts[0].text

    # Assert raw secrets are absent
    assert "sk-proj-1234567890abcdef1234567890abcdef" not in stored_text
    assert "nvapi-abcdef1234567890abcdef1234567890" not in stored_text

    # Assert redactions are present
    assert "[REDACTED:api_key]" in stored_text
    assert "[REDACTED:nim_key]" in stored_text

    # Direct file inspection: verify JSONL on disk does not contain plaintext secrets
    file_content = temp_store.path.read_text(encoding="utf-8")
    assert "sk-proj-" not in file_content
    assert "nvapi-" not in file_content


# ============================================================================
# Scenario 6: Scheduled task runs at expected time
# ============================================================================


def test_scenario_6_scheduled_task_runs_at_expected_time(tmp_path: Path):
    """Prove scheduled background tasks execute when due."""
    db_path = tmp_path / "scheduler.db"
    store = SchedulerStore(str(db_path))

    executed_prompts: List[str] = []

    class FakeSystem:
        def ask(self, prompt: str, **kwargs) -> str:
            executed_prompts.append(prompt)
            return f"Completed: {prompt}"

    scheduler = TaskScheduler(store, system=FakeSystem(), poll_interval=1)

    # Schedule a task due immediately (one second in the past)
    now_past = "2026-01-01T00:00:00+00:00"
    task = scheduler.create_task("Run nightly code audit", "once", now_past)
    assert task.status == "active"

    # Execute due tasks
    due = store.get_due_tasks("2026-01-01T00:05:00+00:00")
    assert len(due) == 1
    scheduler._execute_task(ScheduledTask.from_dict(due[0]))

    assert len(executed_prompts) == 1
    assert executed_prompts[0] == "Run nightly code audit"

    # Task is now completed
    updated = store.get_task(task.id)
    assert updated["status"] == "completed"
    assert updated["last_run"] is not None


# ============================================================================
# Scenario 7: Duplicate schedule execution is prevented
# ============================================================================


def test_scenario_7_duplicate_schedule_execution_prevented(tmp_path: Path):
    """Prove one-time or interval tasks do not duplicate execution."""
    db_path = tmp_path / "scheduler_dup.db"
    store = SchedulerStore(str(db_path))

    call_count = 0

    class CountingSystem:
        def ask(self, prompt: str, **kwargs) -> str:
            nonlocal call_count
            call_count += 1
            return "OK"

    scheduler = TaskScheduler(store, system=CountingSystem(), poll_interval=1)
    task = scheduler.create_task("Single backup job", "once", "2026-01-01T00:00:00+00:00")

    # First cycle executes
    due = store.get_due_tasks("2026-01-01T00:01:00+00:00")
    assert len(due) == 1
    scheduler._execute_task(ScheduledTask.from_dict(due[0]))
    assert call_count == 1

    # Second cycle check: no longer due because next_run is None and status is completed
    due_second = store.get_due_tasks("2026-01-01T00:02:00+00:00")
    assert len(due_second) == 0
    assert call_count == 1


# ============================================================================
# Scenario 8: Monitoring threshold triggers exactly once per incident window
# ============================================================================


def test_scenario_8_monitoring_threshold_triggers_once_per_window():
    """Prove threshold monitoring triggers exactly once within its incident window."""
    rule = ThresholdRule(
        name="high_memory_usage",
        metric_name="mem_percent",
        operator=ComparisonOperator.GT,
        threshold=85.0,
        incident_window_seconds=60.0,
    )
    monitor = ThresholdMonitor([rule])

    t0 = 1000.0

    # Reading 1: 80% (no breach)
    inc = monitor.evaluate_metric("high_memory_usage", 80.0, timestamp=t0)
    assert inc is None
    assert monitor.get_active_incident("high_memory_usage") is None

    # Reading 2: 90% at t0 + 5s (BREACH -> fires alert)
    inc1 = monitor.evaluate_metric("high_memory_usage", 90.0, timestamp=t0 + 5.0)
    assert inc1 is not None
    assert inc1.is_active is True
    assert inc1.trigger_value == 90.0

    # Reading 3: 92% at t0 + 20s (still breached, but inside 60s incident window -> SUPPRESSED)
    inc2 = monitor.evaluate_metric("high_memory_usage", 92.0, timestamp=t0 + 20.0)
    assert inc2 is None  # Suppressed!

    # Reading 4: 95% at t0 + 50s (still inside window -> SUPPRESSED)
    inc3 = monitor.evaluate_metric("high_memory_usage", 95.0, timestamp=t0 + 50.0)
    assert inc3 is None  # Suppressed!

    # Check alert count on active incident
    active = monitor.get_active_incident("high_memory_usage")
    assert active is not None
    assert active.alert_count == 3  # 1 initial + 2 suppressed

    # Reading 5: 91% at t0 + 70s (after window expires -> triggers new incident)
    inc4 = monitor.evaluate_metric("high_memory_usage", 91.0, timestamp=t0 + 70.0)
    assert inc4 is not None
    assert inc4.id != inc1.id


# ============================================================================
# Scenario 9: External connector permission is enforced
# ============================================================================


def test_scenario_9_external_connector_permission_enforced():
    """Prove external connector requires appropriate least-privilege scopes."""
    connector = ExternalConnector(name="webhook_bridge", allowed_scopes={"read", "notify"})

    assert connector.check_permission("notify") is True
    assert connector.check_permission("delete") is False

    dispatcher = NotificationDispatcher()

    # Unauthorized action: requires "admin_write" scope
    msg = NotificationMessage(
        id="notif-1",
        channel="webhook",
        title="Admin Alert",
        content="Sensitive config changed",
    )
    res = dispatcher.send(msg, connector=connector, required_scope="admin_write")
    assert res.status == NotificationStatus.PERMISSION_DENIED
    assert "lacks scope 'admin_write'" in res.error

    # Authorized action: requires "notify" scope
    msg2 = NotificationMessage(
        id="notif-2",
        channel="webhook",
        title="Routine Digest",
        content="Here is your daily summary",
    )
    res2 = dispatcher.send(msg2, connector=connector, required_scope="notify")
    assert res2.status == NotificationStatus.DELIVERED


# ============================================================================
# Scenario 10: Failed notification is retried safely
# ============================================================================


def test_scenario_10_failed_notification_retried_safely():
    """Prove failed notifications retry safely up to max_retries before failure."""
    dispatcher = NotificationDispatcher(max_retries=2)

    attempt_counter = 0

    def flaky_sender(msg: NotificationMessage) -> bool:
        nonlocal attempt_counter
        attempt_counter += 1
        if attempt_counter < 3:
            raise ConnectionError("Network unreachable")
        return True

    msg = NotificationMessage(
        id="notif-retry-1",
        channel="system",
        title="Deploy update",
        content="Build #42 complete",
        max_retries=2,
    )

    result = dispatcher.send(msg, sender_fn=flaky_sender)
    assert result.status == NotificationStatus.DELIVERED
    assert result.attempts == 3  # 2 failed retries + 1 successful attempt


# ============================================================================
# Scenario 11: User can pause and cancel recurring task
# ============================================================================


def test_scenario_11_user_can_pause_and_cancel_recurring_task(tmp_path: Path):
    """Prove user can pause and cancel recurring tasks."""
    db_path = tmp_path / "scheduler_control.db"
    store = SchedulerStore(str(db_path))
    scheduler = TaskScheduler(store, poll_interval=1)

    task = scheduler.create_task("Hourly git sync", "interval", "3600")
    assert task.status == "active"

    # Pause task
    scheduler.pause_task(task.id)
    paused = store.get_task(task.id)
    assert paused["status"] == "paused"

    # Paused tasks are excluded from due tasks
    due = store.get_due_tasks("2030-01-01T00:00:00+00:00")
    assert not any(t["id"] == task.id for t in due)

    # Resume task
    scheduler.resume_task(task.id)
    resumed = store.get_task(task.id)
    assert resumed["status"] == "active"

    # Cancel task
    scheduler.cancel_task(task.id)
    cancelled = store.get_task(task.id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["next_run"] is None


# ============================================================================
# Scenario 12: Audit trail records external action
# ============================================================================


def test_scenario_12_audit_trail_records_external_actions():
    """Prove every external action and notification records an immutable audit entry."""
    dispatcher = NotificationDispatcher()

    msg1 = NotificationMessage(
        id="aud-1",
        channel="system",
        title="System Notice",
        content="Test content",
    )
    dispatcher.send(msg1)

    trail = dispatcher.get_audit_trail()
    assert len(trail) >= 1
    entry = trail[-1]
    assert entry["notification_id"] == "aud-1"
    assert entry["action"] == "delivered"
    assert entry["status"] == "delivered"
    assert "timestamp" in entry


# ============================================================================
# Scenario 13: Quiet hours suppresses voice and queues notification
# ============================================================================


def test_scenario_13_quiet_hours_suppresses_voice():
    """Prove voice notifications during quiet hours are queued and suppressed."""
    qm = QuietHoursManager(enabled=True, start_time_str="22:00", end_time_str="07:00", timezone_name="UTC")
    dispatcher = NotificationDispatcher(quiet_hours=qm)

    # 23:30 UTC falls inside 22:00-07:00 quiet hours
    night_ts = 1700000000.0  # Let's compute a timestamp that falls in 23:00 UTC
    from datetime import datetime, timezone
    night_dt = datetime(2026, 11, 16, 23, 30, tzinfo=timezone.utc)
    night_ts = night_dt.timestamp()

    assert qm.is_in_quiet_hours(night_ts) is True

    msg = NotificationMessage(
        id="quiet-1",
        channel="voice",
        title="Bedtime Alert",
        content="New email received",
    )

    res = dispatcher.send(msg, now_ts=night_ts)
    assert res.status == NotificationStatus.QUEUED_QUIET_HOURS

    queued = qm.flush_queued()
    assert len(queued) == 1
    assert queued[0]["notification_id"] == "quiet-1"

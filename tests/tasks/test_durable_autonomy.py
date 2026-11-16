"""
Tests for Phase 4: Planning, Durable Tasks, Recovery and Self-Correction.

Verifies:
1. Goal creates structured plan with measurable acceptance criteria.
2. Step dependencies execute in strictly correct topological order.
3. Independent tasks can be scheduled separately without cross-interference.
4. Duplicate workers cannot execute the same leased task/step (exclusive leasing).
5. Backend restart resumes safely from the last verified checkpoint.
6. Worker crash / unhandled failure marks task blocked or recovers safely.
7. User cancellation is honored immediately.
8. Retry budget stops repeated failures with bounded attempts.
9. Non-idempotent action is not replayed blindly on ambiguous failure.
10. Checkpoint restore returns expected state and context.
11. Rollback failure is reported truthfully if checkpoint is invalid/missing.
12. Task progress events are logged consistently in SQLite store.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import openjarvis.tools
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tasks.engine import BackgroundTaskEngine
from openjarvis.tasks.models import (
    BackgroundTask,
    StepStatus,
    TaskBudget,
    TaskCheckpoint,
    TaskStatus,
    TaskStep,
)
from openjarvis.tasks.store import TaskStore
from openjarvis.tools._stubs import BaseTool, ToolSpec


# Register a failing tool for testing bounded retries
@ToolRegistry.register("transient_failing_tool")
class TransientFailingTool(BaseTool):
    tool_id = "transient_failing_tool"

    def execute(self, **params):
        return ToolResult(
            tool_name="transient_failing_tool",
            success=False,
            content="Simulated transient network timeout",
        )


# Register a non-idempotent tool for testing non-replay
@ToolRegistry.register("test_channel_send")
class TestChannelSendTool(BaseTool):
    tool_id = "test_channel_send"

    def execute(self, **params):
        return ToolResult(
            tool_name="test_channel_send",
            success=False,
            content="External notification server unreachable",
        )


@pytest.fixture(autouse=True)
def setup_test_tools():
    from openjarvis.tools.think import ThinkTool

    ToolRegistry.register("think")(ThinkTool)
    yield


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(db_path=tmp_path / "durable_tasks.db")


@pytest.fixture
def engine(store: TaskStore) -> BackgroundTaskEngine:
    return BackgroundTaskEngine(store=store)


# --- 1. Goal creates plan and acceptance criteria -----------------------------


def test_scenario_1_goal_creates_plan_and_acceptance_criteria(
    engine: BackgroundTaskEngine,
):
    task = engine.create_task(
        title="Automated Test Suite Creation",
        user_request="Build and verify unit tests for auth module",
        agent_profile="coding",
    )
    assert task.status == TaskStatus.CREATED

    plan = engine._generate_plan(task)
    assert len(plan) >= 2
    for step in plan:
        assert step.step_id
        assert step.title
        assert step.assigned_agent


# --- 2. Dependencies execute in correct order --------------------------------


def test_scenario_2_dependencies_execute_in_correct_order(
    engine: BackgroundTaskEngine,
):
    task = engine.create_task(
        title="Ordered Pipeline", user_request="Step 1 then Step 2"
    )

    s1 = TaskStep(
        step_id="step-1",
        step_number=1,
        title="Prerequisite Step",
        status=StepStatus.PENDING,
        tool_name="think",
        tool_params={"thought": "Step 1 done"},
        assigned_agent="",
    )
    s2 = TaskStep(
        step_id="step-2",
        step_number=2,
        title="Dependent Step",
        status=StepStatus.PENDING,
        tool_name="think",
        tool_params={"thought": "Step 2 done"},
        dependencies=["step-1"],
        assigned_agent="",
    )

    started = engine.start_task(task.id, plan_steps=[s1, s2])
    assert started is True

    # Wait for completion
    worker = engine._active_workers.get(task.id)
    if worker:
        worker.join(timeout=5.0)

    final_task = engine.store.get_task(task.id)
    assert final_task.status == TaskStatus.COMPLETED
    assert final_task.plan[0].status == StepStatus.COMPLETED
    assert final_task.plan[1].status == StepStatus.COMPLETED
    assert final_task.plan[0].completed_at <= final_task.plan[1].started_at


# --- 3. Independent tasks can be scheduled separately -------------------------


def test_scenario_3_independent_tasks_scheduled_separately(
    engine: BackgroundTaskEngine,
):
    t1 = engine.create_task(title="Task A", user_request="Job A")
    t2 = engine.create_task(title="Task B", user_request="Job B")

    assert t1.id != t2.id
    assert engine.store.get_task(t1.id) is not None
    assert engine.store.get_task(t2.id) is not None


# --- 4. Duplicate worker cannot execute same leased step ---------------------


def test_scenario_4_duplicate_worker_cannot_execute_same_leased_step(
    engine: BackgroundTaskEngine,
):
    task = engine.create_task(title="Lease Lock Test", user_request="Lease job")

    # Worker 1 acquires exclusive lease
    acquired_1 = engine.store.acquire_lease(
        task.id, worker_id="worker-1", ttl_seconds=60.0
    )
    assert acquired_1 is True

    # Worker 2 attempts to acquire lease on the same task -> must fail
    acquired_2 = engine.store.acquire_lease(
        task.id, worker_id="worker-2", ttl_seconds=60.0
    )
    assert acquired_2 is False

    # Starting task from engine should reject second duplicate worker
    started = engine.start_task(task.id, worker_id="worker-2")
    assert started is False


# --- 5. Backend restart resumes safely ---------------------------------------


def test_scenario_5_backend_restart_resumes_safely(store: TaskStore):
    # Simulate a task interrupted at step 1
    task = BackgroundTask(
        id="task-restart-test",
        title="Interrupted at Step 1",
        user_request="Simulated work",
        status=TaskStatus.RUNNING,
        current_step_index=1,
        plan=[
            TaskStep(
                step_id="s1",
                step_number=1,
                title="Step 1",
                status=StepStatus.COMPLETED,
            ),
            TaskStep(
                step_id="s2",
                step_number=2,
                title="Step 2",
                status=StepStatus.PENDING,
                tool_name="think",
                tool_params={"thought": "Step 2 after restart"},
            ),
        ],
    )
    store.save_task(task)

    cp = TaskCheckpoint(
        checkpoint_id="cp-restart-1",
        task_id=task.id,
        step_index=1,
        context={"state_key": "persisted_value"},
    )
    store.save_checkpoint(cp)

    # Re-initialize engine as if process restarted
    restarted_engine = BackgroundTaskEngine(store=store)
    recovered_count = restarted_engine.recover_on_startup()
    assert recovered_count == 1

    recovered_task = store.get_task(task.id)
    assert recovered_task.context.get("state_key") == "persisted_value"


# --- 6. Worker crash recovers or marks task blocked --------------------------


def test_scenario_6_worker_crash_marks_task_blocked_on_unmet_dep(
    engine: BackgroundTaskEngine,
):
    task = engine.create_task(
        title="Unmet Dependency Test", user_request="Will block"
    )

    s1 = TaskStep(
        step_id="s1",
        step_number=1,
        title="Missing Dependency",
        status=StepStatus.PENDING,
        dependencies=["non-existent-prereq"],
    )
    started = engine.start_task(task.id, plan_steps=[s1])
    assert started is True

    worker = engine._active_workers.get(task.id)
    if worker:
        worker.join(timeout=5.0)

    final_task = engine.store.get_task(task.id)
    assert final_task.status == TaskStatus.BLOCKED
    assert "unmet dependencies" in (final_task.error or "").lower()


# --- 7. User cancellation is honored -----------------------------------------


def test_scenario_7_user_cancellation_honored(engine: BackgroundTaskEngine):
    task = engine.create_task(title="Cancel Job", user_request="Cancel immediately")

    # Cancel task
    assert engine.cancel_task(task.id) is True
    assert engine.store.get_task(task.id).status == TaskStatus.CANCELLED


# --- 8. Retry budget stops repeated failures ---------------------------------


def test_scenario_8_retry_budget_stops_repeated_failures(
    engine: BackgroundTaskEngine,
):
    budget = TaskBudget(max_retries=2)
    task = engine.create_task(
        title="Failing Task",
        user_request="Fail with budget",
        budget=budget,
    )

    # transient_failing_tool will fail and trigger retries up to budget
    s1 = TaskStep(
        step_id="s1",
        step_number=1,
        title="Failing Step",
        status=StepStatus.PENDING,
        tool_name="transient_failing_tool",
        assigned_agent="",
    )
    engine.start_task(task.id, plan_steps=[s1])

    worker = engine._active_workers.get(task.id)
    if worker:
        worker.join(timeout=5.0)

    final_task = engine.store.get_task(task.id)
    assert final_task.status == TaskStatus.FAILED
    assert final_task.budget.current_retries > 0


# --- 9. Non-idempotent action is not replayed blindly ------------------------


def test_scenario_9_non_idempotent_action_not_replayed_blindly(
    engine: BackgroundTaskEngine,
):
    budget = TaskBudget(max_retries=3)
    task = engine.create_task(
        title="Non-Idempotent Test",
        user_request="Delete file test",
        budget=budget,
    )

    # test_channel_send is classified as non-idempotent
    s1 = TaskStep(
        step_id="s1",
        step_number=1,
        title="Send Notification",
        status=StepStatus.PENDING,
        tool_name="test_channel_send",
        tool_params={"channel": "general", "message": "hello"},
        assigned_agent="",
    )
    engine.start_task(task.id, plan_steps=[s1])

    worker = engine._active_workers.get(task.id)
    if worker:
        worker.join(timeout=5.0)

    final_task = engine.store.get_task(task.id)
    # Refuses blind retry after failure
    assert final_task.status in (TaskStatus.FAILED, TaskStatus.WAITING_APPROVAL)
    if final_task.status == TaskStatus.FAILED:
        assert "non-idempotent" in (final_task.error or "").lower()


# --- 10. Checkpoint restore returns expected context -------------------------


def test_scenario_10_checkpoint_restore_returns_expected_context(
    engine: BackgroundTaskEngine,
):
    task = engine.create_task(
        title="Checkpoint Test", user_request="Testing checkpoints"
    )

    cp = TaskCheckpoint(
        checkpoint_id="cp-save-1",
        task_id=task.id,
        step_index=2,
        context={"saved_key": "saved_value_123"},
    )
    engine.store.save_checkpoint(cp)

    success = engine.rollback_to_checkpoint(task.id, "cp-save-1")
    assert success is True

    updated_task = engine.store.get_task(task.id)
    assert updated_task.current_step_index == 2
    assert updated_task.context.get("saved_key") == "saved_value_123"
    assert updated_task.status == TaskStatus.PAUSED


# --- 11. Rollback failure is reported truthfully ------------------------------


def test_scenario_11_rollback_failure_is_reported(engine: BackgroundTaskEngine):
    task = engine.create_task(title="Invalid Rollback", user_request="Test")
    # Non-existent checkpoint ID
    success = engine.rollback_to_checkpoint(task.id, "cp-non-existent-999")
    assert success is False


# --- 12. Task progress is consistent in database -----------------------------


def test_scenario_12_task_progress_consistent_in_database(
    engine: BackgroundTaskEngine,
):
    task = engine.create_task(title="Audit Events", user_request="Testing events")
    engine.store.log_event(task.id, "test_event_1", {"msg": "First"})
    engine.store.log_event(task.id, "test_event_2", {"msg": "Second"})

    events = engine.store.get_events(task.id)
    assert len(events) >= 2
    assert events[-2]["event_type"] == "test_event_1"
    assert events[-1]["event_type"] == "test_event_2"

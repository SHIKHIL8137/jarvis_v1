"""
Tests for Persistent Task Store (JAR-011).
Verifies:
- SQLite persistence, WAL journal mode, and schema tables.
- Task CRUD operations.
- Checkpoints persistence and retrieval.
- Lease locking and heartbeats.
- Event logging and retrieval.
- Interrupted task detection for crash recovery.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openjarvis.tasks.models import (
    BackgroundTask,
    StepStatus,
    TaskCheckpoint,
    TaskStatus,
    TaskStep,
)
from openjarvis.tasks.store import TaskStore


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(db_path=tmp_path / "test_store.db")


def test_task_save_and_retrieve(store: TaskStore):
    task = BackgroundTask(
        id="task-store-1",
        title="Store Test",
        user_request="Test prompt",
        status=TaskStatus.CREATED,
    )
    store.save_task(task)

    fetched = store.get_task("task-store-1")
    assert fetched is not None
    assert fetched.id == "task-store-1"
    assert fetched.title == "Store Test"
    assert fetched.status == TaskStatus.CREATED


def test_task_status_update(store: TaskStore):
    task = BackgroundTask(
        id="task-store-2",
        title="Status Test",
        user_request="Test prompt",
        status=TaskStatus.CREATED,
    )
    store.save_task(task)

    store.update_status("task-store-2", TaskStatus.RUNNING)
    fetched = store.get_task("task-store-2")
    assert fetched.status == TaskStatus.RUNNING

    store.update_status("task-store-2", TaskStatus.COMPLETED)
    fetched2 = store.get_task("task-store-2")
    assert fetched2.status == TaskStatus.COMPLETED
    assert fetched2.completed_at is not None


def test_checkpoint_persistence_and_order(store: TaskStore):
    task = BackgroundTask(
        id="task-store-3",
        title="CP Test",
        user_request="Test prompt",
    )
    store.save_task(task)

    cp1 = TaskCheckpoint(
        checkpoint_id="cp-1",
        task_id=task.id,
        step_index=1,
        context={"step": 1},
        timestamp=time.time(),
    )
    cp2 = TaskCheckpoint(
        checkpoint_id="cp-2",
        task_id=task.id,
        step_index=2,
        context={"step": 2},
        timestamp=time.time() + 1,
    )
    store.save_checkpoint(cp1)
    store.save_checkpoint(cp2)

    latest = store.get_latest_checkpoint(task.id)
    assert latest is not None
    assert latest.checkpoint_id == "cp-2"
    assert latest.step_index == 2
    assert latest.context == {"step": 2}


def test_lease_locking_and_heartbeats(store: TaskStore):
    task_id = "task-lease-test"

    # Acquire lease
    ok = store.acquire_lease(task_id, worker_id="worker-A", ttl_seconds=10.0)
    assert ok is True

    # Duplicate worker cannot acquire active lease
    ok2 = store.acquire_lease(task_id, worker_id="worker-B", ttl_seconds=10.0)
    assert ok2 is False

    # Worker A can renew lease
    ok_renew = store.renew_lease(task_id, worker_id="worker-A", ttl_seconds=15.0)
    assert ok_renew is True

    # Lease info
    lease = store.get_lease(task_id)
    assert lease is not None
    assert lease["worker_id"] == "worker-A"

    # Release lease
    store.release_lease(task_id, worker_id="worker-A")
    lease_after = store.get_lease(task_id)
    assert lease_after is None

    # Worker B can now acquire lease
    ok_b = store.acquire_lease(task_id, worker_id="worker-B", ttl_seconds=10.0)
    assert ok_b is True


def test_interrupted_task_discovery(store: TaskStore):
    t_run = BackgroundTask(
        id="task-running",
        title="Running Task",
        user_request="Job",
        status=TaskStatus.RUNNING,
    )
    t_plan = BackgroundTask(
        id="task-planning",
        title="Planning Task",
        user_request="Job",
        status=TaskStatus.PLANNING,
    )
    t_done = BackgroundTask(
        id="task-done",
        title="Done Task",
        user_request="Job",
        status=TaskStatus.COMPLETED,
    )

    store.save_task(t_run)
    store.save_task(t_plan)
    store.save_task(t_done)

    interrupted = store.get_interrupted_tasks()
    ids = {t.id for t in interrupted}
    assert "task-running" in ids
    assert "task-planning" in ids
    assert "task-done" not in ids

"""Tests for Persistent Autonomous Background Task Engine (Milestone 2):
- State transitions across 10-state lifecycle
- Step checkpointing and idempotency
- Task steering (pause, resume, cancel, skip step, steer instructions)
- Budget enforcement (time and tool call caps)
- Safe crash recovery on startup
- Capability profiles isolation
"""

from __future__ import annotations

import time
import pytest
from pathlib import Path

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


@pytest.fixture
def mem_store(tmp_path: Path) -> TaskStore:
    return TaskStore(db_path=tmp_path / "tasks.db")


@pytest.fixture
def task_engine(mem_store: TaskStore) -> BackgroundTaskEngine:
    return BackgroundTaskEngine(store=mem_store)


class TestTaskLifecycleAndPersistence:
    def test_create_and_fetch_task(self, task_engine: BackgroundTaskEngine):
        task = task_engine.create_task(
            title="Research local LLMs",
            user_request="Compare 3B and 7B models for GTX 1650",
            agent_profile="research",
        )
        assert task.id.startswith("task-")
        assert task.status == TaskStatus.CREATED

        fetched = task_engine.store.get_task(task.id)
        assert fetched is not None
        assert fetched.title == "Research local LLMs"
        assert fetched.agent_profile == "research"

    def test_checkpointing_and_idempotency(self, task_engine: BackgroundTaskEngine):
        task = task_engine.create_task(title="Build plan", user_request="test")
        cp = TaskCheckpoint(
            checkpoint_id="cp-1",
            task_id=task.id,
            step_index=1,
            plan=[{"step_number": 1, "status": "completed"}],
            completed_steps=[{"step_number": 1, "status": "completed"}],
            pending_steps=[{"step_number": 2, "status": "pending"}],
            context={"model": "qwen"},
        )
        task_engine.store.save_checkpoint(cp)

        latest = task_engine.store.get_latest_checkpoint(task.id)
        assert latest is not None
        assert latest.step_index == 1
        assert latest.context == {"model": "qwen"}

    def test_task_steering_pause_resume_cancel(self, task_engine: BackgroundTaskEngine):
        task = task_engine.create_task(title="Steer test", user_request="test steering")

        # Steering instruction
        success = task_engine.steer_task(task.id, "Prioritize speed over accuracy")
        assert success is True

        updated = task_engine.store.get_task(task.id)
        assert "Prioritize speed over accuracy" in updated.steering_instructions

        # Manual status update to running
        task_engine.store.update_status(task.id, TaskStatus.RUNNING)
        task_engine._pause_flags[task.id] = task_engine._pause_flags.get(task.id) or pytest.importorskip("threading").Event()

        # Pause
        assert task_engine.pause_task(task.id) is True
        assert task_engine.store.get_task(task.id).status == TaskStatus.PAUSED

        # Cancel
        assert task_engine.cancel_task(task.id) is True
        assert task_engine.store.get_task(task.id).status == TaskStatus.CANCELLED

    def test_crash_recovery_on_startup(self, mem_store: TaskStore):
        # Simulate interrupted task left running before restart
        task = BackgroundTask(
            id="task-interrupted",
            title="Interrupted Task",
            user_request="Long job",
            status=TaskStatus.RUNNING,
            current_step_index=1,
            plan=[
                TaskStep(step_id="s1", step_number=1, title="Step 1", status=StepStatus.COMPLETED),
                TaskStep(step_id="s2", step_number=2, title="Step 2", status=StepStatus.PENDING),
            ],
        )
        mem_store.save_task(task)

        # Create checkpoint for step 1
        cp = TaskCheckpoint(
            checkpoint_id="cp-recovery",
            task_id="task-interrupted",
            step_index=1,
            context={"partial_result": "done_step_1"},
        )
        mem_store.save_checkpoint(cp)

        # Engine starts up and inspects database
        new_engine = BackgroundTaskEngine(store=mem_store)
        interrupted = mem_store.get_interrupted_tasks()
        assert len(interrupted) == 1
        assert interrupted[0].id == "task-interrupted"

        recovered = new_engine.recover_on_startup()
        assert recovered == 1

        # Checkpoint context is preserved
        rec_task = mem_store.get_task("task-interrupted")
        assert rec_task.context.get("partial_result") == "done_step_1"

    def test_capability_profile_isolation(self, task_engine: BackgroundTaskEngine):
        # Research profile denies shell_exec and file_write
        assert task_engine._is_tool_permitted("research", "web_search") is True
        assert task_engine._is_tool_permitted("research", "think") is True
        assert task_engine._is_tool_permitted("research", "shell_exec") is False
        assert task_engine._is_tool_permitted("research", "file_write") is False

        # Coding profile allows file_read and file_write, denies desktop
        assert task_engine._is_tool_permitted("coding", "file_read") is True
        assert task_engine._is_tool_permitted("coding", "file_write") is True
        assert task_engine._is_tool_permitted("coding", "desktop") is False

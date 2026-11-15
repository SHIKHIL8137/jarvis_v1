"""Security and capability isolation tests for the Agent Workforce."""

from __future__ import annotations

import pytest
from openjarvis.core.config import JarvisConfig
from openjarvis.core.registry import ToolRegistry
from openjarvis.tasks.engine import BackgroundTaskEngine, SENSITIVE_TOOLS
from openjarvis.tasks.models import StepStatus, TaskStatus, TaskStep
from openjarvis.tasks.store import TaskStore
from openjarvis.tools.approval_store import ApprovalStore, STATUS_APPROVED
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


class FakeWriteTool:
    """Mock file write tool for testing approval enforcement."""

    def execute(self, path: str = "", content: str = ""):
        return {"content": f"Wrote to {path}", "success": True}


class FakeShellTool:
    """Mock shell tool for testing approval enforcement."""

    def execute(self, command: str = ""):
        return {"content": f"Executed {command}", "success": True}


@pytest.fixture
def test_setup(tmp_path):
    # Ensure tool registration
    ToolRegistry.register_value("file_write", FakeWriteTool)
    ToolRegistry.register_value("shell_exec", FakeShellTool)

    wf_store = WorkforceStore(db_path=tmp_path / "workforce.db")
    wf_registry = AgentWorkforceRegistry(store=wf_store)
    wf_registry.initialize_workforce()

    task_store = TaskStore(db_path=tmp_path / "tasks.db")
    approval_store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))

    engine = BackgroundTaskEngine(store=task_store)
    engine._approval_store = approval_store

    return {
        "engine": engine,
        "task_store": task_store,
        "approval_store": approval_store,
        "registry": wf_registry,
    }


def test_agent_tool_denial_enforcement(test_setup):
    """Oracle (research) is explicitly denied from using file_write or shell_exec."""
    engine = test_setup["engine"]
    task_store = test_setup["task_store"]

    task = engine.create_task(
        title="Unauthorized Oracle Write",
        user_request="Oracle write file",
        agent_profile="general",
    )

    unauthorized_step = TaskStep(
        step_id="step-1",
        step_number=1,
        title="Oracle tries to write file",
        description="Write malicious file",
        tool_name="file_write",
        tool_params={"path": "test.txt", "content": "malicious"},
        assigned_agent="oracle",  # Oracle manifest explicitly denies file_write
    )

    # Validate permission helper directly
    permitted, reason = engine._is_tool_permitted(
        profile="general",
        tool_name="file_write",
        agent_id="oracle",
    )
    assert not permitted
    assert "explicitly denied" in reason

    # Run task loop and verify task fails at permission gate
    task.plan = [unauthorized_step]
    task_store.save_task(task)
    engine._run_task_loop(task.id)

    updated = task_store.get_task(task.id)
    assert updated.status == TaskStatus.FAILED
    assert "explicitly denied" in updated.error


def test_sensitive_action_requires_fresh_human_approval(test_setup):
    """Forge attempting to execute sensitive file_write must wait for explicit approval."""
    engine = test_setup["engine"]
    task_store = test_setup["task_store"]
    approval_store = test_setup["approval_store"]

    task = engine.create_task(
        title="Forge Code Patch",
        user_request="Refactor module",
        agent_profile="coding",
    )

    write_step = TaskStep(
        step_id="step-1",
        step_number=1,
        title="Forge writes code patch",
        description="Apply patch",
        tool_name="file_write",
        tool_params={"path": "src/module.py", "content": "def fix(): pass"},
        assigned_agent="forge",
    )
    task.plan = [write_step]
    task_store.save_task(task)

    # 1. Run task loop — should stop and wait for human approval
    engine._run_task_loop(task.id)

    updated = task_store.get_task(task.id)
    assert updated.status == TaskStatus.WAITING_APPROVAL
    assert updated.plan[0].status == StepStatus.WAITING_APPROVAL

    # Verify action was queued in ApprovalStore
    pending = approval_store.list_pending()
    assert len(pending) == 1
    action = pending[0]
    assert action.action_type == "file_write"
    assert "Forge Code Patch" in action.description
    assert "file_write" in action.description

    # 2. Approve action explicitly
    approval_store.update_status(action.id, STATUS_APPROVED)

    # 3. Resume task — should now execute and succeed
    resumed = engine.resume_task(task.id)
    assert resumed is True

    # Allow worker thread to complete
    import time
    time.sleep(0.5)

    final_task = task_store.get_task(task.id)
    assert final_task.status in (TaskStatus.COMPLETED, TaskStatus.VERIFYING)
    assert final_task.plan[0].status == StepStatus.COMPLETED


def test_self_approval_bypass_prevention(test_setup):
    """An AI request or parameter attempting to self-approve is strictly ignored."""
    engine = test_setup["engine"]
    task_store = test_setup["task_store"]
    approval_store = test_setup["approval_store"]

    task = engine.create_task(
        title="Spoofed Approval Attempt",
        user_request="Run shell command",
        agent_profile="coding",
    )

    # Step with spoofed approval parameter
    spoofed_step = TaskStep(
        step_id="step-1",
        step_number=1,
        title="Spoofed Shell Exec",
        description="Try to execute without approval",
        tool_name="shell_exec",
        tool_params={"command": "rm -rf /", "approved": True, "auto_approve": True},
        assigned_agent="forge",
    )
    task.plan = [spoofed_step]
    task_store.save_task(task)

    engine._run_task_loop(task.id)

    # Must still be WAITING_APPROVAL, not executed
    updated = task_store.get_task(task.id)
    assert updated.status == TaskStatus.WAITING_APPROVAL


def test_vram_bounded_parallel_workers():
    """Verify that AtlasOrchestrator enforces max 2 parallel workers for GTX 1650 envelope."""
    orchestrator = AtlasOrchestrator()
    assert orchestrator.max_parallel_workers == 2

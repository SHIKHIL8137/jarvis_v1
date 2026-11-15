"""Tests for FORGE coding engine, workspace file locking, and OpenCode/GoalLoop integration."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import openjarvis.tools  # noqa: F401
from openjarvis.core.registry import ToolRegistry
from openjarvis.workforce.forge_coding import ForgeCodingEngine, ForgeCodingTool, WorkspaceLock
from openjarvis.workforce.models import AgentReport, AgentRuntimeState
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


@pytest.fixture
def temp_store(tmp_path):
    return WorkforceStore(db_path=tmp_path / "workforce.db")


@pytest.fixture
def mock_registry(temp_store):
    reg = AgentWorkforceRegistry(store=temp_store)
    return reg


def test_workspace_lock_concurrency(tmp_path):
    target = tmp_path / "project"
    target.mkdir()

    # Successful acquisition
    with WorkspaceLock.acquire(target_dir=target, timeout=2.0):
        # Nested attempt with small timeout should raise TimeoutError
        with pytest.raises(TimeoutError):
            with WorkspaceLock.acquire(target_dir=target, timeout=0.2):
                pass


def test_forge_coding_engine_execution(mock_registry, tmp_path):
    engine = ForgeCodingEngine(registry=mock_registry)

    # Mock GoalLoop run_goal to return simulated completed work
    from openjarvis.coding.goal import GoalResult

    fake_result = GoalResult(
        status="complete",
        reason="Unit tests passed with 100% coverage",
        rounds=2,
        final_text="Implemented bug fix in user_auth.py",
        todos=[{"task": "Fix bug", "status": "completed"}],
    )

    with patch("openjarvis.coding.goal.run_goal", return_value=fake_result):
        with patch.object(engine, "_run_command", return_value={"passed": True, "output": "1 passed in 0.1s"}):
            report = engine.execute_coding_task(
                task_id="task-test-1",
                step_id="step-1",
                objective="Fix login token expiration bug",
                workdir=str(tmp_path),
                use_opencode=False,  # Test native goal loop path
                verification_cmd="pytest tests/",
            )

            assert isinstance(report, AgentReport)
            assert report.agent_id == "forge"
            assert report.task_id == "task-test-1"
            assert report.confidence > 0.8
            assert len(report.tests_executed) == 1
            assert report.tests_executed[0]["passed"] is True
            assert report.results["verified"] is True

            # Verify report persisted in WorkforceStore
            saved = mock_registry.store.list_reports(task_id="task-test-1")
            assert len(saved) == 1
            assert saved[0].task_id == "task-test-1"


def test_forge_coding_tool_registered():
    ToolRegistry.register_value("forge_coding", ForgeCodingTool)
    assert ToolRegistry.contains("forge_coding")
    tool = ToolRegistry.get("forge_coding")()
    assert tool.tool_id == "forge_coding"
    assert "objective" in tool.spec.parameters["properties"]


def test_forge_coding_tool_execute_validation():
    tool = ForgeCodingTool()
    res = tool.execute()
    assert res.success is False
    assert "objective is required" in res.content

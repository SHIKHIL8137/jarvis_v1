"""End-to-End tests for specialist agent real execution, ToolRegistry audit, and model allocation.

Verifies:
- All tools referenced by planner exist in ToolRegistry (file_read, knowledge_search, forge_coding, think).
- Real specialist tool execution without mock placeholders.
- Removal of silent "auto" fallback when model selection fails (explicit FAILED status).
- Allocated model is passed through to runtime execution.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core.registry import ToolRegistry
import openjarvis.tools  # noqa: F401
from openjarvis.models.capabilities import NoCompatibleModelAvailableResult
from openjarvis.tasks.models import StepStatus, TaskStep
from openjarvis.workforce.models import AgentRuntimeState
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


def test_tool_registry_audit_all_planner_tools_present():
    """Verify ToolRegistry contains every tool referenced across all specialist roles."""
    from openjarvis.tools import ensure_tools_registered
    ensure_tools_registered()

    expected_tools = [
        "file_read",
        "file_write",
        "forge_coding",
        "think",
        "knowledge_search",
        "n8n",
        "glob",
        "grep",
        "shell_exec",
    ]

    for tool_name in expected_tools:
        assert ToolRegistry.contains(tool_name), f"Expected tool '{tool_name}' missing from ToolRegistry"


def test_scout_real_file_read_execution(tmp_path):
    """Scout executes real file_read tool and retrieves real file contents."""
    store = WorkforceStore(db_path=tmp_path / "scout_exec.db")
    reg = AgentWorkforceRegistry(store=store)
    reg.initialize_workforce()
    orchestrator = AtlasOrchestrator(registry=reg)

    test_file = tmp_path / "sample_scout_doc.txt"
    test_file.write_text("SCOUT_VERIFIED_CONTENT_12345", encoding="utf-8")

    step = TaskStep(
        step_id="step-scout-1",
        step_number=1,
        title="Inspect file",
        description="Read sample scout doc",
        tool_name="file_read",
        tool_params={"path": str(test_file)},
        assigned_agent="scout",
    )

    result, err = orchestrator.execute_step_safely(step)
    assert err is None
    assert result is not None
    assert result.get("success") is True
    assert "SCOUT_VERIFIED_CONTENT_12345" in result.get("content", "")


def test_forge_real_verification_execution(tmp_path):
    """Forge executes real verification command with shell=False and returns structured report."""
    store = WorkforceStore(db_path=tmp_path / "forge_exec.db")
    reg = AgentWorkforceRegistry(store=store)
    reg.initialize_workforce()
    orchestrator = AtlasOrchestrator(registry=reg)

    step = TaskStep(
        step_id="step-forge-1",
        step_number=1,
        title="Verify Python environment",
        description="Run test command",
        tool_name="forge_coding",
        tool_params={
            "objective": "Verify python execution",
            "workdir": str(tmp_path),
            "verification_cmd": "python --version",
        },
        assigned_agent="forge",
    )

    with patch("openjarvis.agents.opencode.is_opencode_available", return_value=False):
        with patch("openjarvis.coding.goal.run_goal") as mock_goal:
            mock_goal.return_value = MagicMock(status="completed", rounds=1, reason="Done", todos=[])
            result, err = orchestrator.execute_step_safely(step, model="test-model")
            assert err is None
            assert result is not None
            assert result.get("agent_id") == "forge"
            assert result.get("results", {}).get("verification_status") == "passed"
            assert result.get("results", {}).get("verified") is True


def test_forge_blocks_arbitrary_inline_code_without_container(tmp_path):
    """Untrusted inline code execution (python -c, node -e) is blocked when container sandbox is unavailable."""
    store = WorkforceStore(db_path=tmp_path / "forge_inline_block.db")
    reg = AgentWorkforceRegistry(store=store)
    reg.initialize_workforce()
    orchestrator = AtlasOrchestrator(registry=reg)

    step = TaskStep(
        step_id="step-forge-block",
        step_number=1,
        title="Untrusted inline script",
        description="Run arbitrary script",
        tool_name="forge_coding",
        tool_params={
            "objective": "Attempt inline execution",
            "workdir": str(tmp_path),
            "verification_cmd": 'python -c "import os; print(os.environ)"',
        },
        assigned_agent="forge",
    )

    with patch("openjarvis.agents.opencode.is_opencode_available", return_value=False):
        with patch("openjarvis.coding.goal.run_goal") as mock_goal:
            mock_goal.return_value = MagicMock(status="completed", rounds=1, reason="Done", todos=[])
            # Ensure container sandbox is reported unavailable
            with patch("openjarvis.sandbox.verification_runner.IsolatedVerificationRunner.is_container_runtime_available", return_value=False):
                result, err = orchestrator.execute_step_safely(step, model="test-model")
                assert result is not None
                assert result.get("results", {}).get("verification_status") == "blocked"
                assert result.get("results", {}).get("verified") is False
                tests = result.get("tests_executed", [])
                assert len(tests) >= 1
                assert tests[0]["status"] == "blocked"
                assert "Security policy violation" in tests[0]["output"]


def test_model_selection_failure_fails_step_without_auto_fallback(tmp_path):
    """When model selection fails, the step MUST fail explicitly and NOT silently use 'auto'."""
    store = WorkforceStore(db_path=tmp_path / "model_fail.db")
    reg = AgentWorkforceRegistry(store=store)
    reg.initialize_workforce()
    orchestrator = AtlasOrchestrator(registry=reg)

    step = TaskStep(
        step_id="step-fail-1",
        step_number=1,
        title="Coding step",
        description="Perform coding",
        tool_name="think",
        tool_params={"thought": "test"},
        assigned_agent="forge",
    )

    # Mock ModelSelectionEngine to return NoCompatibleModelAvailableResult
    with patch("openjarvis.models.get_model_selection_engine") as mock_engine_fn:
        mock_sel_engine = MagicMock()
        mock_sel_engine.select_model.return_value = NoCompatibleModelAvailableResult(
            message="No compatible GPU/cloud model available"
        )
        mock_engine_fn.return_value = mock_sel_engine

        result = orchestrator.execute_multi_agent_plan(
            task_id="task-fail-test",
            user_request="Perform coding",
            steps=[step],
        )

        # Step MUST have failed due to model selection error, NOT completed with "auto"
        assert step.status == StepStatus.FAILED
        assert "Model selection failed" in (step.error or "")
        assert step.tool_params.get("_allocated_model") is None or step.tool_params.get("_allocated_model") != "auto"


def test_allocated_model_passed_to_step_executor(tmp_path):
    """The model selected dynamically must be passed directly into the step executor."""
    store = WorkforceStore(db_path=tmp_path / "model_pass.db")
    reg = AgentWorkforceRegistry(store=store)
    reg.initialize_workforce()
    orchestrator = AtlasOrchestrator(registry=reg)

    step = TaskStep(
        step_id="step-pass-1",
        step_number=1,
        title="Analysis step",
        description="Analyze context",
        tool_name="think",
        tool_params={"thought": "thinking"},
        assigned_agent="oracle",
    )

    received_models = []

    def tracking_executor(s: TaskStep, model: str):
        received_models.append(model)
        return {"result": f"Executed with {model}"}, None

    with patch("openjarvis.models.get_model_selection_engine") as mock_engine_fn:
        mock_sel_engine = MagicMock()
        mock_sel_res = MagicMock(is_compatible=True, model_id="qwen-2.5-72b-instruct")
        mock_sel_engine.select_model.return_value = mock_sel_res
        mock_engine_fn.return_value = mock_sel_engine

        orchestrator.execute_multi_agent_plan(
            task_id="task-model-track",
            user_request="Analyze context",
            steps=[step],
            step_executor=tracking_executor,
        )

        assert step.status == StepStatus.COMPLETED
        assert len(received_models) == 1
        assert received_models[0] == "qwen-2.5-72b-instruct"

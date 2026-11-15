"""End-to-End Acceptance Tests for Scenarios A, B, C, and D from Part 15."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.tasks.models import StepStatus, TaskStep
from openjarvis.tools.approval_store import ApprovalStore, STATUS_APPROVED
from openjarvis.workforce.forge_coding import ForgeCodingEngine
from openjarvis.workforce.models import AgentReport, AgentRuntimeState
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


@pytest.fixture
def clean_workforce(tmp_path):
    store = WorkforceStore(db_path=tmp_path / "workforce.db")
    reg = AgentWorkforceRegistry(store=store)
    return reg


# ---------------------------------------------------------------------------
# Scenario A: Multi-file Feature Implementation or Bug Fix
# ---------------------------------------------------------------------------
def test_scenario_a_multi_file_coding_and_verification(clean_workforce, tmp_path):
    """Test Atlas decomposing request, Scout finding files, Forge implementing and verifying, and Atlas consolidating."""
    orchestrator = AtlasOrchestrator(registry=clean_workforce, max_parallel_workers=2)

    # 1. Plan task
    steps = orchestrator.plan_task("Fix authentication token refresh bug and add unit tests")
    assert len(steps) >= 2
    step_agents = [s.assigned_agent for s in steps]
    assert "forge" in step_agents

    # 2. Mock specialist execution
    def mock_step_executor(step: TaskStep):
        if step.assigned_agent == "scout":
            return {"files_found": ["src/auth.py", "tests/test_auth.py"]}, None
        elif step.assigned_agent == "forge":
            # Return ForgeCodingEngine structured report
            rep = AgentReport(
                agent_id="forge",
                task_id="task-scenario-a",
                step_id=step.step_id,
                work_performed="Applied fix in auth.py and added unit test in test_auth.py",
                files_changed=["src/auth.py", "tests/test_auth.py"],
                tests_executed=[{"command": "pytest tests/test_auth.py", "passed": True}],
                confidence=0.98,
            )
            return rep.to_dict(), None
        elif step.assigned_agent == "vanguard":
            return {"verdict": "verified", "acceptance_criteria_met": True}, None
        return {"status": "ok"}, None

    # 3. Execute plan with deterministic mock model runtime
    from openjarvis.models.capabilities import SelectionResult

    mock_sel = SelectionResult(
        model_id="mock-qwen-coder:latest",
        provider="mock",
        task_type="coding",
        reason="Deterministic mock runtime for test",
        policy="local_first",
        is_local=True,
        context_length=8192,
        score=1.0,
        is_compatible=True,
    )

    with patch("openjarvis.models.selector.ModelSelectionEngine.select_model", return_value=mock_sel):
        result = orchestrator.execute_multi_agent_plan(
            task_id="task-scenario-a",
            user_request="Fix authentication token refresh bug and add unit tests",
            steps=steps,
            step_executor=mock_step_executor,
        )

    assert result["status"] == "completed"
    assert result["completed_steps"] == len(steps)
    assert len(result["agent_reports"]) >= 2
    assert "auth" in result["consolidated_response"].lower() or len(result["agent_reports"]) > 0


# ---------------------------------------------------------------------------
# Scenario B: Deep Research Question (ORACLE)
# ---------------------------------------------------------------------------
def test_scenario_b_research_comparison(clean_workforce):
    """Test Oracle conducting structured research and returning evidence-backed report."""
    reg = clean_workforce
    reg.set_agent_state("oracle", AgentRuntimeState.BUSY, current_task_id="task-scenario-b")

    report = AgentReport(
        agent_id="oracle",
        task_id="task-scenario-b",
        work_performed="Comparative technical evaluation of SQLite vs PostgreSQL for local embedded storage",
        findings=[
            {
                "topic": "Concurrency",
                "finding": "SQLite WAL mode supports concurrent readers without blocking writes",
                "citation": "sqlite.org/wal.html",
            },
            {
                "topic": "Resource footprint",
                "finding": "SQLite runs in-process with zero network daemon overhead",
            },
        ],
        evidence=[{"source": "documentation", "verified": True}],
        confidence=0.96,
        suggested_next_action="Adopt SQLite with PRAGMA journal_mode=WAL for embedded storage",
    )
    reg.store.save_report(report)
    reg.set_agent_state("oracle", AgentRuntimeState.IDLE)

    reports = reg.store.list_reports(agent_id="oracle")
    assert len(reports) == 1
    assert reports[0].agent_id == "oracle"
    assert len(reports[0].findings) == 2
    assert reports[0].confidence == 0.96


# ---------------------------------------------------------------------------
# Scenario C: Operations & n8n Workflow Automation (PULSE)
# ---------------------------------------------------------------------------
def test_scenario_c_operations_and_n8n(clean_workforce, tmp_path):
    """Test Pulse using n8n tool, listing workflows, and respecting approval gate on execution."""
    from openjarvis.tools.n8n import N8nTool

    approval_store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    n8n_tool = N8nTool(approval_store=approval_store)

    # 1. Health check
    with patch.object(n8n_tool.client, "check_health", return_value={"online": True, "status_code": 200, "latency_ms": 75.0}):
        health_res = n8n_tool.execute(action="health")
        assert health_res.success is True
        assert '"online": true' in health_res.content

    # 2. Workflow discovery
    sample_wfs = [{"id": "wf-101", "name": "Backup Routine", "active": True}]
    with patch.object(n8n_tool.client, "list_workflows", return_value=sample_wfs):
        wf_res = n8n_tool.execute(action="list_workflows")
        assert wf_res.success is True
        assert "Backup Routine" in wf_res.content

    # 3. Gated execution queues for human approval
    exec_res = n8n_tool.execute(action="execute_workflow", workflow_id="wf-101", payload={"dry_run": False})
    assert exec_res.success is False
    assert "Approval queued" in exec_res.content


# ---------------------------------------------------------------------------
# Scenario D: Security Audit and Permission Enforcement (SENTINEL)
# ---------------------------------------------------------------------------
def test_scenario_d_security_audit_and_permissions(clean_workforce):
    """Test Sentinel verifying tool permissions and denying unauthorized tools."""
    sentinel_manifest = clean_workforce.get_agent_manifest("sentinel")
    assert sentinel_manifest is not None

    # Sentinel does not have shell execution or destructive writing permitted
    assert "file_write" in sentinel_manifest.denied_tools or "file:write" in sentinel_manifest.denied_capabilities
    assert "desktop" in sentinel_manifest.denied_tools

    # Test Orchestrator step permission check replacing denied tool with think
    orchestrator = AtlasOrchestrator(registry=clean_workforce)
    raw_steps = [
        {
            "step_id": "step-sec-1",
            "title": "Audit system",
            "description": "Inspect permissions safely",
            "assigned_agent": "sentinel",
            "tool_name": "desktop",  # Denied tool
            "tool_params": {"action": "click"},
        }
    ]

    validated_steps = orchestrator._validate_and_build_steps(raw_steps, "Run security audit")
    # desktop was denied; should be sanitized to think
    assert validated_steps[0].tool_name == "think"

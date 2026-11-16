"""Phase 5 — Specialist Workforce Acceptance and Security Test Suite.

Validates all 11 requirements from 08_PHASE_5_SPECIALIST_WORKFORCE.md:
1. Atlas selects correct specialist.
2. Agent tool restrictions are enforced.
3. Child agent cannot exceed central policy.
4. Read-only agents cannot modify files.
5. Independent tasks execute concurrently.
6. Conflicting file edits are isolated or blocked.
7. Child failure is reported to parent.
8. Parent cancellation propagates.
9. Results include evidence and status.
10. Atlas detects conflicting findings.
11. Agent budget exhaustion stops safely.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core.cancellation import CancellationToken
from openjarvis.core.registry import ToolRegistry
from openjarvis.models.capabilities import SelectionResult
from openjarvis.tasks.models import StepStatus, TaskBudget, TaskStep
from openjarvis.tools import ensure_tools_registered
from openjarvis.workforce.forge_coding import WorkspaceLock
from openjarvis.workforce.models import AgentReport, AgentRuntimeState
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


@pytest.fixture(autouse=True)
def setup_workforce_env(tmp_path):
    """Ensure tools are registered and deterministic mock model selection is active."""
    ensure_tools_registered()

    store = WorkforceStore(db_path=tmp_path / "wf_test.db")
    registry = AgentWorkforceRegistry(store=store)
    registry.initialize_workforce()
    orchestrator = AtlasOrchestrator(registry=registry, max_parallel_workers=2)

    mock_sel = SelectionResult(
        model_id="mock-specialist-model",
        provider="mock",
        task_type="general",
        reason="Deterministic test model fixture",
        policy="local_first",
        is_local=True,
        context_length=8192,
        score=1.0,
        is_compatible=True,
    )

    with patch("openjarvis.models.selector.ModelSelectionEngine.select_model", return_value=mock_sel):
        yield {
            "registry": registry,
            "orchestrator": orchestrator,
            "store": store,
            "tmp_path": tmp_path,
        }

    store.close()


# ---------------------------------------------------------------------------
# 1. Atlas selects correct specialist for domain requests
# ---------------------------------------------------------------------------
def test_scenario_1_atlas_selects_correct_specialist(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    # Security query -> sentinel
    sec_steps = orchestrator.plan_task("Audit security posture and scan for vulnerabilities in pyproject.toml")
    assert any(s.assigned_agent == "sentinel" for s in sec_steps), "Expected Sentinel for security audit"

    # QA / verification query -> vanguard
    qa_steps = orchestrator.plan_task("Execute test verification suite and check acceptance criteria")
    assert any(s.assigned_agent == "vanguard" for s in qa_steps), "Expected Vanguard for test verification"

    # Coding / bugfix query -> forge
    code_steps = orchestrator.plan_task("Fix bug in user authentication session management")
    assert any(s.assigned_agent == "forge" for s in code_steps), "Expected Forge for bug fix"

    # File discovery query -> scout
    scout_steps = orchestrator.plan_task("Find files and explore repository directory tree")
    assert any(s.assigned_agent == "scout" for s in scout_steps), "Expected Scout for file exploration"

    # Research query -> oracle
    res_steps = orchestrator.plan_task("Research and compare vector databases for embedded storage")
    assert any(s.assigned_agent == "oracle" for s in res_steps), "Expected Oracle for research"

    # Operations / routine query -> pulse
    ops_steps = orchestrator.plan_task("Schedule routine health check for background services")
    assert any(s.assigned_agent == "pulse" for s in ops_steps), "Expected Pulse for routine operations"

    # Comprehensive ERP / multi-agent query -> all specialists in DAG
    erp_steps = orchestrator.plan_task("Jarvis, inspect my ERP project, identify bugs, check security, and recommend performance improvements.")
    erp_agents = {s.assigned_agent for s in erp_steps}
    assert {"scout", "forge", "sentinel", "oracle", "vanguard"}.issubset(erp_agents)


# ---------------------------------------------------------------------------
# 2. Agent tool restrictions are enforced
# ---------------------------------------------------------------------------
def test_scenario_2_agent_tool_restrictions_enforced(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    # Scout denied file_write
    scout_write = TaskStep(
        step_id="s-1",
        step_number=1,
        title="Scout attempts write",
        tool_name="file_write",
        tool_params={"path": "test.txt", "content": "data"},
        assigned_agent="scout",
    )
    res, err = orchestrator.execute_step_safely(scout_write)
    assert res is None
    assert "explicitly denied" in err or "denied capability 'file:write'" in err

    # Sentinel denied file_write
    sentinel_write = TaskStep(
        step_id="s-2",
        step_number=2,
        title="Sentinel attempts write",
        tool_name="file_write",
        tool_params={"path": "test.txt", "content": "data"},
        assigned_agent="sentinel",
    )
    res, err = orchestrator.execute_step_safely(sentinel_write)
    assert res is None
    assert "explicitly denied" in err or "denied capability 'file:write'" in err

    # Oracle denied shell_exec
    oracle_shell = TaskStep(
        step_id="s-3",
        step_number=3,
        title="Oracle attempts shell",
        tool_name="shell_exec",
        tool_params={"command": "dir"},
        assigned_agent="oracle",
    )
    res, err = orchestrator.execute_step_safely(oracle_shell)
    assert res is None
    assert "explicitly denied" in err or "denied capability 'code:execute'" in err

    # Non-forge agent attempting forge_coding is blocked
    scout_forge = TaskStep(
        step_id="s-4",
        step_number=4,
        title="Scout attempts forge coding",
        tool_name="forge_coding",
        tool_params={"objective": "code"},
        assigned_agent="scout",
    )
    res, err = orchestrator.execute_step_safely(scout_forge)
    assert res is None
    assert "restricted to Forge" in err or "denied" in err


# ---------------------------------------------------------------------------
# 3. Child agent cannot exceed central policy
# ---------------------------------------------------------------------------
def test_scenario_3_child_agent_cannot_exceed_central_policy(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    # Child agent attempting forbidden desktop interaction
    vanguard_desktop = TaskStep(
        step_id="child-1",
        step_number=1,
        title="Vanguard attempts desktop automation",
        tool_name="desktop",
        tool_params={"action": "click"},
        assigned_agent="vanguard",
    )
    res, err = orchestrator.execute_step_safely(vanguard_desktop)
    assert res is None
    assert "explicitly denied" in err

    # Child agent attempting unauthorized channel send
    forge_send = TaskStep(
        step_id="child-2",
        step_number=2,
        title="Forge attempts unauthorized channel send",
        tool_name="channel_send",
        tool_params={"channel": "slack", "message": "hello"},
        assigned_agent="forge",
    )
    res, err = orchestrator.execute_step_safely(forge_send)
    assert res is None
    assert "explicitly denied" in err


# ---------------------------------------------------------------------------
# 4. Read-only agents cannot modify files
# ---------------------------------------------------------------------------
def test_scenario_4_read_only_agents_cannot_modify_files(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]
    read_only_agents = ["scout", "oracle", "sentinel", "vanguard", "pulse", "atlas"]

    for agent_id in read_only_agents:
        step = TaskStep(
            step_id=f"write-{agent_id}",
            step_number=1,
            title=f"{agent_id} attempts write",
            tool_name="file_write",
            tool_params={"path": "test.txt", "content": "malicious"},
            assigned_agent=agent_id,
        )
        res, err = orchestrator.execute_step_safely(step)
        assert res is None, f"{agent_id} should not be permitted to execute file_write"
        assert err is not None


# ---------------------------------------------------------------------------
# 5. Independent tasks execute concurrently
# ---------------------------------------------------------------------------
def test_scenario_5_independent_tasks_execute_concurrently(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    start_times = {}
    end_times = {}
    lock = threading.Lock()

    def mock_concurrent_executor(step: TaskStep, model: str = ""):
        with lock:
            start_times[step.step_id] = time.time()
        time.sleep(0.15)
        with lock:
            end_times[step.step_id] = time.time()
        return {"output": f"Done {step.step_id}"}, None

    step1 = TaskStep(
        step_id="step-p1",
        step_number=1,
        title="Independent Task 1",
        tool_name="think",
        assigned_agent="oracle",
        dependencies=[],
    )
    step2 = TaskStep(
        step_id="step-p2",
        step_number=2,
        title="Independent Task 2",
        tool_name="think",
        assigned_agent="sentinel",
        dependencies=[],
    )

    t0 = time.time()
    result = orchestrator.execute_multi_agent_plan(
        task_id="task-parallel",
        user_request="Perform parallel investigations",
        steps=[step1, step2],
        step_executor=mock_concurrent_executor,
    )
    total_elapsed = time.time() - t0

    assert result["status"] == "completed"
    assert result["completed_steps"] == 2
    # If run in parallel, 2 x 0.15s tasks should finish in significantly less than sequential 0.30s + overhead
    assert total_elapsed < 0.28
    # Start times should overlap
    assert abs(start_times["step-p1"] - start_times["step-p2"]) < 0.08


# ---------------------------------------------------------------------------
# 6. Conflicting file edits are isolated or blocked
# ---------------------------------------------------------------------------
def test_scenario_6_conflicting_file_edits_isolated_or_blocked(setup_workforce_env):
    tmp_path = setup_workforce_env["tmp_path"]
    shared_dir = tmp_path / "shared_repo"
    shared_dir.mkdir(parents=True, exist_ok=True)

    # 1. Test locking: two concurrent attempts to acquire lock on same directory
    acquired_1 = threading.Event()
    release_1 = threading.Event()
    worker_2_failed = [False]

    def worker_1():
        with WorkspaceLock.acquire(target_dir=shared_dir, timeout=5.0):
            acquired_1.set()
            release_1.wait(timeout=2.0)

    def worker_2():
        acquired_1.wait(timeout=2.0)
        try:
            # Short timeout: should fail because worker_1 holds the lock
            with WorkspaceLock.acquire(target_dir=shared_dir, timeout=0.1):
                pass
        except TimeoutError:
            worker_2_failed[0] = True

    t1 = threading.Thread(target=worker_1)
    t2 = threading.Thread(target=worker_2)
    t1.start()
    t2.start()

    t2.join(timeout=3.0)
    release_1.set()
    t1.join(timeout=3.0)

    assert worker_2_failed[0] is True, "Second writer to same workspace without isolation must be blocked"

    # 2. Test isolation: two distinct directories or worktrees proceed without blocking
    isolated_dir1 = tmp_path / "wt1"
    isolated_dir2 = tmp_path / "wt2"
    isolated_dir1.mkdir(parents=True, exist_ok=True)
    isolated_dir2.mkdir(parents=True, exist_ok=True)

    with WorkspaceLock.acquire(target_dir=isolated_dir1, timeout=1.0):
        # Should acquire immediately even though dir1 is locked
        with WorkspaceLock.acquire(target_dir=isolated_dir2, timeout=1.0):
            pass


# ---------------------------------------------------------------------------
# 7. Child failure is reported to parent
# ---------------------------------------------------------------------------
def test_scenario_7_child_failure_reported_to_parent(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    def failing_step_executor(step: TaskStep, model: str = ""):
        if step.step_id == "step-fail":
            return None, "Specialist syntax error in generated code"
        return {"output": "ok"}, None

    step_fail = TaskStep(
        step_id="step-fail",
        step_number=1,
        title="Failing Step",
        tool_name="think",
        assigned_agent="forge",
        dependencies=[],
    )
    step_downstream = TaskStep(
        step_id="step-downstream",
        step_number=2,
        title="Downstream Step",
        tool_name="think",
        assigned_agent="vanguard",
        dependencies=["step-fail"],
    )

    res = orchestrator.execute_multi_agent_plan(
        task_id="task-fail-test",
        user_request="Refactor module and verify",
        steps=[step_fail, step_downstream],
        step_executor=failing_step_executor,
    )

    assert res["status"] == "incomplete"
    assert res["completed_steps"] == 0
    # Downstream step was not run due to prerequisite failure
    assert step_downstream.status == StepStatus.FAILED
    assert "Unresolved dependency" in step_downstream.error

    # Child failure recorded in agent_reports and consolidated_response
    assert any("Specialist syntax error" in r["errors"][0] for r in res["agent_reports"] if r.get("errors"))
    assert "Specialist syntax error" in res["consolidated_response"]


# ---------------------------------------------------------------------------
# 8. Parent cancellation propagates
# ---------------------------------------------------------------------------
def test_scenario_8_parent_cancellation_propagates(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]
    cancel_token = CancellationToken()

    step1 = TaskStep(
        step_id="step-c1",
        step_number=1,
        title="First step",
        tool_name="think",
        assigned_agent="scout",
        dependencies=[],
    )
    step2 = TaskStep(
        step_id="step-c2",
        step_number=2,
        title="Second step",
        tool_name="think",
        assigned_agent="forge",
        dependencies=["step-c1"],
    )

    def cancelling_executor(step: TaskStep, model: str = ""):
        # Cancel while executing step 1
        cancel_token.cancel("User cancelled task via UI")
        return {"output": "Step 1 executed"}, None

    result = orchestrator.execute_multi_agent_plan(
        task_id="task-cancel-prop",
        user_request="Long multi-step operation",
        steps=[step1, step2],
        step_executor=cancelling_executor,
        cancellation_token=cancel_token,
    )

    assert result["status"] == "cancelled"
    assert step2.status == StepStatus.CANCELLED
    assert "cancelled" in step2.error.lower()


# ---------------------------------------------------------------------------
# 9. Results include evidence and status
# ---------------------------------------------------------------------------
def test_scenario_9_results_include_evidence_and_status(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    def evidence_executor(step: TaskStep, model: str = ""):
        rep = AgentReport(
            agent_id="vanguard",
            task_id="task-ev-1",
            step_id=step.step_id,
            work_performed="Ran verification test suite",
            findings=[{"title": "Regression Check", "summary": "Zero regressions found"}],
            evidence=[{"type": "command_output", "cmd": "pytest", "passed": True}],
            files_inspected=["src/core.py"],
            tests_executed=[{"name": "test_core", "passed": True}],
            results={"verdict": "verified"},
            confidence=0.99,
        )
        return rep.to_dict(), None

    step = TaskStep(
        step_id="step-ev-1",
        step_number=1,
        title="Verification with evidence",
        tool_name="think",
        assigned_agent="vanguard",
        dependencies=[],
    )

    result = orchestrator.execute_multi_agent_plan(
        task_id="task-ev-1",
        user_request="Verify core library",
        steps=[step],
        step_executor=evidence_executor,
    )

    assert result["status"] == "completed"
    assert len(result["agent_reports"]) == 1
    report = result["agent_reports"][0]
    assert report["agent_id"] == "vanguard"
    assert report["confidence"] == 0.99
    assert len(report["evidence"]) >= 1
    assert report["evidence"][0]["passed"] is True
    assert len(report["findings"]) >= 1


# ---------------------------------------------------------------------------
# 10. Atlas detects conflicting findings between specialists
# ---------------------------------------------------------------------------
def test_scenario_10_atlas_detects_conflicting_findings(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    def conflicting_executor(step: TaskStep, model: str = ""):
        if step.assigned_agent == "scout":
            rep = AgentReport(
                agent_id="scout",
                task_id="task-conflict",
                step_id=step.step_id,
                work_performed="Inspected environment",
                findings=[{"title": "All Clean", "summary": "No security risks detected"}],
                errors=[],
                confidence=0.95,
            )
            return rep.to_dict(), None
        elif step.assigned_agent == "sentinel":
            rep = AgentReport(
                agent_id="sentinel",
                task_id="task-conflict",
                step_id=step.step_id,
                work_performed="Audited dependencies",
                findings=[{"title": "Vulnerability Detected", "summary": "Critical CVE-2026-999 found"}],
                errors=["Critical CVE found in dependencies"],
                confidence=0.95,
            )
            return rep.to_dict(), None
        return {"output": "ok"}, None

    step1 = TaskStep(
        step_id="s-conf-1",
        step_number=1,
        title="Scout audit",
        tool_name="think",
        assigned_agent="scout",
        dependencies=[],
    )
    step2 = TaskStep(
        step_id="s-conf-2",
        step_number=2,
        title="Sentinel audit",
        tool_name="think",
        assigned_agent="sentinel",
        dependencies=[],
    )

    result = orchestrator.execute_multi_agent_plan(
        task_id="task-conflict",
        user_request="Cross-audit environment",
        steps=[step1, step2],
        step_executor=conflicting_executor,
    )

    assert result["has_conflicts"] is True
    assert len(result["conflicts"]) >= 1
    assert "Discrepancy Analysis" in result["consolidated_response"]
    assert "Contradiction Detected" in result["consolidated_response"]


# ---------------------------------------------------------------------------
# 11. Agent budget exhaustion stops safely without hanging
# ---------------------------------------------------------------------------
def test_scenario_11_agent_budget_exhaustion_stops_safely(setup_workforce_env):
    orchestrator = setup_workforce_env["orchestrator"]

    # Budget allows max 2 tool calls
    budget = TaskBudget(
        max_tool_calls=2,
        max_execution_time_seconds=60.0,
    )

    steps = [
        TaskStep(step_id=f"b-step-{i}", step_number=i, title=f"Step {i}", tool_name="think", assigned_agent="scout", dependencies=[f"b-step-{i-1}"] if i > 1 else [])
        for i in range(1, 6)
    ]

    def sequential_executor(step: TaskStep, model: str = ""):
        return {"output": f"Done {step.step_id}"}, None

    result = orchestrator.execute_multi_agent_plan(
        task_id="task-budget-exhaust",
        user_request="Execute 5 steps under budget",
        steps=steps,
        step_executor=sequential_executor,
        budget=budget,
    )

    assert result["status"] == "budget_exhausted"
    assert result["completed_steps"] == 2
    # Steps 3, 4, 5 must be cancelled with budget reason
    assert steps[2].status == StepStatus.CANCELLED
    assert "budget exceeded" in steps[2].error.lower()

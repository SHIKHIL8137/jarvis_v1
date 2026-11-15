import tempfile
from pathlib import Path
import pytest

from openjarvis.tasks.models import StepStatus, TaskStep
from openjarvis.workforce.communication import ConflictResolver, TeamMessageBus
from openjarvis.workforce.models import AgentReport
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


@pytest.fixture
def temp_workforce_setup():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "workforce.db"
        store = WorkforceStore(db_path=db_path)
        registry = AgentWorkforceRegistry(store=store)
        registry.initialize_workforce()
        orchestrator = AtlasOrchestrator(registry=registry)
        yield registry, orchestrator, store
        store.close()


def test_dynamic_task_planning_erp(temp_workforce_setup):
    registry, orchestrator, store = temp_workforce_setup

    query = "Jarvis, inspect my ERP project, identify bugs, check security, and recommend performance improvements."
    steps = orchestrator.plan_task(query)

    assert len(steps) >= 4

    assigned_agents = [s.assigned_agent for s in steps]
    assert "scout" in assigned_agents
    assert "forge" in assigned_agents
    assert "sentinel" in assigned_agents
    assert "oracle" in assigned_agents
    assert "vanguard" in assigned_agents

    # Verify dependency structure
    # Scout should have no dependencies (root step)
    scout_step = [s for s in steps if s.assigned_agent == "scout"][0]
    assert len(scout_step.dependencies) == 0

    # Vanguard should depend on prior steps
    vanguard_step = [s for s in steps if s.assigned_agent == "vanguard"][0]
    assert len(vanguard_step.dependencies) >= 1


def test_multi_agent_execution_and_synthesis(temp_workforce_setup):
    registry, orchestrator, store = temp_workforce_setup

    steps = [
        TaskStep(
            step_id="step-1",
            step_number=1,
            title="Locate files",
            description="Find ERP source code files",
            tool_name="file_read",
            assigned_agent="scout",
            dependencies=[],
        ),
        TaskStep(
            step_id="step-2",
            step_number=2,
            title="Inspect Database layer",
            description="Inspect queries for N+1 bottlenecks",
            tool_name="think",
            assigned_agent="forge",
            dependencies=["step-1"],
        ),
        TaskStep(
            step_id="step-3",
            step_number=3,
            title="Security audit",
            description="Check SQL injection risk",
            tool_name="think",
            assigned_agent="sentinel",
            dependencies=["step-1"],
        ),
    ]

    # Mock step executor
    def mock_executor(step: TaskStep):
        return {"output": f"Done {step.title}"}, None

    result = orchestrator.execute_multi_agent_plan(
        task_id="task-test-1",
        user_request="Inspect ERP database performance and security",
        steps=steps,
        step_executor=mock_executor,
    )

    assert result["total_steps"] == 3
    assert result["completed_steps"] == 3
    assert len(result["agent_reports"]) == 3
    assert "Mission Report" in result["consolidated_response"]
    assert "ATLAS (Chief of Staff)" in result["consolidated_response"]
    assert "Forge" in result["consolidated_response"]
    assert "Sentinel" in result["consolidated_response"]


def test_team_messaging_and_conflict_resolution(temp_workforce_setup):
    registry, orchestrator, store = temp_workforce_setup
    bus = TeamMessageBus(store=store)

    # Agent message
    msg_id = bus.send_agent_message(
        task_id="task-100",
        from_agent="forge",
        to_agent="sentinel",
        message_type="FINDING",
        content={"detail": "Found database query without parameterized input on line 42"},
    )
    assert msg_id.startswith("msg-")

    messages = bus.get_task_messages("task-100")
    assert len(messages) == 1
    assert messages[0]["from_agent"] == "forge"
    assert messages[0]["to_agent"] == "sentinel"

    # Scoped context
    rep1 = AgentReport(
        agent_id="forge",
        task_id="task-100",
        work_performed="Audited database models",
        findings=[{"title": "Unparameterized query", "summary": "Found raw SQL concat"}],
    )
    context = bus.build_scoped_context(
        task_id="task-100",
        for_agent_id="sentinel",
        prior_reports=[rep1],
        user_request="Check ERP security",
    )
    assert len(context["prior_findings"]) == 1
    assert context["prior_findings"][0]["source_agent"] == "forge"

    # Conflict resolver
    rep2 = AgentReport(
        agent_id="sentinel",
        task_id="task-100",
        work_performed="Ran security checks",
        errors=["Critical CVE found in database driver"],
    )
    resolved = ConflictResolver.detect_and_resolve([rep1, rep2])
    assert resolved["has_conflicts"] is True
    assert len(resolved["conflicts"]) == 1

"""End-to-end acceptance tests for the autonomous multi-agent workforce.

Scenario: "Jarvis, analyze my ERP project, identify bugs, check security, and recommend improvements"
Flow:
  ATLAS (Chief of Staff) plans DAG:
    1. SCOUT gathers context & inspects repository files.
    2. FORGE analyzes software architecture & investigates bugs.
    3. SENTINEL inspects security, permissions, and dependencies.
    4. ORACLE researches ERP best practices and comparisons.
    5. VANGUARD verifies QA criteria and validates specialist claims.
    6. ATLAS consolidates all specialist reports into an executive summary.
"""

from __future__ import annotations

import pytest
from openjarvis.core.events import EventBus
from openjarvis.core.registry import ToolRegistry
from openjarvis.tasks.models import StepStatus, TaskStep
from openjarvis.workforce.communication import ConflictResolver, TeamMessageBus
from openjarvis.workforce.models import AgentReport, AgentRuntimeState
from openjarvis.workforce.orchestrator import AtlasOrchestrator
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


@pytest.fixture
def e2e_context(tmp_path):
    bus = EventBus()
    store = WorkforceStore(db_path=tmp_path / "e2e_workforce.db")
    registry = AgentWorkforceRegistry(store=store, bus=bus)
    registry.initialize_workforce()

    message_bus = TeamMessageBus(store=store, bus=bus)
    conflict_resolver = ConflictResolver()
    orchestrator = AtlasOrchestrator(registry=registry, bus=bus)

    return {
        "store": store,
        "registry": registry,
        "orchestrator": orchestrator,
        "message_bus": message_bus,
        "conflict_resolver": conflict_resolver,
        "bus": bus,
    }


def test_erp_analysis_multi_agent_e2e(e2e_context):
    orchestrator: AtlasOrchestrator = e2e_context["orchestrator"]
    registry: AgentWorkforceRegistry = e2e_context["registry"]
    store: WorkforceStore = e2e_context["store"]
    msg_bus: TeamMessageBus = e2e_context["message_bus"]

    goal = "Jarvis, analyze my ERP project, identify bugs, check security, and recommend improvements"

    # 1. Atlas decomposes the goal into a multi-agent DAG
    steps = orchestrator.plan_task(goal)
    assert len(steps) >= 4

    assigned_agents = [s.assigned_agent for s in steps]
    assert "scout" in assigned_agents
    assert "forge" in assigned_agents
    assert "sentinel" in assigned_agents
    assert "vanguard" in assigned_agents

    # Verify DAG dependency ordering
    step_map = {s.step_id: s for s in steps}
    scout_step = next(s for s in steps if s.assigned_agent == "scout")
    forge_step = next(s for s in steps if s.assigned_agent == "forge")
    sentinel_step = next(s for s in steps if s.assigned_agent == "sentinel")
    vanguard_step = next(s for s in steps if s.assigned_agent == "vanguard")

    assert scout_step.dependencies == []
    assert scout_step.step_id in forge_step.dependencies
    assert scout_step.step_id in sentinel_step.dependencies
    assert forge_step.step_id in vanguard_step.dependencies or sentinel_step.step_id in vanguard_step.dependencies

    # 2. Simulated execution with live heartbeat tracking and inter-agent messages
    task_id = "task-erp-audit-001"

    def mock_step_executor(step: TaskStep):
        # Specialist executes step and messages team
        agent = step.assigned_agent
        registry.set_agent_state(agent, AgentRuntimeState.BUSY, current_task_id=task_id)

        if agent == "scout":
            msg_bus.post_message(
                task_id=task_id,
                from_agent="scout",
                to_agent="forge",
                message_type="FINDING",
                content="Located ERP core modules: accounting, inventory, auth, api_gateway.",
                metadata={"module_count": 4},
            )
            return {"modules": ["accounting", "inventory", "auth"]}, None

        elif agent == "forge":
            msg_bus.post_message(
                task_id=task_id,
                from_agent="forge",
                to_agent="vanguard",
                message_type="FINDING",
                content="Identified N+1 query bug in inventory ledger and unhandled KeyError in auth router.",
                metadata={"bugs_found": 2},
            )
            return {"bugs": ["N+1 query in inventory", "Unhandled KeyError in auth"]}, None

        elif agent == "sentinel":
            msg_bus.post_message(
                task_id=task_id,
                from_agent="sentinel",
                to_agent="vanguard",
                message_type="FINDING",
                content="Security audit found hardcoded JWT secret fallback and lack of rate limiting.",
                metadata={"vulnerabilities": 2},
            )
            return {"risks": ["Hardcoded JWT secret", "Missing API rate limiting"]}, None

        elif agent == "oracle":
            return {"best_practices": ["Adopt event-driven ledger", "Use Vault for secrets"]}, None

        elif agent == "vanguard":
            return {"verification": "All findings validated against codebase criteria"}, None

        return {"status": "ok"}, None

    # Execute plan through Atlas DAG engine
    result = orchestrator.execute_multi_agent_plan(
        task_id=task_id,
        user_request=goal,
        steps=steps,
        step_executor=mock_step_executor,
    )

    # 3. Verify execution result and Atlas synthesis
    assert result["status"] == "completed"
    assert result["total_steps"] == len(steps)
    assert result["completed_steps"] == len(steps)
    assert len(result["reports"]) >= 4

    consolidated = result["consolidated_report"]
    assert "Mission Report" in consolidated
    assert "Summary of Specialist Findings" in consolidated
    assert "Team Verification" in consolidated
    assert "VANGUARD" in consolidated

    # 4. Verify inter-agent messages persisted in database
    messages = store.list_messages(task_id)
    assert len(messages) >= 3
    senders = [m["from_agent"] for m in messages]
    assert "scout" in senders
    assert "forge" in senders
    assert "sentinel" in senders

    # 5. Verify reports persisted in database
    persisted_reports = store.list_reports(task_id=task_id)
    assert len(persisted_reports) == len(steps)

    # 6. Verify agents returned to IDLE state
    for s in steps:
        st = registry.get_agent_state(s.assigned_agent)
        assert st in (AgentRuntimeState.IDLE, AgentRuntimeState.ONLINE)

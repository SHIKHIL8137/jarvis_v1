import tempfile
from pathlib import Path
import pytest

from openjarvis.workforce.models import AgentRuntimeState
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


@pytest.fixture
def temp_workforce_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "workforce.db"
        store = WorkforceStore(db_path=db_path)
        yield store
        store.close()


def test_workforce_registry_initialization(temp_workforce_store):
    registry = AgentWorkforceRegistry(store=temp_workforce_store)
    registry.initialize_workforce()

    agents = registry.list_agents()
    assert len(agents) == 7

    agent_ids = {a["id"] for a in agents}
    assert "atlas" in agent_ids
    assert "oracle" in agent_ids
    assert "forge" in agent_ids
    assert "sentinel" in agent_ids
    assert "scout" in agent_ids
    assert "vanguard" in agent_ids
    assert "pulse" in agent_ids

    # Atlas should be online and idle
    assert registry.get_agent_state("atlas") in (AgentRuntimeState.IDLE, AgentRuntimeState.ONLINE)

    # Summary
    summary = registry.get_team_summary()
    assert summary.total_registered == 7
    assert summary.total_online >= 6


def test_heartbeat_and_presence(temp_workforce_store):
    registry = AgentWorkforceRegistry(store=temp_workforce_store)
    registry.initialize_workforce()

    # Forge starts working on a task
    registry.record_heartbeat("forge", current_task_id="task-erp-scan")
    assert registry.get_agent_state("forge") == AgentRuntimeState.BUSY

    summary = registry.get_team_summary()
    assert summary.total_busy == 1
    assert summary.active_tasks == 1

    # Forge completes task
    registry.record_heartbeat("forge", current_task_id=None)
    assert registry.get_agent_state("forge") == AgentRuntimeState.IDLE


def test_natural_language_queries(temp_workforce_store):
    registry = AgentWorkforceRegistry(store=temp_workforce_store)
    registry.initialize_workforce()

    # Query count
    ans = registry.answer_status_query("Jarvis, how many agents are online?")
    assert ans is not None
    assert "7 agents are registered" in ans
    assert "online" in ans

    # Query available
    ans_avail = registry.answer_status_query("Which agents are available right now?")
    assert ans_avail is not None
    assert "Atlas" in ans_avail or "Forge" in ans_avail

    # Forge busy
    registry.record_heartbeat("forge", current_task_id="task-123")
    ans_forge = registry.answer_status_query("What is Forge doing?")
    assert ans_forge is not None
    assert "BUSY executing task task-123" in ans_forge

    # Non status query
    assert registry.answer_status_query("Can you write a python script for me?") is None


def test_startup_announcement(temp_workforce_store):
    registry = AgentWorkforceRegistry(store=temp_workforce_store)
    registry.initialize_workforce()

    announcement = registry.generate_startup_announcement()
    assert "Jarvis is online" in announcement
    assert "Atlas" in announcement
    assert "Oracle" in announcement
    assert "Forge" in announcement
    assert "Awaiting your instructions" in announcement

"""Tests for Agent Workforce REST API routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.engine._base import InferenceEngine
from openjarvis.server.app import create_app
from openjarvis.tasks.engine import BackgroundTaskEngine
from openjarvis.tasks.store import TaskStore
from openjarvis.workforce.registry import AgentWorkforceRegistry
from openjarvis.workforce.store import WorkforceStore


class DummyEngine(InferenceEngine):
    def complete(self, messages, **kwargs):
        from openjarvis.core.types import CompletionResult, Message, Role

        return CompletionResult(message=Message(role=Role.ASSISTANT, content="dummy"))

    def stream(self, messages, **kwargs):
        return iter([])

    def generate(self, prompt, **kwargs):
        return "dummy"

    def list_models(self):
        return ["dummy"]

    def health(self):
        return True


@pytest.fixture
def workforce_client(tmp_path) -> TestClient:
    engine = DummyEngine()
    cfg = JarvisConfig()
    app = create_app(engine=engine, model="dummy", config=cfg)

    store = WorkforceStore(db_path=tmp_path / "workforce.db")
    registry = AgentWorkforceRegistry(store=store)
    registry.initialize_workforce(engine=engine)

    app.state.workforce_registry = registry
    app.state.task_engine = BackgroundTaskEngine(store=TaskStore(db_path=tmp_path / "tasks.db"))

    return TestClient(app)


def test_list_agents_and_summary(workforce_client: TestClient):
    res = workforce_client.get("/v1/workforce/agents")
    assert res.status_code == 200
    agents = res.json()["agents"]
    assert len(agents) == 7
    agent_ids = [a["id"] for a in agents]
    assert "atlas" in agent_ids
    assert "forge" in agent_ids
    assert "sentinel" in agent_ids

    sum_res = workforce_client.get("/v1/workforce/summary")
    assert sum_res.status_code == 200
    summary = sum_res.json()
    assert summary["total_registered"] == 7
    assert summary["total_online"] >= 1


def test_agent_detail_enable_disable(workforce_client: TestClient):
    res = workforce_client.get("/v1/workforce/agents/forge")
    assert res.status_code == 200
    data = res.json()
    assert data["manifest"]["id"] == "forge"
    assert "Software Engineering Specialist" in data["manifest"]["role"]

    # Disable forge
    dis_res = workforce_client.post("/v1/workforce/agents/forge/disable")
    assert dis_res.status_code == 200
    assert dis_res.json()["status"] == "disabled"

    forge_state = workforce_client.get("/v1/workforce/agents/forge").json()
    assert forge_state["status"] == "offline"

    # Enable forge
    en_res = workforce_client.post("/v1/workforce/agents/forge/enable")
    assert en_res.status_code == 200
    assert en_res.json()["status"] == "enabled"


def test_heartbeat_and_nl_query(workforce_client: TestClient):
    # Post heartbeat
    hb_res = workforce_client.post(
        "/v1/workforce/agents/sentinel/heartbeat",
        json={"current_task_id": "sec-audit-1", "active_model": "deepseek-coder"},
    )
    assert hb_res.status_code == 200

    # Query status
    q_res = workforce_client.post(
        "/v1/workforce/query",
        json={"query": "How many agents are online?"},
    )
    assert q_res.status_code == 200
    answer = q_res.json()["answer"]
    assert "currently online" in answer

    # Announcement
    ann_res = workforce_client.get("/v1/workforce/announcement")
    assert ann_res.status_code == 200
    assert "multi-agent workforce" in ann_res.json()["announcement"]


def test_delegate_team_task(workforce_client: TestClient):
    res = workforce_client.post(
        "/v1/workforce/tasks",
        json={"goal": "Analyze ERP project, identify bugs, check security, and recommend improvements"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "delegated"
    assert "task_id" in data
    assert len(data["steps"]) >= 4

    # Timeline endpoint
    t_res = workforce_client.get(f"/v1/workforce/tasks/{data['task_id']}/timeline")
    assert t_res.status_code == 200
    assert "messages" in t_res.json()
    assert "reports" in t_res.json()

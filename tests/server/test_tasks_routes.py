"""Tests for Background Task REST API routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.engine._base import InferenceEngine
from openjarvis.server.app import create_app
from openjarvis.tasks.engine import BackgroundTaskEngine
from openjarvis.tasks.store import TaskStore


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
def client(tmp_path) -> TestClient:
    engine = DummyEngine()
    cfg = JarvisConfig()
    app = create_app(engine=engine, model="dummy", config=cfg)
    app.state.task_engine = BackgroundTaskEngine(store=TaskStore(db_path=tmp_path / "tasks.db"))
    return TestClient(app)


def test_task_crud_routes(client: TestClient):
    # Create task
    res = client.post(
        "/v1/tasks",
        json={
            "title": "API Test Task",
            "user_request": "Execute tests",
            "agent_profile": "general",
        },
    )
    assert res.status_code == 200
    data = res.json()
    task_id = data["id"]
    assert data["title"] == "API Test Task"
    assert data["status"] == "created"

    # List tasks
    list_res = client.get("/v1/tasks")
    assert list_res.status_code == 200
    assert list_res.json()["count"] >= 1

    # Get details
    get_res = client.get(f"/v1/tasks/{task_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == task_id

    # Steer task
    steer_res = client.post(
        f"/v1/tasks/{task_id}/steer",
        json={"instruction": "Keep output concise"},
    )
    assert steer_res.status_code == 200
    assert steer_res.json()["status"] == "steered"

    # Cancel task
    cancel_res = client.post(f"/v1/tasks/{task_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"

"""Tests for n8n tool, client, dual authentication, cookie caching, and approval gating."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.approval_store import ApprovalStore, STATUS_APPROVED
from openjarvis.tools.n8n import N8nClient, N8nTool


@pytest.fixture
def temp_approval_store(tmp_path):
    return ApprovalStore(db_path=str(tmp_path / "approvals.db"))


def test_n8n_health_online():
    client = N8nClient(base_url="https://mock-n8n.local")
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.getcode.return_value = 200

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = client.check_health()
        assert res["online"] is True
        assert res["status_code"] == 200
        assert res["base_url"] == "https://mock-n8n.local"


def test_n8n_health_offline():
    client = N8nClient(base_url="https://offline-n8n.local")
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        res = client.check_health()
        assert res["online"] is False
        assert "error" in res


def test_n8n_session_cookie_caching():
    client = N8nClient(
        base_url="https://mock-n8n.local",
        email_or_login_id="admin@example.com",
        password="secretpassword",
    )

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.headers.get.return_value = "n8n-auth=mock_token_abc123; Path=/; HttpOnly"

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        # First call: should authenticate
        cookie1 = client._ensure_session()
        assert cookie1 == "n8n-auth=mock_token_abc123"
        assert client._session_cookie == "n8n-auth=mock_token_abc123"
        assert client._session_cookie_expires_at > time.time() + 100000
        assert mock_urlopen.call_count == 1

        # Second call: must reuse cached cookie without calling /rest/login again (no 429)
        cookie2 = client._ensure_session()
        assert cookie2 == "n8n-auth=mock_token_abc123"
        assert mock_urlopen.call_count == 1
        assert cookie2 == "n8n-auth=mock_token_abc123"
        assert mock_urlopen.call_count == 1


def test_n8n_list_workflows():
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    fake_response = {
        "data": [
            {
                "id": "wf-1",
                "name": "Health Monitor",
                "active": True,
                "createdAt": "2026-01-01T00:00:00Z",
                "tags": [{"name": "ops"}],
            },
            {
                "id": "wf-2",
                "name": "Instagram Sync",
                "active": False,
                "tags": [],
            },
        ]
    }

    with patch.object(client, "_make_request", return_value=fake_response):
        wfs = client.list_workflows()
        assert len(wfs) == 2
        assert wfs[0]["id"] == "wf-1"
        assert wfs[0]["name"] == "Health Monitor"
        assert wfs[0]["active"] is True
        assert wfs[0]["tags"] == ["ops"]


def test_n8n_tool_execute_approval_gate(temp_approval_store):
    tool = N8nTool(base_url="https://mock-n8n.local", approval_store=temp_approval_store)

    # 1. First execution attempt: not approved yet, must queue approval request
    res = tool.execute(action="execute_workflow", workflow_id="wf-sensitive-1", payload={"env": "prod"})
    assert res.success is False
    assert "Approval queued with ID:" in res.content

    # Extract approval ID
    actions = temp_approval_store.list_pending()
    assert len(actions) == 1
    approval_id = actions[0].id
    assert actions[0].action_type == "n8n_execute_workflow"

    # 2. Approve the action in ApprovalStore
    temp_approval_store.update_status(approval_id, STATUS_APPROVED)

    # 3. Second execution attempt with approval_id: should proceed to execution
    with patch.object(tool.client, "execute_workflow", return_value={"status": "completed"}):
        res2 = tool.execute(
            action="execute_workflow",
            workflow_id="wf-sensitive-1",
            payload={"env": "prod"},
            approval_id=approval_id,
        )
        assert res2.success is True
        assert "executed" in res2.content

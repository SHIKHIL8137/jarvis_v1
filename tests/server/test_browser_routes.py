"""Tests for desktop browser server API routes and WebSocket extension bridge."""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from openjarvis.browser.manager import get_browser_manager
from openjarvis.browser.models import BrowserActionResult, BrowserTabInfo
from openjarvis.server.app import create_app


def _make_mock_engine():
    engine = MagicMock()
    engine.engine_id = "mock"
    engine.health.return_value = True
    engine.list_models.return_value = ["test-model"]
    return engine


@pytest.fixture
def client():
    app = create_app(_make_mock_engine(), "test-model")
    return TestClient(app)


def test_get_browser_status(client):
    response = client.get("/v1/browser/status")
    assert response.status_code == 200
    data = response.json()
    assert "state" in data
    assert "mode" in data
    assert "tab_count" in data
    assert "pairing_token" in data


def test_get_browser_tabs(client):
    mgr = get_browser_manager()
    tabs = [
        BrowserTabInfo(tab_id=1, url="https://www.linkedin.com/feed", title="LinkedIn"),
        BrowserTabInfo(tab_id=2, url="https://github.com", title="GitHub"),
    ]
    with patch.object(mgr, "list_tabs", return_value=tabs):
        response = client.get("/v1/browser/tabs")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["tabs"]) == 2
        assert data["tabs"][0]["tab_id"] == 1


def test_open_or_reuse_tab_endpoint(client):
    mgr = get_browser_manager()
    expected_result = BrowserActionResult(
        success=True,
        action="activate_tab",
        message="Activated existing tab: 'LinkedIn' (tab ID 1). No duplicate tab created.",
        reused_existing=True,
        tab=BrowserTabInfo(tab_id=1, url="https://www.linkedin.com", title="LinkedIn"),
    )
    with patch.object(mgr, "open_or_reuse", return_value=expected_result) as mock_open:
        response = client.post(
            "/v1/browser/tabs/open",
            json={"destination": "https://www.linkedin.com", "explicit_new_tab": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["reused_existing"] is True
        assert data["action"] == "activate_tab"
        mock_open.assert_called_once_with(
            query_or_url="https://www.linkedin.com",
            explicit_new_tab=False,
            task_context="",
        )


def test_activate_tab_endpoint(client):
    mgr = get_browser_manager()
    with patch.object(mgr, "activate_tab", return_value=True) as mock_activate:
        response = client.post(
            "/v1/browser/tabs/42/activate",
            json={"window_id": 10, "title_hint": "LinkedIn"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["activated"] is True
        assert data["tab_id"] == "42"
        mock_activate.assert_called_once_with("42", window_id=10, title_hint="LinkedIn")


def test_close_tab_endpoint(client):
    mgr = get_browser_manager()
    with patch.object(mgr, "close_tab", return_value=True) as mock_close:
        response = client.delete("/v1/browser/tabs/42")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["closed"] is True
        mock_close.assert_called_once_with("42")


def test_browser_connect_endpoint(client):
    response = client.post("/v1/browser/connect")
    assert response.status_code == 200
    data = response.json()
    assert "state" in data
    assert "mode" in data


def test_get_extension_token_endpoint(client):
    response = client.get("/v1/browser/extension/token")
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert len(data["token"]) >= 24


def test_extension_websocket_auth(client):
    mgr = get_browser_manager()
    valid_token = mgr.extension_bridge.token

    # 1. Reject invalid token
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/browser/extension/ws?token=wrong_secret"):
            pass

    # 2. Accept valid token
    with client.websocket_connect(f"/v1/browser/extension/ws?token={valid_token}") as ws:
        # Send tab report
        ws.send_text('{"type": "tabs_report", "tabs": [{"id": 1, "url": "https://linkedin.com", "title": "LinkedIn"}]}')
        assert mgr.extension_bridge.is_connected is True
        tabs = mgr.extension_bridge.get_tabs()
        assert len(tabs) == 1
        assert tabs[0].tab_id == 1

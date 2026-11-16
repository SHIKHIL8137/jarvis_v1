"""Unit tests for ExtensionBridge: token auth, tab caching, command dispatch, and disconnects."""

import asyncio
import json
import pytest

from openjarvis.browser.extension_bridge import ExtensionBridge


@pytest.fixture
def bridge():
    return ExtensionBridge()


def test_token_validation(bridge):
    assert bridge.validate_token(bridge.token) is True
    assert bridge.validate_token(f"  {bridge.token}  ") is True
    assert bridge.validate_token("invalid_token_12345678901234") is False
    assert bridge.validate_token("") is False
    assert bridge.validate_token(None) is False


def test_tab_and_window_caching(bridge):
    raw_tabs = [
        {"id": 1, "windowId": 100, "url": "https://www.linkedin.com/feed", "title": "LinkedIn", "active": True},
        {"id": 2, "windowId": 100, "url": "https://github.com", "title": "GitHub", "active": False},
        {"id": 3, "windowId": 200, "url": "https://youtube.com", "title": "YouTube", "active": True},
    ]

    bridge.update_tabs(raw_tabs)
    assert bridge.is_connected is True

    tabs = bridge.get_tabs()
    assert len(tabs) == 3
    assert tabs[0].tab_id == 1
    assert tabs[0].domain == "linkedin.com"
    assert tabs[0].is_active is True

    # Windows grouping
    windows = bridge.get_windows()
    assert len(windows) == 2
    win100 = next(w for w in windows if w.window_id == 100)
    assert len(win100.tabs) == 2
    assert win100.is_focused is True

    # Active tab retrieval
    active = bridge.get_active_tab()
    assert active is not None
    assert active.tab_id in (1, 3)


def test_handle_tab_activated_event(bridge):
    raw_tabs = [
        {"id": 10, "windowId": 1, "url": "https://site1.com", "title": "Site 1", "active": True},
        {"id": 20, "windowId": 1, "url": "https://site2.com", "title": "Site 2", "active": False},
    ]
    bridge.update_tabs(raw_tabs)

    bridge.handle_message(json.dumps({
        "type": "tab_activated",
        "tab_id": 20,
        "window_id": 1,
    }))

    tabs = bridge.get_tabs()
    t10 = next(t for t in tabs if t.tab_id == 10)
    t20 = next(t for t in tabs if t.tab_id == 20)
    assert t10.is_active is False
    assert t20.is_active is True


@pytest.mark.asyncio
async def test_command_dispatch_and_response(bridge):
    class MockWS:
        def __init__(self):
            self.sent_messages = []

        async def send_text(self, text):
            self.sent_messages.append(text)

    mock_ws = MockWS()
    bridge.on_ws_connect(mock_ws)

    # Launch send_command task
    cmd_task = asyncio.create_task(
        bridge.send_command("activate_tab", {"tab_id": 42}, timeout=2.0)
    )

    await asyncio.sleep(0.01)
    assert len(mock_ws.sent_messages) == 1
    payload = json.loads(mock_ws.sent_messages[0])
    cmd_id = payload["id"]
    assert payload["action"] == "activate_tab"
    assert payload["params"]["tab_id"] == 42

    # Simulate extension returning response
    bridge.handle_message(json.dumps({
        "type": "command_response",
        "cmd_id": cmd_id,
        "success": True,
        "result": {"status": "ok"},
    }))

    res = await cmd_task
    assert res == {"status": "ok"}


def test_disconnect_clears_pending_commands(bridge):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        class MockWS:
            async def send_text(self, text):
                pass

        bridge.on_ws_connect(MockWS())
        future = loop.create_future()
        bridge._pending_commands["test_cmd"] = future

        bridge.on_ws_disconnect()
        assert bridge.is_connected is False
        assert future.done() is True
        with pytest.raises(ConnectionError):
            future.result()
    finally:
        loop.close()

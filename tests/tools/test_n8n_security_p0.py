"""Regression tests for P0 n8n Approval Security requirements."""

import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from openjarvis.tools.approval_store import (
    ApprovalStore,
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    TIER_HIGH,
)
from openjarvis.tools.n8n import (
    N8nClient,
    N8nTool,
    _canonical_payload_hash,
    _parse_cookie_expiry,
    _sanitize_path,
    _validate_base_url,
)


@pytest.fixture
def temp_approval_store(tmp_path):
    db_file = tmp_path / "test_approvals.db"
    return ApprovalStore(str(db_file))


def test_url_validation_rejects_ssrf_and_invalid_schemes():
    """Requirement 11: Validate base URL against non-HTTP/HTTPS schemes and path traversal."""
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        _validate_base_url("file:///etc/passwd")

    with pytest.raises(ValueError, match="Invalid URL scheme"):
        _validate_base_url("gopher://localhost:8080")

    with pytest.raises(ValueError, match="missing network location"):
        _validate_base_url("http://")

    valid = _validate_base_url("https://n8n.example.com/")
    assert valid == "https://n8n.example.com"


def test_path_sanitization_rejects_directory_traversal():
    """Requirement 11: Reject path traversal like ../ and null bytes."""
    with pytest.raises(ValueError, match="path traversal"):
        _sanitize_path("../../../etc/shadow")

    with pytest.raises(ValueError, match="path traversal"):
        _sanitize_path("webhooks/..\\secret")

    clean = _sanitize_path("webhook/deploy")
    assert clean == "/webhook/deploy"


def test_cookie_expiry_parsing_respects_max_age_and_expires():
    """Requirement 9: Respect actual n8n cookie expiry rather than assuming fixed 6-day TTL."""
    now = 1000.0
    with patch("time.time", return_value=now):
        # Test Max-Age=7200
        header1 = "n8n-auth=token123; Path=/; Max-Age=7200; HttpOnly"
        exp1 = _parse_cookie_expiry(header1)
        assert exp1 == now + 7200

        # Test Expires header
        header2 = "n8n-auth=token456; Path=/; Expires=Wed, 21 Oct 2026 07:28:00 GMT"
        exp2 = _parse_cookie_expiry(header2)
        assert exp2 > now


def test_basic_health_distinguished_from_authenticated_api_health():
    """Requirement 12: Distinguish basic health reachability from authenticated API health."""
    client = N8nClient(base_url="https://n8n.example.com", api_key="secret-key")

    with patch.object(client, "list_workflows", side_effect=RuntimeError("Unauthorized")):
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.getcode.return_value = 200
            mock_open.return_value.__enter__.return_value = mock_resp

            health = client.check_health()
            assert health["online"] is True
            assert health["api_authenticated"] is False
            assert health["auth_mode"] == "api_key"


def test_webhook_trigger_requires_approval(temp_approval_store):
    """Requirement 1: Every sensitive webhook trigger must require explicit approval."""
    tool = N8nTool(approval_store=temp_approval_store)

    result = tool.execute(
        action="trigger_webhook",
        webhook_path="production-deploy",
        payload={"env": "prod"},
    )

    assert result.success is False
    assert "Action required approval" in result.content
    assert "Approval queued" in result.content


def test_approval_validates_exact_action_type(temp_approval_store):
    """Requirement 2: Approval must match exact action type."""
    tool = N8nTool(approval_store=temp_approval_store)
    perm_key = f"n8n:workflow:wf-1:{_canonical_payload_hash({'data': 1})}"

    # Queue action for n8n_trigger_webhook instead of n8n_execute_workflow
    action = temp_approval_store.queue_action(
        action_type="n8n_trigger_webhook",
        description="Trigger webhook",
        payload={"data": 1},
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    temp_approval_store.update_status(action.id, STATUS_APPROVED)

    # Attempt to use webhook approval to execute workflow
    res = tool.execute(
        action="execute_workflow",
        workflow_id="wf-1",
        payload={"data": 1},
        approval_id=action.id,
    )
    assert res.success is False
    assert "action type mismatch" in res.content.lower()


def test_approval_for_one_workflow_never_authorizes_another(temp_approval_store):
    """Requirement 3: An approval for one workflow must never authorize another workflow."""
    tool = N8nTool(approval_store=temp_approval_store)
    payload = {"command": "restart"}
    perm_key_wf1 = f"n8n:workflow:wf-1:{_canonical_payload_hash(payload)}"

    action = temp_approval_store.queue_action(
        action_type="n8n_execute_workflow",
        description="Execute workflow 1",
        payload=payload,
        permission_key=perm_key_wf1,
        tier=TIER_HIGH,
    )
    temp_approval_store.update_status(action.id, STATUS_APPROVED)

    # Attempt to execute workflow wf-2 using wf-1 approval
    res = tool.execute(
        action="execute_workflow",
        workflow_id="wf-2",
        payload=payload,
        approval_id=action.id,
    )
    assert res.success is False
    assert "permission mismatch" in res.content.lower()


def test_changed_payload_requires_new_approval(temp_approval_store):
    """Requirement 4: A changed payload must require a new approval."""
    tool = N8nTool(approval_store=temp_approval_store)
    approved_payload = {"count": 1}
    modified_payload = {"count": 999}  # Tampered payload

    perm_key = f"n8n:workflow:wf-1:{_canonical_payload_hash(approved_payload)}"
    action = temp_approval_store.queue_action(
        action_type="n8n_execute_workflow",
        description="Execute workflow 1",
        payload=approved_payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    temp_approval_store.update_status(action.id, STATUS_APPROVED)

    # Attempt execution with modified payload
    res = tool.execute(
        action="execute_workflow",
        workflow_id="wf-1",
        payload=modified_payload,
        approval_id=action.id,
    )
    assert res.success is False
    assert "permission mismatch" in res.content.lower()


def test_single_use_and_prevent_reuse_of_approval(temp_approval_store):
    """Requirement 5 & 6: Approvals are single-use; reusing consumed approval fails."""
    tool = N8nTool(approval_store=temp_approval_store)
    payload = {"action": "sync"}
    perm_key = f"n8n:workflow:wf-1:{_canonical_payload_hash(payload)}"

    action = temp_approval_store.queue_action(
        action_type="n8n_execute_workflow",
        description="Sync workflow",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    temp_approval_store.update_status(action.id, STATUS_APPROVED)

    with patch.object(tool.client, "execute_workflow", return_value={"status": "executed"}):
        # First execution succeeds and consumes approval
        res1 = tool.execute(
            action="execute_workflow",
            workflow_id="wf-1",
            payload=payload,
            approval_id=action.id,
        )
        assert res1.success is True

        # Check status is now EXECUTED
        reloaded = temp_approval_store.get_action(action.id)
        assert reloaded.status == STATUS_EXECUTED

        # Second execution with same approval ID fails immediately (no replay)
        res2 = tool.execute(
            action="execute_workflow",
            workflow_id="wf-1",
            payload=payload,
            approval_id=action.id,
        )
        assert res2.success is False
        assert "already been consumed" in res2.content.lower()


def test_expired_approval_rejection(temp_approval_store):
    """Requirement 5 & 6: Expired approvals cannot be executed."""
    tool = N8nTool(approval_store=temp_approval_store)
    payload = {"job": "nightly"}
    perm_key = f"n8n:workflow:wf-1:{_canonical_payload_hash(payload)}"

    action = temp_approval_store.queue_action(
        action_type="n8n_execute_workflow",
        description="Nightly job",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
        ttl_hours=1,
    )
    temp_approval_store.update_status(action.id, STATUS_APPROVED)

    # Force expiration in store
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    temp_approval_store._conn.execute(
        "UPDATE pending_actions SET expires_at = ? WHERE id = ?",
        (past_iso, action.id),
    )
    temp_approval_store._conn.commit()

    res = tool.execute(
        action="execute_workflow",
        workflow_id="wf-1",
        payload=payload,
        approval_id=action.id,
    )
    assert res.success is False
    assert "expired" in res.content.lower()


def test_agent_generated_requests_cannot_approve_themselves(temp_approval_store):
    """Requirement 8: Ensure agent-generated requests cannot self-approve without human decision."""
    tool = N8nTool(approval_store=temp_approval_store)
    payload = {"_requester": "agent", "cmd": "eval"}
    perm_key = f"n8n:workflow:wf-1:{_canonical_payload_hash(payload)}"

    action = temp_approval_store.queue_action(
        action_type="n8n_execute_workflow",
        description="Agent self-trigger",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    # Manually mark as approved without human decision_at
    temp_approval_store._conn.execute(
        "UPDATE pending_actions SET status = 'approved', decision_at = NULL WHERE id = ?",
        (action.id,),
    )
    temp_approval_store._conn.commit()

    res = tool.execute(
        action="execute_workflow",
        workflow_id="wf-1",
        payload=payload,
        approval_id=action.id,
    )
    assert res.success is False
    assert "cannot approve themselves" in res.content.lower()

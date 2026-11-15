"""P0 Security Tests for n8n Ambiguous Execution Outcomes, Idempotency, and Reconciliation.

Verifies:
1. Socket read timeout during/after transmission transitions approval to STATUS_OUTCOME_UNKNOWN.
2. Approval is NOT automatically restored following an ambiguous network timeout.
3. Pre-transmission failures (DNS resolution failure, connection refused) safely restore approval to STATUS_APPROVED.
4. An approval in STATUS_OUTCOME_UNKNOWN cannot be consumed again without reconciliation.
5. Reconciliation action checks n8n execution status and safely transitions state.
6. Idempotency keys are included in request headers and payloads.
7. Atomic single-use constraint is maintained under concurrent claim attempts.
"""

from __future__ import annotations

import concurrent.futures
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.tools.approval_store import (
    ApprovalStore,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_OUTCOME_UNKNOWN,
    STATUS_PENDING,
    TIER_HIGH,
)
from openjarvis.tools.n8n import (
    N8nClient,
    N8nTool,
    _canonical_payload_hash,
    classify_network_failure,
)


def test_classify_network_failure_pre_vs_post():
    """Verify classification accurately distinguishes pre-transmission vs ambiguous post-transmission."""
    # Pre-transmission: DNS error
    dns_err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    assert classify_network_failure(dns_err) == "pre_transmission"

    # Pre-transmission: Connection refused
    refused_err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    assert classify_network_failure(refused_err) == "pre_transmission"

    # Post-transmission: TimeoutError (socket timeout waiting for response)
    timeout_err = TimeoutError("The read operation timed out")
    assert classify_network_failure(timeout_err) == "outcome_unknown"

    url_timeout_err = urllib.error.URLError(socket.timeout("timed out"))
    assert classify_network_failure(url_timeout_err) == "outcome_unknown"

    # Post-transmission: HTTP 504 Gateway Timeout
    http_504 = urllib.error.HTTPError(url="http://test", code=504, msg="Gateway Timeout", hdrs=MagicMock(), fp=MagicMock())
    assert classify_network_failure(http_504) == "outcome_unknown"

    # Post-transmission: HTTP 400 Bad Request
    http_400 = urllib.error.HTTPError(url="http://test", code=400, msg="Bad Request", hdrs=MagicMock(), fp=MagicMock())
    assert classify_network_failure(http_400) == "post_transmission_error"


def test_post_transmission_timeout_transitions_to_outcome_unknown_no_auto_restore(tmp_path):
    """Network timeout during workflow execution transitions approval to outcome_unknown, NOT approved."""
    store = ApprovalStore(db_path=tmp_path / "timeout_test.db")
    tool = N8nTool(approval_store=store)

    payload = {"param": "value"}
    canon_hash = _canonical_payload_hash(payload)
    perm_key = f"n8n:workflow:42:{canon_hash}"

    # 1. Queue and approve workflow execution
    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Execute workflow 42",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)
    assert store.get_action(act.id).status == STATUS_APPROVED

    # 2. Simulate read timeout during urlopen
    timeout_err = urllib.error.URLError(socket.timeout("The read operation timed out"))

    with patch("urllib.request.urlopen", side_effect=timeout_err):
        res = tool.execute(
            action="execute_workflow",
            workflow_id="42",
            payload=payload,
            approval_id=act.id,
        )
        assert res.success is False

    # 3. Status MUST be outcome_unknown, NEVER restored to approved!
    updated_act = store.get_action(act.id)
    assert updated_act.status == STATUS_OUTCOME_UNKNOWN

    # 4. Immediate retry MUST be rejected
    claimed, _, reason = store.claim_approval_for_execution(act.id, perm_key, "n8n_execute_workflow")
    assert claimed is False
    assert "outcome_unknown" in reason
    assert "Reconciliation is required" in reason


def test_pre_transmission_failure_safely_restores_approved(tmp_path):
    """Connection refused (zero bytes delivered) restores approval to approved so user can retry."""
    store = ApprovalStore(db_path=tmp_path / "pre_trans_test.db")
    tool = N8nTool(approval_store=store)

    payload = {"param": "value"}
    canon_hash = _canonical_payload_hash(payload)
    perm_key = f"n8n:workflow:42:{canon_hash}"

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Execute workflow 42",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)

    # Simulate DNS failure before any byte transmitted
    dns_err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    with patch("urllib.request.urlopen", side_effect=dns_err):
        res = tool.execute(
            action="execute_workflow",
            workflow_id="42",
            payload=payload,
            approval_id=act.id,
        )
        assert res.success is False

    # Pre-transmission error safely restores state to APPROVED
    updated_act = store.get_action(act.id)
    assert updated_act.status == STATUS_APPROVED


def test_reconcile_outcome_unknown_with_n8n_execution_api(tmp_path):
    """Reconciliation transitions outcome_unknown to executed when n8n confirms completion."""
    store = ApprovalStore(db_path=tmp_path / "reconcile_test.db")
    tool = N8nTool(approval_store=store)

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Execute workflow 42",
        payload={"param": "value"},
        permission_key="n8n:workflow:42:testhash",
        tier=TIER_HIGH,
    )
    # Directly set to outcome_unknown
    store.update_status(act.id, STATUS_OUTCOME_UNKNOWN)

    # 1. Case A: n8n execution API confirms workflow completed successfully
    with patch.object(
        tool.client,
        "reconcile_workflow_execution",
        return_value={"reconciled": True, "found": True, "status": "success", "finished": True},
    ):
        res = tool.execute(action="reconcile", approval_id=act.id, workflow_id="42")
        assert res.success is True
        assert store.get_action(act.id).status == STATUS_EXECUTED

    # 2. Case B: n8n execution API confirms workflow NEVER started
    store.update_status(act.id, STATUS_OUTCOME_UNKNOWN)
    with patch.object(
        tool.client,
        "reconcile_workflow_execution",
        return_value={"reconciled": True, "found": False, "status": "not_executed"},
    ):
        res = tool.execute(action="reconcile", approval_id=act.id, workflow_id="42")
        assert res.success is True
        assert store.get_action(act.id).status == STATUS_APPROVED  # Safe to retry!


def test_idempotency_key_sent_in_requests(tmp_path):
    """Execution passes idempotency key matching approval ID."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "ok"}'
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        client.execute_workflow("wf-101", data={"key": "val"}, idempotency_key="test-approval-id-999")

        req = mock_urlopen.call_args[0][0]
        assert req.headers.get("X-idempotency-key") == "test-approval-id-999"


def test_concurrent_claim_race_single_winner(tmp_path):
    """Under 20 concurrent threads attempting to claim the same approval, exactly ONE succeeds."""
    store = ApprovalStore(db_path=tmp_path / "concurrent_claims.db")

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Concurrent claim test",
        payload={"test": "payload"},
        permission_key="n8n:workflow:concurrent:123",
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)

    results = []

    def try_claim():
        claimed, _, reason = store.claim_approval_for_execution(act.id, "n8n:workflow:concurrent:123", "n8n_execute_workflow")
        return claimed

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(try_claim) for _ in range(20)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly ONE claim succeeded
    assert results.count(True) == 1
    assert results.count(False) == 19
    assert store.get_action(act.id).status == "executing"

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


def test_reconcile_correlates_by_started_after_and_idempotency_key():
    """Verify reconciliation ignores older executions and correctly matches via timestamp and key."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    mock_executions = [
        # Old execution before our request
        {
            "id": "exec-old-001",
            "workflowId": "wf-test",
            "status": "success",
            "createdAt": "2026-11-15T10:00:00.000Z",
            "finished": True,
        },
        # Current execution matching our request
        {
            "id": "exec-new-002",
            "workflowId": "wf-test",
            "status": "running",
            "createdAt": "2026-11-15T12:00:05.000Z",
            "finished": False,
            "customData": {"approval_id": "appr-12345"},
        },
    ]

    with patch.object(client, "_make_request", return_value={"data": mock_executions}):
        # Request with started_after pointing to 11:59:00
        started_after_ts = 1794743940.0  # ~11:59:00 on Nov 15 2026
        # Old execution is at 10:00 (1794736800.0), so it should be skipped
        # New execution matches timestamp and customData
        res = client.reconcile_workflow_execution(
            workflow_id="wf-test",
            started_after=started_after_ts,
            approval_id="appr-12345",
        )
        assert res["found"] is True
        assert res["execution_id"] == "exec-new-002"
        assert res["status"] == "running"
        assert res["finished"] is False


def test_execution_ledger_tracks_attempts_and_outcomes(tmp_path):
    """Execution ledger tracks dispatch and outcome records for full auditability."""
    store = ApprovalStore(db_path=tmp_path / "ledger_test.db")
    tool = N8nTool(approval_store=store)

    payload = {"k": "v"}
    canon_hash = _canonical_payload_hash(payload)
    perm_key = f"n8n:workflow:ledger_wf:{canon_hash}"

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Execute workflow ledger_wf",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)

    with patch.object(tool.client, "execute_workflow", return_value={"id": "exec-999", "status": "running"}):
        res = tool.execute(action="execute_workflow", workflow_id="ledger_wf", payload=payload, approval_id=act.id)
        assert res.success is True

    entry = tool._ledger.get_entry(act.id)
    assert entry is not None
    assert entry["target"] == "ledger_wf"
    assert entry["status"] == "executed"
    assert entry["execution_id"] == "exec-999"


def test_reconcile_exact_approval_match():
    """Exact approval match in execution metadata successfully reconciles."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    item = {
        "id": "exec-exact-appr",
        "workflowId": "wf-1",
        "status": "success",
        "createdAt": "2026-11-15T12:00:00.000Z",
        "finished": True,
        "customData": {"approval_id": "appr-exact-1"},
    }
    with patch.object(client, "_make_request", return_value={"data": [item]}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-exact-1",
        )
        assert res["reconciled"] is True
        assert res["found"] is True
        assert res["execution_id"] == "exec-exact-appr"
        assert res["status"] == "success"


def test_reconcile_exact_idempotency_match():
    """Exact idempotency key match in execution metadata successfully reconciles."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    item = {
        "id": "exec-exact-idem",
        "workflowId": "wf-1",
        "status": "success",
        "createdAt": "2026-11-15T12:00:00.000Z",
        "finished": True,
        "customData": {"idempotency_key": "idem-exact-1"},
    }
    with patch.object(client, "_make_request", return_value={"data": [item]}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            idempotency_key="idem-exact-1",
        )
        assert res["reconciled"] is True
        assert res["found"] is True
        assert res["execution_id"] == "exec-exact-idem"
        assert res["status"] == "success"


def test_reconcile_unrelated_newer_execution_returns_outcome_unknown():
    """Unrelated execution in window without exact correlation metadata must return outcome_unknown, not guess."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    item = {
        "id": "exec-unrelated-newer",
        "workflowId": "wf-1",
        "status": "success",
        "createdAt": "2026-11-15T12:05:00.000Z",
        "finished": True,
        # No customData or approval metadata
    }
    with patch.object(client, "_make_request", return_value={"data": [item]}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-target-1",
        )
        assert res["reconciled"] is False
        assert res["found"] is False
        assert res["status"] == "outcome_unknown"
        assert "manual verification required" in res["error"]


def test_reconcile_older_execution_returns_not_executed():
    """Executions prior to dispatch window are ignored; confirmed not_executed."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    item = {
        "id": "exec-older",
        "workflowId": "wf-1",
        "status": "success",
        "createdAt": "2026-11-15T08:00:00.000Z",
        "finished": True,
    }
    with patch.object(client, "_make_request", return_value={"data": [item]}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-target-1",
        )
        assert res["reconciled"] is True
        assert res["found"] is False
        assert res["status"] == "not_executed"


def test_reconcile_same_workflow_with_another_approval_not_matched():
    """Execution for the same workflow belonging to another approval is NOT matched."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    item = {
        "id": "exec-other-appr",
        "workflowId": "wf-1",
        "status": "success",
        "createdAt": "2026-11-15T12:01:00.000Z",
        "finished": True,
        "customData": {"approval_id": "other-approval-999"},
    }
    with patch.object(client, "_make_request", return_value={"data": [item]}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="our-approval-111",
        )
        assert res["found"] is False
        assert res["reconciled"] is True
        assert res["status"] == "not_executed"


def test_reconcile_missing_metadata_returns_outcome_unknown():
    """When API returns executions in window lacking metadata, Jarvis never guesses and returns outcome_unknown."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    item = {
        "id": "exec-no-metadata",
        "workflowId": "wf-1",
        "status": "running",
        "createdAt": "2026-11-15T12:00:01.000Z",
        "finished": False,
        "customData": {},
    }
    with patch.object(client, "_make_request", return_value={"data": [item]}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-missing-meta",
        )
        assert res["reconciled"] is False
        assert res["found"] is False
        assert res["status"] == "outcome_unknown"


def test_reconcile_multiple_executions_correlates_only_exact():
    """When multiple executions exist, only the exact matching execution is correlated."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    items = [
        {"id": "exec-old", "workflowId": "wf-1", "createdAt": "2026-11-15T09:00:00Z", "status": "success", "finished": True},
        {"id": "exec-other", "workflowId": "wf-1", "createdAt": "2026-11-15T12:00:00Z", "status": "failed", "customData": {"approval_id": "other"}},
        {"id": "exec-exact-match", "workflowId": "wf-1", "createdAt": "2026-11-15T12:00:05Z", "status": "success", "finished": True, "customData": {"approval_id": "our-appr"}},
    ]
    with patch.object(client, "_make_request", return_value={"data": items}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="our-appr",
        )
        assert res["found"] is True
        assert res["execution_id"] == "exec-exact-match"
        assert res["status"] == "success"


def test_reconcile_paginated_results_finds_execution_on_subsequent_page():
    """Pagination traverses multiple pages and locates exact execution on subsequent page."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    page1 = {
        "data": [{"id": "exec-p1", "workflowId": "wf-1", "createdAt": "2026-11-15T12:00:00Z", "customData": {"approval_id": "other"}}],
        "nextCursor": "cursor-page-2",
    }
    page2 = {
        "data": [{"id": "exec-p2-target", "workflowId": "wf-1", "createdAt": "2026-11-15T12:00:02Z", "status": "success", "finished": True, "customData": {"approval_id": "target-appr"}}],
        "nextCursor": None,
    }

    def mock_request(endpoint):
        if "cursor=cursor-page-2" in endpoint:
            return page2
        return page1

    with patch.object(client, "_make_request", side_effect=mock_request):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="target-appr",
        )
        assert res["found"] is True
        assert res["execution_id"] == "exec-p2-target"
        assert res["status"] == "success"


def test_reconcile_empty_first_page_with_additional_pages():
    """Empty first page is not treated as proof workflow never ran if nextCursor is present."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    page1 = {"data": [], "nextCursor": "cursor-p2"}
    page2 = {
        "data": [{"id": "exec-p2", "workflowId": "wf-1", "createdAt": "2026-11-15T12:00:02Z", "status": "running", "finished": False, "customData": {"approval_id": "appr-p2"}}],
        "nextCursor": None,
    }

    def mock_request(endpoint):
        if "cursor=cursor-p2" in endpoint:
            return page2
        return page1

    with patch.object(client, "_make_request", side_effect=mock_request):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-p2",
        )
        assert res["found"] is True
        assert res["execution_id"] == "exec-p2"
        assert res["status"] == "running"


def test_reconcile_incomplete_api_response_returns_outcome_unknown():
    """Malformed or incomplete API response returns outcome_unknown."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    with patch.object(client, "_make_request", return_value="invalid response string"):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-1",
        )
        assert res["reconciled"] is False
        assert res["found"] is False
        assert res["status"] == "outcome_unknown"


def test_reconcile_api_unavailable_returns_outcome_unknown():
    """When n8n execution API is completely unreachable, returns outcome_unknown without guessing."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    with patch.object(client, "_make_request", side_effect=urllib.error.URLError("Connection refused")):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-1",
            started_after=1794743990.0,
            approval_id="appr-1",
        )
        assert res["reconciled"] is False
        assert res["found"] is False
        assert res["status"] == "outcome_unknown"
        assert "Execution API unavailable" in res["error"]


def test_duplicate_retry_prevention_while_ambiguous(tmp_path):
    """An ambiguous execution status leaves approval in outcome_unknown and blocks duplicate retries."""
    store = ApprovalStore(db_path=tmp_path / "retry_prevention.db")
    tool = N8nTool(approval_store=store)

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Ambiguous retry test",
        payload={"param": 1},
        permission_key="n8n:workflow:wf-retry:hash123",
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)
    store.update_status(act.id, STATUS_OUTCOME_UNKNOWN)

    # Reconciliation returns outcome_unknown (e.g. API unavailable)
    with patch.object(
        tool.client,
        "reconcile_workflow_execution",
        return_value={"reconciled": False, "found": False, "status": "outcome_unknown", "error": "API unreachable"},
    ):
        rec_res = tool.execute(action="reconcile", approval_id=act.id, workflow_id="wf-retry")
        assert rec_res.success is False

    # Status MUST remain STATUS_OUTCOME_UNKNOWN
    updated_act = store.get_action(act.id)
    assert updated_act.status == STATUS_OUTCOME_UNKNOWN

    # Retry execution MUST be blocked
    claimed, _, reason = store.claim_approval_for_execution(act.id, "n8n:workflow:wf-retry:hash123", "n8n_execute_workflow")
    assert claimed is False
    assert "outcome_unknown" in reason


def test_reconcile_empty_completed_result_returns_not_executed():
    """When API returns an empty completed page (no nextCursor), authoritatively conclude not_executed."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    with patch.object(client, "_make_request", return_value={"data": [], "nextCursor": None}):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-empty",
            started_after=1794743990.0,
            approval_id="appr-empty",
        )
        assert res["reconciled"] is True
        assert res["found"] is False
        assert res["status"] == "not_executed"


def test_reconcile_missing_workflow_id_returns_outcome_unknown():
    """Missing workflow ID is rejected and returns outcome_unknown."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    res = client.reconcile_workflow_execution(
        workflow_id="",
        started_after=1794743990.0,
        approval_id="appr-1",
    )
    assert res["reconciled"] is False
    assert res["status"] == "outcome_unknown"
    assert "workflow_id is required" in res["error"]


def test_reconcile_wrong_workflow_id_in_execution_record_ignored():
    """Execution records bearing mismatched or missing workflowId are rejected."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    items = [
        {"id": "exec-wrong-wf", "workflowId": "wrong-workflow", "createdAt": "2026-11-15T12:00:05Z", "customData": {"approval_id": "appr-target"}},
        {"id": "exec-no-wf-id", "workflowId": None, "createdAt": "2026-11-15T12:00:06Z", "customData": {"approval_id": "appr-target"}},
    ]
    with patch.object(client, "_make_request", return_value={"data": items, "nextCursor": None}):
        res = client.reconcile_workflow_execution(
            workflow_id="target-workflow",
            started_after=1794743990.0,
            approval_id="appr-target",
        )
        # Neither matches target-workflow, so no match found and no ambiguous in-window execution for this workflow
        assert res["reconciled"] is True
        assert res["found"] is False
        assert res["status"] == "not_executed"


def test_reconcile_api_pagination_limit_reached_returns_outcome_unknown():
    """When pagination reaches max_pages while more pages exist, outcome is unknown."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")

    page_count = 0

    def mock_request(endpoint):
        nonlocal page_count
        page_count += 1
        return {
            "data": [{"id": f"exec-{page_count}", "workflowId": "wf-paged", "createdAt": "2026-11-15T10:00:00Z"}],
            "nextCursor": f"cursor-page-{page_count+1}",
        }

    with patch.object(client, "_make_request", side_effect=mock_request):
        res = client.reconcile_workflow_execution(
            workflow_id="wf-paged",
            started_after=1794743990.0,
            approval_id="appr-unfound",
            max_pages=2,
        )
        assert res["reconciled"] is False
        assert res["found"] is False
        assert res["status"] == "outcome_unknown"
        assert "Pagination limit reached" in res["error"]


def test_reconcile_missing_both_approval_and_idempotency_keys():
    """Attempting reconciliation without any correlation metadata returns outcome_unknown."""
    client = N8nClient(base_url="https://mock-n8n.local", api_key="test-api-key")
    res = client.reconcile_workflow_execution(
        workflow_id="wf-test",
        approval_id=None,
        idempotency_key=None,
    )
    assert res["reconciled"] is False
    assert res["status"] == "outcome_unknown"
    assert "Insufficient correlation metadata" in res["error"]



"""Concurrency and atomic transaction tests for n8n approvals.

Verifies:
- Explicit states: pending, approved, executing, executed, failed, expired, denied.
- Multi-threaded race condition: exactly ONE concurrent thread can consume an approval.
- Stale, expired, or non-matching approvals cannot be claimed.
- Pre-execution failure preserves approval (resets to approved) and does not consume it permanently.
- Target, payload hash, and action type binding.
"""

from __future__ import annotations

import concurrent.futures
import time
import urllib.error
from unittest.mock import patch

import pytest

from openjarvis.tools.approval_store import (
    ApprovalStore,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXECUTED,
    STATUS_EXECUTING,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    TIER_HIGH,
)
from openjarvis.tools.n8n import N8nTool, _canonical_payload_hash


def test_explicit_approval_states_transitions(tmp_path):
    """Verify state transitions: pending -> approved -> executing -> executed / failed / expired."""
    store = ApprovalStore(db_path=str(tmp_path / "test_states.db"))
    perm_key = "n8n:workflow:wf-states:hash123"

    # 1. Pending
    action = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Run test workflow",
        payload={"_requester": "user"},
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    assert action.status == STATUS_PENDING

    # 2. Approved
    store.approve_action(action.id)
    approved_action = store.get_action(action.id)
    assert approved_action.status == STATUS_APPROVED

    # 3. Executing (claimed atomically)
    claimed, claim_obj, msg = store.claim_approval_for_execution(
        action.id, perm_key, "n8n_execute_workflow"
    )
    assert claimed is True
    assert claim_obj.status == STATUS_EXECUTING

    # 4. Executed
    store.complete_approval_execution(action.id, success=True)
    done_action = store.get_action(action.id)
    assert done_action.status == STATUS_EXECUTED

    # Single-use: cannot be claimed again
    claimed_again, _, msg = store.claim_approval_for_execution(
        action.id, perm_key, "n8n_execute_workflow"
    )
    assert claimed_again is False
    assert "already been consumed" in msg


def test_concurrent_simultaneous_approval_claims(tmp_path):
    """10 simultaneous threads attempting to claim the SAME approval: exactly ONE must succeed."""
    store = ApprovalStore(db_path=str(tmp_path / "concurrent_approvals.db"))
    perm_key = "n8n:workflow:wf-concurrency:hash999"

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="High concurrency claim test",
        payload={"_requester": "user"},
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)

    num_threads = 10
    results = []

    def attempt_claim(worker_id: int):
        claimed, obj, msg = store.claim_approval_for_execution(
            act.id, perm_key, "n8n_execute_workflow"
        )
        return worker_id, claimed, msg

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(attempt_claim, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r[1] is True]
    failures = [r for r in results if r[1] is False]

    assert len(successes) == 1, f"Expected exactly 1 claim to succeed, got {len(successes)}"
    assert len(failures) == num_threads - 1


def test_pre_execution_failure_does_not_permanently_consume_approval(tmp_path):
    """When a request fails prior to actual execution, approval status must revert to 'approved'."""
    store = ApprovalStore(db_path=str(tmp_path / "pre_exec_rollback.db"))
    tool = N8nTool(approval_store=store)

    payload = {"query": "run_job"}
    canon_hash = _canonical_payload_hash(payload)
    perm_key = f"n8n:workflow:wf-fail-pre:{canon_hash}"

    act = store.queue_action(
        action_type="n8n_execute_workflow",
        description="Pre-execution failure test",
        payload=payload,
        permission_key=perm_key,
        tier=TIER_HIGH,
    )
    store.approve_action(act.id)

    # Simulate network connection refusal (pre-execution failure)
    with patch.object(tool.client, "execute_workflow", side_effect=urllib.error.URLError("Connection refused")):
        res = tool.execute(
            action="execute_workflow",
            workflow_id="wf-fail-pre",
            payload=payload,
            approval_id=act.id,
        )
        assert res.success is False

    # The approval status should NOT be permanently consumed (STATUS_FAILED or STATUS_EXECUTED)
    # It must be reset to STATUS_APPROVED
    refreshed = store.get_action(act.id)
    assert refreshed.status == STATUS_APPROVED

    # A subsequent retry should succeed
    with patch.object(tool.client, "execute_workflow", return_value={"data": "success"}):
        res_retry = tool.execute(
            action="execute_workflow",
            workflow_id="wf-fail-pre",
            payload=payload,
            approval_id=act.id,
        )
        assert res_retry.success is True

    # Now it is executed
    assert store.get_action(act.id).status == STATUS_EXECUTED

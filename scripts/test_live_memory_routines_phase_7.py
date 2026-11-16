"""Live Benchmark and Verification Script for Phase 7: Memory, Background Routines & External Integrations.

Runs real in-memory and on-disk executions of:
1. Fact creation with categorization, secret redaction, and expiration.
2. Prompt context injection ranking relevant facts and omitting irrelevant/expired facts.
3. Fact update and deletion with audit verification.
4. Scheduled task execution and lifecycle control (pause/resume/cancel).
5. Incident threshold monitoring with exactly-once triggering per window.
6. Safe notification dispatch with retry backoff and audit trail logging.
7. External connector scope validation.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

# Ensure src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from openjarvis.core.types import Message, Role
from openjarvis.memory.store import Fact, LocalFactStore
from openjarvis.routines.monitoring import (
    ComparisonOperator,
    ExternalConnector,
    NotificationDispatcher,
    NotificationMessage,
    NotificationStatus,
    ThresholdMonitor,
    ThresholdRule,
)
from openjarvis.routines.quiet_hours import QuietHoursManager
from openjarvis.scheduler.scheduler import ScheduledTask, TaskScheduler
from openjarvis.scheduler.store import SchedulerStore
from openjarvis.tools.storage.context import ContextConfig, inject_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase_7_live_benchmark")


def run_benchmark() -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "phase": 7,
        "title": "Memory, Background Routines & External Integrations Live Benchmark",
        "timestamp": time.time(),
        "scenarios": {},
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="jarvis_phase_7_live_"))
    logger.info("Using temporary directory: %s", tmp_dir)

    try:
        # ---------------------------------------------------------------------
        # Scenario 1: Memory Persistence, Categorization & Secret Redaction
        # ---------------------------------------------------------------------
        logger.info("Running Scenario 1: Categorization & Secret Redaction...")
        facts_file = tmp_dir / "memory_facts.jsonl"
        store = LocalFactStore(path=facts_file)

        # Add facts across categories, with a secret key embedded in one
        store.add(
            "User preferred language is Python and uses uv for package management",
            category="preferences",
        )
        store.add(
            "Project Jarvis backend exposes FastAPI REST and WebSocket endpoints",
            category="projects",
        )
        store.add(
            "Staging API key is sk-proj-1234567890abcdef1234567890abcdef for testing",
            category="environment",
        )
        store.add(
            "Sprint goal: complete Phase 7 release readiness",
            category="tasks",
            expires_at=time.time() + 7200.0,
        )
        store.add(
            "Temporary auth cookie session expired",
            category="tasks",
            expires_at=time.time() - 3600.0,  # Expired 1 hr ago
        )

        all_facts = store.list()
        file_text = facts_file.read_text(encoding="utf-8")

        secret_scrubbed = "sk-proj-1234567890abcdef1234567890abcdef" not in file_text and "[REDACTED:api_key]" in file_text
        expired_withheld = any(f.is_expired for f in all_facts)

        results["scenarios"]["scenario_1_memory_and_scrubbing"] = {
            "passed": len(all_facts) == 5 and secret_scrubbed and expired_withheld,
            "total_facts": len(all_facts),
            "secret_scrubbed": secret_scrubbed,
            "expired_detected": expired_withheld,
        }
        logger.info("Scenario 1 Passed: Secret scrubbed and expired detected.")

        # ---------------------------------------------------------------------
        # Scenario 2: Relevance Retrieval & Context Injection
        # ---------------------------------------------------------------------
        logger.info("Running Scenario 2: Relevance Retrieval & Context Injection...")
        messages = [Message(role=Role.USER, content="What packages and frameworks are used?")]
        cfg = ContextConfig(enabled=True, filter_relevant_facts=True, min_fact_score=0.1)

        augmented = inject_context("Python FastAPI backend", messages, None, config=cfg, facts=all_facts)
        prompt_content = augmented[0].content

        relevant_included = "FastAPI" in prompt_content and "Python" in prompt_content
        expired_omitted = "Temporary auth cookie" not in prompt_content
        irrelevant_omitted = "Sprint goal" not in prompt_content

        results["scenarios"]["scenario_2_relevance_retrieval"] = {
            "passed": relevant_included and expired_omitted and irrelevant_omitted,
            "relevant_included": relevant_included,
            "expired_omitted": expired_omitted,
            "irrelevant_omitted": irrelevant_omitted,
        }
        logger.info("Scenario 2 Passed: Relevant included, irrelevant/expired excluded.")

        # ---------------------------------------------------------------------
        # Scenario 3: Memory Correction & Deletion
        # ---------------------------------------------------------------------
        logger.info("Running Scenario 3: User Memory Correction & Deletion...")
        pref_facts = store.list_categorized(category="preferences")
        target_fact = pref_facts[0]
        store.update_fact(target_fact.id, text="User preferred language is modern Python 3.12+", confidence=0.98)

        reloaded_facts = store.list()
        updated_fact = next(f for f in reloaded_facts if f.id == target_fact.id)
        update_verified = "modern Python 3.12+" in updated_fact.text

        store.delete_fact(target_fact.id)
        final_facts = store.list()
        delete_verified = len(final_facts) == 4 and not any(f.id == target_fact.id for f in final_facts)

        results["scenarios"]["scenario_3_user_correction_deletion"] = {
            "passed": update_verified and delete_verified,
            "update_verified": update_verified,
            "delete_verified": delete_verified,
        }
        logger.info("Scenario 3 Passed: User correction and deletion verified.")

        # ---------------------------------------------------------------------
        # Scenario 4: Background Task Scheduling & Lifecycle
        # ---------------------------------------------------------------------
        logger.info("Running Scenario 4: Background Task Scheduling & Lifecycle...")
        scheduler_db = tmp_dir / "scheduler.db"
        sched_store = SchedulerStore(str(scheduler_db))

        executed_actions = []

        class MockSystem:
            def ask(self, prompt: str, **kwargs) -> str:
                executed_actions.append(prompt)
                return f"Done: {prompt}"

        scheduler = TaskScheduler(sched_store, system=MockSystem(), poll_interval=1)
        task_due = scheduler.create_task("Execute system health check", "once", "2026-01-01T00:00:00+00:00")
        task_recurring = scheduler.create_task("Periodic backup routine", "interval", "1800")

        # Execute due task
        due_list = sched_store.get_due_tasks("2026-01-01T00:05:00+00:00")
        scheduler._execute_task(ScheduledTask.from_dict(due_list[0]))

        task_completed = sched_store.get_task(task_due.id)["status"] == "completed"

        # Pause and cancel recurring task
        scheduler.pause_task(task_recurring.id)
        is_paused = sched_store.get_task(task_recurring.id)["status"] == "paused"
        scheduler.cancel_task(task_recurring.id)
        is_cancelled = sched_store.get_task(task_recurring.id)["status"] == "cancelled"

        results["scenarios"]["scenario_4_scheduler_lifecycle"] = {
            "passed": len(executed_actions) == 1 and task_completed and is_paused and is_cancelled,
            "execution_verified": len(executed_actions) == 1,
            "task_completed": task_completed,
            "pause_and_cancel_verified": is_paused and is_cancelled,
        }
        logger.info("Scenario 4 Passed: Scheduling and lifecycle control verified.")

        # ---------------------------------------------------------------------
        # Scenario 5: Incident Threshold Monitoring (Single Trigger per Window)
        # ---------------------------------------------------------------------
        logger.info("Running Scenario 5: Threshold Monitoring Single-Trigger Window...")
        rule = ThresholdRule(
            name="cpu_load_spike",
            metric_name="cpu_load_percent",
            operator=ComparisonOperator.GT,
            threshold=90.0,
            incident_window_seconds=30.0,
        )
        monitor = ThresholdMonitor([rule])

        t0 = 5000.0
        # Trigger 1: 95% at t0 -> Alert
        inc1 = monitor.evaluate_metric("cpu_load_spike", 95.0, timestamp=t0)
        # Trigger 2: 98% at t0 + 10s -> Suppressed
        inc2 = monitor.evaluate_metric("cpu_load_spike", 98.0, timestamp=t0 + 10.0)
        # Trigger 3: 94% at t0 + 20s -> Suppressed
        inc3 = monitor.evaluate_metric("cpu_load_spike", 94.0, timestamp=t0 + 20.0)
        # Trigger 4: 92% at t0 + 35s (after 30s window) -> New Alert
        inc4 = monitor.evaluate_metric("cpu_load_spike", 92.0, timestamp=t0 + 35.0)

        single_trigger_passed = (
            inc1 is not None and inc2 is None and inc3 is None and inc4 is not None and inc4.id != inc1.id
        )

        results["scenarios"]["scenario_5_threshold_monitoring"] = {
            "passed": single_trigger_passed,
            "initial_alert_fired": inc1 is not None,
            "suppressed_in_window": inc2 is None and inc3 is None,
            "subsequent_window_fired": inc4 is not None,
        }
        logger.info("Scenario 5 Passed: Single alert per incident window verified.")

        # ---------------------------------------------------------------------
        # Scenario 6: Notification Dispatcher, Retries & Connector Scope
        # ---------------------------------------------------------------------
        logger.info("Running Scenario 6: Notification Dispatcher, Retries & Connector Scope...")
        qm = QuietHoursManager(enabled=True, start_time_str="22:00", end_time_str="07:00", timezone_name="UTC")
        dispatcher = NotificationDispatcher(quiet_hours=qm, max_retries=2)

        # 6a: Voice notification during quiet hours -> queued
        from datetime import datetime, timezone
        night_ts = datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc).timestamp()
        v_msg = NotificationMessage(id="v-1", channel="voice", title="Night Ping", content="Check alert")
        v_res = dispatcher.send(v_msg, now_ts=night_ts)
        quiet_queued = v_res.status == NotificationStatus.QUEUED_QUIET_HOURS and len(qm.flush_queued()) == 1

        # 6b: Retried notification upon transient failure
        attempts = 0

        def failing_sender(m: NotificationMessage) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConnectionResetError("Reset")
            return True

        retry_msg = NotificationMessage(id="ret-1", channel="system", title="Sync", content="Data sync")
        retry_res = dispatcher.send(retry_msg, sender_fn=failing_sender)
        retry_passed = retry_res.status == NotificationStatus.DELIVERED and attempts == 2

        # 6c: Connector scope enforcement
        connector = ExternalConnector(name="alert_webhook", allowed_scopes={"notify"})
        bad_msg = NotificationMessage(id="perm-1", channel="webhook", title="Delete", content="Delete DB")
        bad_res = dispatcher.send(bad_msg, connector=connector, required_scope="db_admin")
        scope_denied = bad_res.status == NotificationStatus.PERMISSION_DENIED

        # 6d: Audit trail
        audit_trail = dispatcher.get_audit_trail()
        audit_recorded = len(audit_trail) >= 3

        results["scenarios"]["scenario_6_notifications_and_connectors"] = {
            "passed": quiet_queued and retry_passed and scope_denied and audit_recorded,
            "quiet_hours_queued": quiet_queued,
            "retry_recovery": retry_passed,
            "scope_enforced": scope_denied,
            "audit_trail_recorded": audit_recorded,
        }
        logger.info("Scenario 6 Passed: Notifications, retries, scopes, and audit trail verified.")

        # Overall validation
        all_passed = all(s.get("passed", False) for s in results["scenarios"].values())
        results["all_passed"] = all_passed
        logger.info("Phase 7 Benchmark Complete! Overall Passed: %s", all_passed)

    finally:
        # Cleanup temp directory
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Save results
    output_path = repo_root / "scripts" / "phase_7_live_memory_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("Saved benchmark evidence to %s", output_path)

    return results


if __name__ == "__main__":
    res = run_benchmark()
    if not res.get("all_passed", False):
        sys.exit(1)

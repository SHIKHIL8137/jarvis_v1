"""
Live Test for Phase 4: Durable Autonomy and Restart Recovery.

Executes:
1. Multi-step task spanning multiple operations with persistent state.
2. Step 1 executes and establishes a persistent checkpoint in SQLite.
3. Simulates an abrupt backend crash/shutdown while task is running.
4. Initializes a completely new BackgroundTaskEngine instance pointing to the same SQLite database.
5. Calls recover_on_startup() to resume execution from verified checkpoint.
6. Verifies Step 1 is not replayed blindly (idempotency preserved).
7. Verifies Task completes with status COMPLETED and full audit log.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tasks.engine import BackgroundTaskEngine
from openjarvis.tasks.models import (
    BackgroundTask,
    StepStatus,
    TaskBudget,
    TaskCheckpoint,
    TaskStatus,
    TaskStep,
)
from openjarvis.tasks.store import TaskStore
from openjarvis.tools._stubs import BaseTool


# Step tracking to verify idempotency (ensures step 1 is executed only once)
STEP_EXECUTION_COUNTS = {"step-1": 0, "step-2": 0, "step-3": 0}


@ToolRegistry.register("live_step_recorder")
class LiveStepRecorderTool(BaseTool):
    tool_id = "live_step_recorder"

    @property
    def spec(self):
        from openjarvis.tools._stubs import ToolSpec

        return ToolSpec(
            name="live_step_recorder",
            description="records step execution",
            parameters={"type": "object"},
        )

    def execute(self, **params):
        step_id = params.get("step_id", "unknown")
        STEP_EXECUTION_COUNTS[step_id] = (
            STEP_EXECUTION_COUNTS.get(step_id, 0) + 1
        )
        return ToolResult(
            tool_name="live_step_recorder",
            success=True,
            content=f"Executed {step_id} (count={STEP_EXECUTION_COUNTS[step_id]})",
        )


def run_live_recovery_test():
    print("=== Phase 4 Live Task Execution & Crash Recovery Test ===")

    # 1. Setup persistent database
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-task-live-")).resolve()
    db_path = temp_dir / "tasks_live.db"
    print(f"Persistent database at: {db_path}")

    try:
        store1 = TaskStore(db_path=db_path)
        engine1 = BackgroundTaskEngine(store=store1)

        task = engine1.create_task(
            title="Durable Multi-Step Pipeline",
            user_request="Perform multi-stage computation with crash tolerance",
            agent_profile="general",
            budget=TaskBudget(max_execution_time_seconds=60.0),
        )

        steps = [
            TaskStep(
                step_id="step-1",
                step_number=1,
                title="Stage 1: Extract and Initialize",
                status=StepStatus.PENDING,
                tool_name="live_step_recorder",
                tool_params={"step_id": "step-1"},
                idempotency_key=f"{task.id}:step1:extract",
                assigned_agent="",
            ),
            TaskStep(
                step_id="step-2",
                step_number=2,
                title="Stage 2: Process and Transform",
                status=StepStatus.PENDING,
                tool_name="live_step_recorder",
                tool_params={"step_id": "step-2"},
                idempotency_key=f"{task.id}:step2:transform",
                assigned_agent="",
            ),
            TaskStep(
                step_id="step-3",
                step_number=3,
                title="Stage 3: Verify and Publish",
                status=StepStatus.PENDING,
                tool_name="live_step_recorder",
                tool_params={"step_id": "step-3"},
                idempotency_key=f"{task.id}:step3:publish",
                assigned_agent="",
            ),
        ]

        # 2. Simulate Step 1 completing before abrupt crash
        print("\n--- Executing Stage 1 ---")
        steps[0].status = StepStatus.COMPLETED
        steps[0].completed_at = time.time()
        STEP_EXECUTION_COUNTS["step-1"] += 1
        task.plan = steps
        task.current_step_index = 1
        task.status = TaskStatus.RUNNING
        task.context["stage1_output"] = "stage_1_verified_checksum_abc123"
        store1.save_task(task)

        # Checkpoint Stage 1
        cp = TaskCheckpoint(
            checkpoint_id=f"cp-{task.id}-step1",
            task_id=task.id,
            step_index=1,
            plan=[s.to_dict() for s in steps],
            completed_steps=[steps[0].to_dict()],
            pending_steps=[s.to_dict() for s in steps[1:]],
            context=task.context,
        )
        store1.save_checkpoint(cp)
        print("Stage 1 complete and checkpoint saved to SQLite.")

        # 3. Simulate Backend Crash / Process Shutdown
        print(
            "\n--- Simulating Abrupt Backend Shutdown (destroying engine 1) ---"
        )
        del engine1
        del store1

        # 4. Simulate Backend Startup & Recovery
        print("\n--- Simulating Backend Startup (Engine 2) ---")
        store2 = TaskStore(db_path=db_path)
        engine2 = BackgroundTaskEngine(store=store2)

        interrupted = store2.get_interrupted_tasks()
        print(f"Discovered {len(interrupted)} interrupted tasks in database.")
        assert len(interrupted) == 1
        assert interrupted[0].id == task.id

        recovered = engine2.recover_on_startup()
        print(f"Recovered {recovered} tasks from last verified checkpoint.")
        assert recovered == 1

        # Wait for resumed background worker to complete remaining steps
        print("Waiting for resumed background task to complete...")
        time.sleep(2.0)
        worker = engine2._active_workers.get(task.id)
        if worker and worker.is_alive():
            worker.join(timeout=10.0)

        # 5. Verify Results
        final_task = store2.get_task(task.id)
        print(f"\nFinal Task Status: {final_task.status.value}")
        print(f"Completed Steps: {final_task.current_step_index}/3")
        print(f"Execution counts: {STEP_EXECUTION_COUNTS}")

        assert final_task.status == TaskStatus.COMPLETED
        assert final_task.current_step_index == 3
        # Step 1 was executed exactly once (never replayed blindly)
        assert STEP_EXECUTION_COUNTS["step-1"] == 1
        # Steps 2 and 3 executed during recovery
        assert STEP_EXECUTION_COUNTS["step-2"] == 1
        assert STEP_EXECUTION_COUNTS["step-3"] == 1

        # Audit events check
        events = store2.get_events(task.id)
        event_types = [e["event_type"] for e in events]
        print(f"Logged Audit Events: {event_types}")
        assert "task_recovered_on_startup" in event_types

        results = {
            "test_status": "SUCCESS",
            "task_id": task.id,
            "resumed_from_step": 1,
            "final_status": final_task.status.value,
            "idempotency_preserved": True,
            "step_counts": STEP_EXECUTION_COUNTS,
            "audit_events": event_types,
        }
        res_file = Path(__file__).parent / "phase_4_live_recovery_results.json"
        with open(res_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print("\n[PASSED] Phase 4 Live Task Recovery Test passed successfully!")
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_live_recovery_test()

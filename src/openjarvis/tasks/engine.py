"""Autonomous Background Task Engine with lifecycle state machine, step checkpointing,
crash recovery, task steering, execution budgets, and capability profiles."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from openjarvis.core.events import EventBus, EventType
from openjarvis.tasks.models import (
    BackgroundTask,
    StepStatus,
    TaskBudget,
    TaskCheckpoint,
    TaskStatus,
    TaskStep,
)
from openjarvis.tasks.store import TaskStore

logger = logging.getLogger(__name__)

# Capability profiles for agent isolation
CAPABILITY_PROFILES: Dict[str, Dict[str, Any]] = {
    "research": {
        "allowed_tools": {
            "web_search",
            "knowledge_search",
            "knowledge_sql",
            "scan_chunks",
            "retrieval",
            "think",
            "calculator",
            "memory_manage",
            "file_read",
        },
        "denied_tools": {
            "shell_exec",
            "file_write",
            "desktop",
            "git_tool",
            "channel_send",
            "db_query",
        },
    },
    "coding": {
        "allowed_tools": {
            "file_read",
            "file_write",
            "git_tool",
            "think",
            "knowledge_search",
            "shell_exec",
        },
        "denied_tools": {
            "desktop",
            "channel_send",
        },
    },
    "monitor": {
        "allowed_tools": {
            "think",
            "knowledge_search",
            "knowledge_sql",
            "file_read",
        },
        "denied_tools": {
            "shell_exec",
            "file_write",
            "desktop",
            "git_tool",
            "channel_send",
        },
    },
    "general": {
        "allowed_tools": set(),  # All tools passing standard approval gates
        "denied_tools": set(),
    },
}

# Sensitive actions that strictly require fresh explicit human approval via ApprovalStore
SENSITIVE_TOOLS: set[str] = {
    "file_write",
    "shell_exec",
    "desktop",
    "channel_send",
    "send_email",
    "sms_send",
    "execute_system_command",
    "file_delete",
    "delete_file",
}


class BackgroundTaskEngine:
    """Manages autonomous, long-running persistent tasks with safe crash recovery,
    step-by-step checkpointing, and steering controls."""

    def __init__(
        self,
        store: Optional[TaskStore] = None,
        bus: Optional[EventBus] = None,
        tool_executor_factory: Optional[Callable[[List[str], str], Any]] = None,
    ) -> None:
        self.store = store or TaskStore()
        self.bus = bus
        self._tool_executor_factory = tool_executor_factory
        self._active_workers: Dict[str, threading.Thread] = {}
        self._pause_flags: Dict[str, threading.Event] = {}
        self._cancel_flags: Dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def create_task(
        self,
        title: str,
        user_request: str,
        agent_profile: str = "general",
        budget: Optional[TaskBudget] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> BackgroundTask:
        """Create and persist a new background task."""
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        task = BackgroundTask(
            id=task_id,
            title=title,
            user_request=user_request,
            status=TaskStatus.CREATED,
            agent_profile=agent_profile,
            budget=budget or TaskBudget(),
            context=context or {},
        )
        self.store.save_task(task)
        self.store.log_event(task_id, "task_created", {"title": title, "profile": agent_profile})
        self._publish_event("task_created", task.to_dict())
        return task

    def start_task(
        self,
        task_id: str,
        plan_steps: Optional[List[TaskStep]] = None,
    ) -> bool:
        """Launch background worker thread for task execution."""
        with self._lock:
            task = self.store.get_task(task_id)
            if not task:
                return False

            if task.status in (TaskStatus.RUNNING, TaskStatus.COMPLETED):
                return False

            if plan_steps:
                task.plan = plan_steps
                self.store.save_task(task)

            self._pause_flags[task_id] = threading.Event()
            self._cancel_flags[task_id] = threading.Event()

            worker = threading.Thread(
                target=self._run_task_loop,
                args=(task_id,),
                name=f"task-worker-{task_id}",
                daemon=True,
            )
            self._active_workers[task_id] = worker
            worker.start()
            return True

    def pause_task(self, task_id: str) -> bool:
        """Pause execution after the currently running step finishes."""
        with self._lock:
            if task_id in self._pause_flags:
                self._pause_flags[task_id].set()
            task = self.store.get_task(task_id)
            if task and task.status == TaskStatus.RUNNING:
                self.store.update_status(task_id, TaskStatus.PAUSED)
                self.store.log_event(task_id, "task_paused", {})
                self._publish_event("task_paused", {"task_id": task_id})
                return True
            return False

    def resume_task(self, task_id: str) -> bool:
        """Resume execution of a paused or approval-waiting task."""
        with self._lock:
            task = self.store.get_task(task_id)
            if not task or task.status not in (TaskStatus.PAUSED, TaskStatus.WAITING_APPROVAL):
                return False

            if task_id in self._pause_flags:
                self._pause_flags[task_id].clear()

            return self.start_task(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel an ongoing or paused task."""
        with self._lock:
            if task_id in self._cancel_flags:
                self._cancel_flags[task_id].set()
            task = self.store.get_task(task_id)
            if task and task.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                self.store.update_status(task_id, TaskStatus.CANCELLED)
                self.store.log_event(task_id, "task_cancelled", {})
                self._publish_event("task_cancelled", {"task_id": task_id})
                return True
            return False

    def steer_task(self, task_id: str, instruction: str) -> bool:
        """Provide steering instruction (e.g. 'Focus on latency', 'Skip details')."""
        success = self.store.add_steering_instruction(task_id, instruction)
        if success:
            self._publish_event("task_steered", {"task_id": task_id, "instruction": instruction})
        return success

    def skip_step(self, task_id: str) -> bool:
        """Skip the current step and proceed to the next."""
        with self._lock:
            task = self.store.get_task(task_id)
            if not task or task.current_step_index >= len(task.plan):
                return False

            curr = task.plan[task.current_step_index]
            curr.status = StepStatus.SKIPPED
            curr.completed_at = time.time()
            task.current_step_index += 1
            self.store.save_task(task)
            self.store.log_event(task_id, "step_skipped", {"step_id": curr.step_id})
            return True

    def recover_on_startup(self) -> int:
        """Inspect task store on startup and recover interrupted tasks without
        repeating already completed side-effects."""
        interrupted = self.store.get_interrupted_tasks()
        recovered_count = 0

        for task in interrupted:
            logger.info("Found interrupted task %s (status=%s). Recovering...", task.id, task.status.value)
            latest_cp = self.store.get_latest_checkpoint(task.id)

            if latest_cp:
                # Restore to last checkpointed step
                task.current_step_index = latest_cp.step_index
                task.context.update(latest_cp.context)
                task.status = TaskStatus.RECOVERING
            else:
                task.status = TaskStatus.PAUSED

            self.store.save_task(task)
            self.store.log_event(task.id, "task_recovered_on_startup", {"resumed_at_step": task.current_step_index})
            recovered_count += 1

            # Resume safely in background
            self.start_task(task.id)

        return recovered_count

    def _run_task_loop(self, task_id: str) -> None:
        """Main execution loop for a background task."""
        task = self.store.get_task(task_id)
        if not task:
            return

        self.store.update_status(task_id, TaskStatus.RUNNING)
        self._publish_event("task_status", {"task_id": task_id, "status": "running"})

        start_time = time.time()
        pause_event = self._pause_flags.get(task_id)
        cancel_event = self._cancel_flags.get(task_id)

        try:
            # If no plan was generated yet, plan first
            if not task.plan:
                self.store.update_status(task_id, TaskStatus.PLANNING)
                task.plan = self._generate_plan(task)
                task.status = TaskStatus.RUNNING
                self.store.save_task(task)

            # Execute steps sequentially with checkpointing
            while task.current_step_index < len(task.plan):
                if cancel_event and cancel_event.is_set():
                    self.store.update_status(task_id, TaskStatus.CANCELLED)
                    return

                if pause_event and pause_event.is_set():
                    self.store.update_status(task_id, TaskStatus.PAUSED)
                    return

                # Check budget
                elapsed = time.time() - start_time
                if elapsed > task.budget.max_execution_time_seconds:
                    error_msg = f"Task exceeded maximum execution time ({task.budget.max_execution_time_seconds}s)"
                    self.store.update_status(task_id, TaskStatus.FAILED, error=error_msg)
                    return

                if task.budget.current_tool_calls >= task.budget.max_tool_calls:
                    error_msg = f"Task exceeded maximum tool calls budget ({task.budget.max_tool_calls})"
                    self.store.update_status(task_id, TaskStatus.FAILED, error=error_msg)
                    return

                step = task.plan[task.current_step_index]

                # Check if step was already completed (idempotency key protection)
                if step.status == StepStatus.COMPLETED:
                    task.current_step_index += 1
                    continue

                # Execute step
                step.status = StepStatus.RUNNING
                step.started_at = time.time()
                self.store.save_task(task)
                self.store.log_event(task_id, "step_started", {"step_number": step.step_number, "title": step.title})
                self._publish_event("step_started", {"task_id": task_id, "step_number": step.step_number})

                # 1. Profile and workforce specialist isolation check
                permitted, deny_reason = self._is_tool_permitted(
                    task.agent_profile,
                    step.tool_name,
                    agent_id=step.assigned_agent,
                )
                if not permitted:
                    step.status = StepStatus.FAILED
                    step.error = deny_reason
                    self.store.update_status(task_id, TaskStatus.FAILED, error=deny_reason)
                    if step.assigned_agent:
                        try:
                            from openjarvis.workforce.registry import AgentWorkforceRegistry
                            from openjarvis.workforce.models import AgentRuntimeState
                            AgentWorkforceRegistry.get_instance().set_agent_state(
                                step.assigned_agent,
                                AgentRuntimeState.FAILED,
                                error=deny_reason,
                            )
                        except Exception:
                            pass
                    return

                # 2. Sensitive action explicit approval gate
                is_approved, approval_id = self._check_step_approval(task, step)
                if not is_approved:
                    step.status = StepStatus.WAITING_APPROVAL
                    task.status = TaskStatus.WAITING_APPROVAL
                    self.store.save_task(task)
                    self.store.log_event(
                        task_id,
                        "step_waiting_approval",
                        {
                            "step_number": step.step_number,
                            "tool": step.tool_name,
                            "approval_id": approval_id,
                            "assigned_agent": step.assigned_agent,
                        },
                    )
                    self._publish_event(
                        "task_waiting_approval",
                        {
                            "task_id": task_id,
                            "step_number": step.step_number,
                            "approval_id": approval_id,
                            "assigned_agent": step.assigned_agent,
                        },
                    )
                    if step.assigned_agent:
                        try:
                            from openjarvis.workforce.registry import AgentWorkforceRegistry
                            from openjarvis.workforce.models import AgentRuntimeState
                            AgentWorkforceRegistry.get_instance().set_agent_state(
                                step.assigned_agent,
                                AgentRuntimeState.WAITING_APPROVAL,
                                current_task_id=task_id,
                            )
                        except Exception:
                            pass
                    logger.info(
                        "Task %s step %d waiting for explicit human approval (action_id=%s)",
                        task_id,
                        step.step_number,
                        approval_id,
                    )
                    return

                # Execute step tool call
                step_result, step_error = self._execute_step(task, step)

                if step_error:
                    task.budget.current_retries += 1
                    if task.budget.current_retries > task.budget.max_retries:
                        step.status = StepStatus.FAILED
                        step.error = step_error
                        self.store.update_status(task_id, TaskStatus.FAILED, error=step_error)
                        return
                    else:
                        logger.warning("Step %d failed, retrying (%d/%d): %s", step.step_number, task.budget.current_retries, task.budget.max_retries, step_error)
                        time.sleep(1.0)
                        continue

                step.status = StepStatus.COMPLETED
                step.completed_at = time.time()
                step.tool_result = step_result
                task.budget.current_tool_calls += 1
                task.current_step_index += 1

                # Checkpoint progress
                checkpoint = TaskCheckpoint(
                    checkpoint_id=f"cp-{task.id}-{task.current_step_index}",
                    task_id=task.id,
                    step_index=task.current_step_index,
                    plan=[s.to_dict() for s in task.plan],
                    completed_steps=[s.to_dict() for s in task.plan[: task.current_step_index]],
                    pending_steps=[s.to_dict() for s in task.plan[task.current_step_index :]],
                    context=task.context,
                    artifacts=task.artifacts,
                )
                self.store.save_checkpoint(checkpoint)
                self.store.save_task(task)
                self.store.log_event(task_id, "step_completed", {"step_number": step.step_number})
                self._publish_event("step_completed", {"task_id": task_id, "step_number": step.step_number})

            # Verification phase
            self.store.update_status(task_id, TaskStatus.VERIFYING)
            verification_ok = self._verify_results(task)
            task.verification_status = "verified" if verification_ok else "failed"

            self.store.update_status(task_id, TaskStatus.COMPLETED if verification_ok else TaskStatus.FAILED)
            self._publish_event("task_completed", {"task_id": task_id, "verified": verification_ok})
            logger.info("Task %s completed successfully (verified=%s)", task_id, verification_ok)

        except Exception as exc:
            logger.exception("Task worker encountered unexpected error: %s", exc)
            self.store.update_status(task_id, TaskStatus.FAILED, error=str(exc))
        finally:
            with self._lock:
                self._active_workers.pop(task_id, None)
                self._pause_flags.pop(task_id, None)
                self._cancel_flags.pop(task_id, None)

    def _generate_plan(self, task: BackgroundTask) -> List[TaskStep]:
        """Generate dynamic multi-agent execution plan for the task using AtlasOrchestrator."""
        try:
            from openjarvis.workforce.orchestrator import AtlasOrchestrator

            orchestrator = AtlasOrchestrator(bus=self.bus)
            return orchestrator.plan_task(task.user_request)
        except Exception as exc:
            logger.warning("AtlasOrchestrator planning encountered error: %s, using fallback", exc)
            return [
                TaskStep(
                    step_id=f"{task.id}-step-1",
                    step_number=1,
                    title="Investigate and gather context",
                    description=f"Inspect query and gather data for: {task.user_request}",
                    tool_name="knowledge_search",
                    tool_params={"query": task.user_request},
                    idempotency_key=f"{task.id}:step:1:knowledge_search",
                    assigned_agent="scout",
                ),
                TaskStep(
                    step_id=f"{task.id}-step-2",
                    step_number=2,
                    title="Synthesize and compile findings",
                    description="Process evidence and structure recommendations",
                    tool_name="think",
                    tool_params={"thought": f"Synthesizing results for {task.title}"},
                    idempotency_key=f"{task.id}:step:2:think",
                    assigned_agent="atlas",
                ),
            ]

    def _is_tool_permitted(
        self,
        profile: str,
        tool_name: str,
        agent_id: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Enforce agent capability isolation and specialist permissions."""
        if not tool_name:
            return True, None

        # Check workforce specialist manifest if agent assigned
        if agent_id:
            try:
                from openjarvis.workforce.registry import AgentWorkforceRegistry

                reg = AgentWorkforceRegistry.get_instance()
                manifest = reg.get_agent_manifest(agent_id)
                if manifest:
                    if tool_name in manifest.denied_tools:
                        return False, f"Agent '{agent_id}' is explicitly denied from using tool '{tool_name}'"
                    if manifest.allowed_tools and tool_name not in manifest.allowed_tools:
                        return False, f"Tool '{tool_name}' not in allowed tools for agent '{agent_id}'"
            except Exception as exc:
                logger.debug("Workforce manifest lookup skipped: %s", exc)

        prof = CAPABILITY_PROFILES.get(profile, CAPABILITY_PROFILES.get("general", {}))
        if tool_name in prof.get("denied_tools", set()):
            return False, f"Tool '{tool_name}' denied by capability profile '{profile}'"
        allowed = prof.get("allowed_tools", set())
        if allowed and tool_name not in allowed:
            return False, f"Tool '{tool_name}' not in allowed tools for capability profile '{profile}'"
        return True, None

    def _check_step_approval(self, task: BackgroundTask, step: TaskStep) -> tuple[bool, Optional[str]]:
        """Enforce fresh, explicit human approval for sensitive actions."""
        if not step.tool_name or step.tool_name not in SENSITIVE_TOOLS:
            return True, None

        from openjarvis.tools.approval_store import (
            ApprovalStore,
            STATUS_APPROVED,
            TIER_HIGH,
        )

        approval_store = getattr(self, "_approval_store", None)
        if approval_store is None:
            approval_store = ApprovalStore()
            self._approval_store = approval_store

        perm_key = f"task:{task.id}:step:{step.step_number}:{step.tool_name}"
        existing = approval_store.get_action_by_permission_key(perm_key)

        if existing and existing.status == STATUS_APPROVED:
            return True, existing.id

        if existing:
            return False, existing.id

        # Queue fresh pending approval request
        desc = (
            f"Task '{task.title}' (Step {step.step_number} by {step.assigned_agent or 'system'}): "
            f"Execute sensitive action '{step.tool_name}' with parameters {step.tool_params}"
        )
        action = approval_store.queue_action(
            action_type=step.tool_name,
            description=desc,
            payload=step.tool_params,
            permission_key=perm_key,
            tier=TIER_HIGH,
        )
        return False, action.id

    def _execute_step(
        self,
        task: BackgroundTask,
        step: TaskStep,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Execute step tool call using ToolRegistry."""
        from openjarvis.core.registry import ToolRegistry

        if not step.tool_name:
            return {"status": "ok"}, None

        if not ToolRegistry.contains(step.tool_name):
            return None, f"Tool '{step.tool_name}' not registered"

        try:
            tool_cls = ToolRegistry.get(step.tool_name)
            tool_inst = tool_cls() if callable(tool_cls) else tool_cls
            res = tool_inst.execute(**step.tool_params)
            if hasattr(res, "success") and not res.success:
                return None, str(getattr(res, "content", "Execution failed"))
            content = getattr(res, "content", str(res))
            return {"content": content, "success": True}, None
        except Exception as exc:
            return None, str(exc)

    def _verify_results(self, task: BackgroundTask) -> bool:
        """Verify that completed task produced meaningful output."""
        completed_steps = [s for s in task.plan if s.status == StepStatus.COMPLETED]
        return len(completed_steps) > 0

    def _publish_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.bus:
            try:
                self.bus.publish(EventType.TASK_EVENT if hasattr(EventType, "TASK_EVENT") else EventType.AGENT_EVENT, {"type": event_type, **data})
            except Exception:
                pass


_GLOBAL_TASK_ENGINE: Optional[BackgroundTaskEngine] = None


def get_task_engine(bus: Optional[EventBus] = None) -> BackgroundTaskEngine:
    global _GLOBAL_TASK_ENGINE
    if _GLOBAL_TASK_ENGINE is None:
        _GLOBAL_TASK_ENGINE = BackgroundTaskEngine(bus=bus)
    return _GLOBAL_TASK_ENGINE

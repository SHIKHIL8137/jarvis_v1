"""Unified Routine Engine — schedule calculation, background dispatch, quiet-hours gating,
and missed-run recovery."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from openjarvis.core.events import EventBus, EventType
from openjarvis.routines.models import Routine, RoutineRun, RoutineRunStatus, RoutineType
from openjarvis.routines.quiet_hours import QuietHoursManager
from openjarvis.routines.store import RoutineStore

logger = logging.getLogger(__name__)


def compute_next_run(
    routine_type: RoutineType,
    schedule_value: str,
    timezone_name: str = "Asia/Kolkata",
    now_ts: Optional[float] = None,
) -> Optional[float]:
    """Calculate the next execution timestamp based on schedule type and timezone."""
    now = now_ts if now_ts is not None else time.time()
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    now_dt = datetime.fromtimestamp(now, tz=tz)

    if routine_type == RoutineType.INTERVAL:
        seconds = float(schedule_value)
        return now + max(1.0, seconds)

    elif routine_type == RoutineType.DAILY:
        parts = schedule_value.strip().split(":")
        target_hour = int(parts[0])
        target_min = int(parts[1]) if len(parts) > 1 else 0
        target_dt = now_dt.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        if target_dt <= now_dt:
            target_dt += timedelta(days=1)
        return target_dt.timestamp()

    elif routine_type == RoutineType.WEEKLY:
        # e.g. "monday 09:00"
        parts = schedule_value.strip().split()
        day_name = parts[0].lower()
        time_str = parts[1] if len(parts) > 1 else "09:00"
        t_parts = time_str.split(":")
        target_hour = int(t_parts[0])
        target_min = int(t_parts[1]) if len(t_parts) > 1 else 0

        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        target_weekday = days.index(day_name) if day_name in days else 0

        days_ahead = (target_weekday - now_dt.weekday()) % 7
        target_dt = (now_dt + timedelta(days=days_ahead)).replace(
            hour=target_hour, minute=target_min, second=0, microsecond=0
        )
        if target_dt <= now_dt:
            target_dt += timedelta(days=7)
        return target_dt.timestamp()

    elif routine_type == RoutineType.CRON:
        from croniter import croniter

        cron = croniter(schedule_value, now_dt)
        next_dt = cron.get_next(datetime)
        return next_dt.timestamp()

    elif routine_type == RoutineType.ONE_TIME:
        # Can be float timestamp or ISO string
        try:
            return float(schedule_value)
        except ValueError:
            dt = datetime.fromisoformat(schedule_value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.timestamp()

    elif routine_type == RoutineType.STARTUP:
        return now

    return None


class RoutineEngine:
    """Unified scheduler and execution engine for all routines and proactive actions."""

    def __init__(
        self,
        store: Optional[RoutineStore] = None,
        quiet_hours: Optional[QuietHoursManager] = None,
        bus: Optional[EventBus] = None,
        action_dispatcher: Optional[Callable[[Routine], Any]] = None,
    ) -> None:
        self.store = store or RoutineStore()
        self.quiet_hours = quiet_hours or QuietHoursManager()
        self.bus = bus
        self._action_dispatcher = action_dispatcher
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def start(self) -> None:
        """Start the background scheduler thread and execute startup routines."""
        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._handle_startup_and_missed_runs()
            self._worker_thread = threading.Thread(
                target=self._scheduler_loop,
                name="routine-scheduler-loop",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info("RoutineEngine started")

    def stop(self) -> None:
        """Stop the background scheduler loop."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        logger.info("RoutineEngine stopped")

    def create_routine(
        self,
        name: str,
        routine_type: RoutineType,
        schedule_value: str,
        action_type: str = "task",
        action_payload: Optional[Dict[str, Any]] = None,
        timezone: str = "Asia/Kolkata",
        is_voice_announcement: bool = False,
        description: str = "",
    ) -> Routine:
        """Create and schedule a new routine."""
        routine_id = f"routine-{uuid.uuid4().hex[:10]}"
        next_run = compute_next_run(routine_type, schedule_value, timezone)
        routine = Routine(
            id=routine_id,
            name=name,
            description=description,
            routine_type=routine_type,
            schedule_value=schedule_value,
            action_type=action_type,
            action_payload=action_payload or {},
            timezone=timezone,
            is_voice_announcement=is_voice_announcement,
            next_run=next_run,
        )
        self.store.save_routine(routine)
        logger.info("Created routine '%s' (id=%s, next_run=%s)", name, routine_id, next_run)
        return routine

    def trigger_routine(self, routine_id: str) -> Optional[RoutineRun]:
        """Manually trigger immediate execution of a routine."""
        routine = self.store.get_routine(routine_id)
        if not routine:
            return None
        return self._execute_routine(routine)

    def _handle_startup_and_missed_runs(self) -> None:
        """Handle startup routines and missed recurring runs upon engine startup."""
        now = time.time()
        routines = self.store.list_routines(enabled_only=True)

        for r in routines:
            # Handle startup routines
            if r.routine_type == RoutineType.STARTUP:
                logger.info("Executing startup routine '%s'", r.name)
                self._execute_routine(r)
                continue

            # Handle missed runs
            if r.next_run and r.next_run < now:
                elapsed_missed = now - r.next_run
                logger.info("Routine '%s' was missed by %.1fs", r.name, elapsed_missed)

                if r.routine_type == RoutineType.ONE_TIME:
                    # Run once and disable
                    self._execute_routine(r)
                    r.enabled = False
                else:
                    # Catchup: compute the NEXT future execution slot to avoid backlogged storms
                    r.next_run = compute_next_run(r.routine_type, r.schedule_value, r.timezone, now)
                    self.store.save_routine(r)
                    # Log missed catchup event
                    run_record = RoutineRun(
                        id=uuid.uuid4().hex[:12],
                        routine_id=r.id,
                        status=RoutineRunStatus.MISSED_CATCHUP,
                        executed_at=now,
                        result_summary=f"Skipped backlog; next execution set to {r.next_run}",
                    )
                    self.store.log_run(run_record)

    def _scheduler_loop(self) -> None:
        """Check for due routines every second."""
        while not self._stop_event.is_set():
            now = time.time()
            try:
                routines = self.store.list_routines(enabled_only=True)
                for r in routines:
                    if r.next_run and r.next_run <= now:
                        self._execute_routine(r)
            except Exception as exc:
                logger.exception("Error in routine scheduler loop: %s", exc)

            time.sleep(1.0)

    def _execute_routine(self, routine: Routine) -> RoutineRun:
        """Execute a routine, checking quiet hours and updating next fire time."""
        now = time.time()
        start_t = time.time()

        # Quiet hours check for voice announcements
        if self.quiet_hours.should_suppress_voice(routine.is_voice_announcement, now):
            logger.info("Routine '%s' suppressed during quiet hours", routine.name)
            self.quiet_hours.queue_notification(
                {
                    "routine_id": routine.id,
                    "name": routine.name,
                    "payload": routine.action_payload,
                }
            )
            run_record = RoutineRun(
                id=uuid.uuid4().hex[:12],
                routine_id=routine.id,
                status=RoutineRunStatus.SKIPPED_QUIET_HOURS,
                executed_at=now,
                duration_seconds=0.0,
                result_summary="Suppressed during quiet hours; queued for later.",
            )
            self.store.log_run(run_record)
            self._update_next_run(routine, now)
            return run_record

        # Execute action
        error_msg: Optional[str] = None
        result_summary = "Executed successfully"
        try:
            if self._action_dispatcher:
                self._action_dispatcher(routine)
            else:
                self._default_dispatch(routine)
            status = RoutineRunStatus.SUCCESS
            routine.run_count += 1
        except Exception as exc:
            logger.exception("Failed to execute routine '%s': %s", routine.name, exc)
            status = RoutineRunStatus.FAILED
            error_msg = str(exc)
            result_summary = f"Execution error: {exc}"
            routine.fail_count += 1

        duration = time.time() - start_t
        run_record = RoutineRun(
            id=uuid.uuid4().hex[:12],
            routine_id=routine.id,
            status=status,
            executed_at=now,
            duration_seconds=duration,
            result_summary=result_summary,
            error=error_msg,
        )
        self.store.log_run(run_record)

        # Update schedule
        routine.last_run = now
        self._update_next_run(routine, now)
        return run_record

    def _update_next_run(self, routine: Routine, from_time: float) -> None:
        if routine.routine_type == RoutineType.ONE_TIME:
            routine.enabled = False
            routine.next_run = None
        else:
            routine.next_run = compute_next_run(routine.routine_type, routine.schedule_value, routine.timezone, from_time)
        self.store.save_routine(routine)

    def _default_dispatch(self, routine: Routine) -> None:
        """Default dispatch logic for routine actions."""
        if routine.action_type == "task":
            from openjarvis.tasks.engine import get_task_engine

            engine = get_task_engine(self.bus)
            engine.create_task(
                title=routine.name,
                user_request=routine.action_payload.get("request", routine.description or routine.name),
                agent_profile=routine.action_payload.get("profile", "general"),
            )
        elif routine.action_type == "speech":
            from openjarvis.speech.session_manager import get_voice_session_manager

            text = routine.action_payload.get("text", routine.name)
            get_voice_session_manager().speak(text)


_GLOBAL_ROUTINE_ENGINE: Optional[RoutineEngine] = None


def get_routine_engine(bus: Optional[EventBus] = None) -> RoutineEngine:
    global _GLOBAL_ROUTINE_ENGINE
    if _GLOBAL_ROUTINE_ENGINE is None:
        _GLOBAL_ROUTINE_ENGINE = RoutineEngine(bus=bus)
    return _GLOBAL_ROUTINE_ENGINE

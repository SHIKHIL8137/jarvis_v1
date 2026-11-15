"""Tests for Unified Routines, Scheduling, and Quiet Hours (Milestone 3)."""

from __future__ import annotations

import time
import pytest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openjarvis.routines.engine import RoutineEngine, compute_next_run
from openjarvis.routines.models import Routine, RoutineRunStatus, RoutineType
from openjarvis.routines.quiet_hours import QuietHoursManager
from openjarvis.routines.store import RoutineStore


@pytest.fixture
def routine_store(tmp_path: Path) -> RoutineStore:
    return RoutineStore(db_path=tmp_path / "routines.db")


@pytest.fixture
def quiet_hours() -> QuietHoursManager:
    return QuietHoursManager(
        enabled=True,
        start_time_str="22:00",
        end_time_str="07:00",
        timezone_name="Asia/Kolkata",
    )


class TestScheduleCalculation:
    def test_interval_schedule(self):
        now = 1000.0
        next_run = compute_next_run(RoutineType.INTERVAL, "300", now_ts=now)
        assert next_run == 1300.0

    def test_daily_schedule_kolkata(self):
        tz = ZoneInfo("Asia/Kolkata")
        # 8:00 AM Kolkata
        base_dt = datetime(2026, 11, 15, 8, 0, 0, tzinfo=tz)
        base_ts = base_dt.timestamp()

        # Target 9:00 AM Kolkata -> same day
        next_ts = compute_next_run(RoutineType.DAILY, "09:00", "Asia/Kolkata", now_ts=base_ts)
        next_dt = datetime.fromtimestamp(next_ts, tz=tz)
        assert next_dt.day == 15
        assert next_dt.hour == 9
        assert next_dt.minute == 0

        # Target 7:00 AM Kolkata (in past for this day) -> next day
        next_ts_tomorrow = compute_next_run(RoutineType.DAILY, "07:00", "Asia/Kolkata", now_ts=base_ts)
        next_dt_tomorrow = datetime.fromtimestamp(next_ts_tomorrow, tz=tz)
        assert next_dt_tomorrow.day == 16
        assert next_dt_tomorrow.hour == 7


class TestQuietHours:
    def test_quiet_hours_overnight(self, quiet_hours: QuietHoursManager):
        tz = ZoneInfo("Asia/Kolkata")

        # 23:30 (Night) -> in quiet hours
        night_dt = datetime(2026, 11, 15, 23, 30, tzinfo=tz)
        assert quiet_hours.is_in_quiet_hours(night_dt.timestamp()) is True
        assert quiet_hours.should_suppress_voice(is_voice_announcement=True, check_timestamp=night_dt.timestamp()) is True

        # 05:30 (Early morning) -> in quiet hours
        early_dt = datetime(2026, 11, 15, 5, 30, tzinfo=tz)
        assert quiet_hours.is_in_quiet_hours(early_dt.timestamp()) is True

        # 14:00 (Afternoon) -> NOT in quiet hours
        day_dt = datetime(2026, 11, 15, 14, 0, tzinfo=tz)
        assert quiet_hours.is_in_quiet_hours(day_dt.timestamp()) is False
        assert quiet_hours.should_suppress_voice(is_voice_announcement=True, check_timestamp=day_dt.timestamp()) is False

    def test_notification_queuing(self, quiet_hours: QuietHoursManager):
        quiet_hours.queue_notification({"title": "Morning reminder", "text": "Check email"})
        assert quiet_hours.to_dict()["queued_count"] == 1
        flushed = quiet_hours.flush_queued()
        assert len(flushed) == 1
        assert flushed[0]["title"] == "Morning reminder"
        assert quiet_hours.to_dict()["queued_count"] == 0


class TestRoutinePersistenceAndExecution:
    def test_create_and_run_routine(self, routine_store: RoutineStore, quiet_hours: QuietHoursManager):
        dispatched = []
        engine = RoutineEngine(
            store=routine_store,
            quiet_hours=quiet_hours,
            action_dispatcher=lambda r: dispatched.append(r.name),
        )

        r = engine.create_routine(
            name="Daily status update",
            routine_type=RoutineType.INTERVAL,
            schedule_value="3600",
            action_type="task",
        )
        assert r.id.startswith("routine-")
        assert routine_store.get_routine(r.id) is not None

        # Trigger manually
        run_record = engine.trigger_routine(r.id)
        assert run_record is not None
        assert run_record.status == RoutineRunStatus.SUCCESS
        assert len(dispatched) == 1
        assert dispatched[0] == "Daily status update"

        # Verify run logged
        runs = routine_store.list_runs(routine_id=r.id)
        assert len(runs) == 1
        assert runs[0].status == RoutineRunStatus.SUCCESS

    def test_missed_run_catchup_on_startup(self, routine_store: RoutineStore, quiet_hours: QuietHoursManager):
        now = time.time()
        # Routine with next_run in the past (missed by 2 hours)
        missed_routine = Routine(
            id="routine-missed",
            name="Missed Check",
            routine_type=RoutineType.INTERVAL,
            schedule_value="600",
            next_run=now - 7200,
        )
        routine_store.save_routine(missed_routine)

        engine = RoutineEngine(store=routine_store, quiet_hours=quiet_hours)
        engine._handle_startup_and_missed_runs()

        updated = routine_store.get_routine("routine-missed")
        assert updated.next_run > now

        # Run history records catchup
        runs = routine_store.list_runs(routine_id="routine-missed")
        assert len(runs) == 1
        assert runs[0].status == RoutineRunStatus.MISSED_CATCHUP

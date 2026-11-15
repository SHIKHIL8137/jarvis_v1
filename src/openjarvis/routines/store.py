"""SQLite-backed persistence for scheduled routines and execution history."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import get_config_dir
from openjarvis.routines.models import Routine, RoutineRun

logger = logging.getLogger(__name__)


class RoutineStore:
    """Thread-safe SQLite store for routines and routine execution logs."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            self._db_path = get_config_dir() / "routines.db"
        else:
            self._db_path = Path(db_path)

        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS routines (
                    id                      TEXT PRIMARY KEY,
                    name                    TEXT NOT NULL,
                    description             TEXT NOT NULL DEFAULT '',
                    routine_type            TEXT NOT NULL,
                    schedule_value          TEXT NOT NULL,
                    action_type             TEXT NOT NULL,
                    action_payload_json     TEXT NOT NULL DEFAULT '{}',
                    timezone                TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                    enabled                 INTEGER NOT NULL DEFAULT 1,
                    is_voice_announcement   INTEGER NOT NULL DEFAULT 0,
                    created_at              REAL NOT NULL,
                    last_run                REAL,
                    next_run                REAL,
                    run_count               INTEGER NOT NULL DEFAULT 0,
                    fail_count              INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS routine_runs (
                    id                      TEXT PRIMARY KEY,
                    routine_id              TEXT NOT NULL,
                    status                  TEXT NOT NULL,
                    executed_at             REAL NOT NULL,
                    duration_seconds        REAL NOT NULL DEFAULT 0.0,
                    result_summary          TEXT NOT NULL DEFAULT '',
                    error                   TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_routines_enabled ON routines(enabled);
                CREATE INDEX IF NOT EXISTS idx_routine_runs_routine ON routine_runs(routine_id);
            """)
            self._conn.commit()

    def save_routine(self, routine: Routine) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO routines (
                    id, name, description, routine_type, schedule_value,
                    action_type, action_payload_json, timezone, enabled,
                    is_voice_announcement, created_at, last_run, next_run,
                    run_count, fail_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    routine_type=excluded.routine_type,
                    schedule_value=excluded.schedule_value,
                    action_type=excluded.action_type,
                    action_payload_json=excluded.action_payload_json,
                    timezone=excluded.timezone,
                    enabled=excluded.enabled,
                    is_voice_announcement=excluded.is_voice_announcement,
                    last_run=excluded.last_run,
                    next_run=excluded.next_run,
                    run_count=excluded.run_count,
                    fail_count=excluded.fail_count
                """,
                (
                    routine.id,
                    routine.name,
                    routine.description,
                    routine.routine_type.value,
                    routine.schedule_value,
                    routine.action_type,
                    json.dumps(routine.action_payload),
                    routine.timezone,
                    1 if routine.enabled else 0,
                    1 if routine.is_voice_announcement else 0,
                    routine.created_at,
                    routine.last_run,
                    routine.next_run,
                    routine.run_count,
                    routine.fail_count,
                ),
            )
            self._conn.commit()

    def get_routine(self, routine_id: str) -> Optional[Routine]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_routine(row)

    def list_routines(self, enabled_only: bool = False) -> List[Routine]:
        with self._lock:
            if enabled_only:
                cur = self._conn.execute("SELECT * FROM routines WHERE enabled = 1 ORDER BY created_at ASC")
            else:
                cur = self._conn.execute("SELECT * FROM routines ORDER BY created_at ASC")
            return [self._row_to_routine(r) for r in cur.fetchall()]

    def delete_routine(self, routine_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def log_run(self, run: RoutineRun) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO routine_runs (id, routine_id, status, executed_at, duration_seconds, result_summary, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.routine_id,
                    run.status.value,
                    run.executed_at,
                    run.duration_seconds,
                    run.result_summary,
                    run.error,
                ),
            )
            self._conn.commit()

    def list_runs(self, routine_id: Optional[str] = None, limit: int = 50) -> List[RoutineRun]:
        with self._lock:
            if routine_id:
                cur = self._conn.execute(
                    "SELECT * FROM routine_runs WHERE routine_id = ? ORDER BY executed_at DESC LIMIT ?",
                    (routine_id, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM routine_runs ORDER BY executed_at DESC LIMIT ?",
                    (limit,),
                )
            return [
                RoutineRun(
                    id=r["id"],
                    routine_id=r["routine_id"],
                    status=r["status"],
                    executed_at=r["executed_at"],
                    duration_seconds=r["duration_seconds"],
                    result_summary=r["result_summary"],
                    error=r["error"],
                )
                for r in cur.fetchall()
            ]

    def _row_to_routine(self, row: sqlite3.Row) -> Routine:
        return Routine.from_dict(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "routine_type": row["routine_type"],
                "schedule_value": row["schedule_value"],
                "action_type": row["action_type"],
                "action_payload": json.loads(row["action_payload_json"] or "{}"),
                "timezone": row["timezone"],
                "enabled": bool(row["enabled"]),
                "is_voice_announcement": bool(row["is_voice_announcement"]),
                "created_at": row["created_at"],
                "last_run": row["last_run"],
                "next_run": row["next_run"],
                "run_count": row["run_count"],
                "fail_count": row["fail_count"],
            }
        )

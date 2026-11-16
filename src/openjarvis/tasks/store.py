"""SQLite-backed persistent store for background tasks, checkpoints, and events."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import get_config_dir
from openjarvis.tasks.models import BackgroundTask, TaskCheckpoint, TaskStatus

logger = logging.getLogger(__name__)


class TaskStore:
    """Thread-safe SQLite store for background tasks and checkpoints."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            self._db_path = get_config_dir() / "tasks.db"
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
                CREATE TABLE IF NOT EXISTS background_tasks (
                    id                      TEXT PRIMARY KEY,
                    title                   TEXT NOT NULL,
                    user_request            TEXT NOT NULL,
                    status                  TEXT NOT NULL,
                    agent_profile           TEXT NOT NULL DEFAULT 'general',
                    plan_json               TEXT NOT NULL DEFAULT '[]',
                    current_step_index      INTEGER NOT NULL DEFAULT 0,
                    budget_json             TEXT NOT NULL DEFAULT '{}',
                    context_json            TEXT NOT NULL DEFAULT '{}',
                    artifacts_json          TEXT NOT NULL DEFAULT '[]',
                    approval_required       INTEGER NOT NULL DEFAULT 0,
                    verification_status     TEXT NOT NULL DEFAULT 'unverified',
                    steering_json           TEXT NOT NULL DEFAULT '[]',
                    created_at              REAL NOT NULL,
                    updated_at              REAL NOT NULL,
                    completed_at            REAL,
                    error                   TEXT
                );

                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    checkpoint_id           TEXT PRIMARY KEY,
                    task_id                 TEXT NOT NULL,
                    step_index              INTEGER NOT NULL,
                    snapshot_json           TEXT NOT NULL,
                    timestamp               REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    id                      TEXT PRIMARY KEY,
                    task_id                 TEXT NOT NULL,
                    event_type              TEXT NOT NULL,
                    details_json            TEXT NOT NULL,
                    timestamp               REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id                      TEXT PRIMARY KEY,
                    task_id                 TEXT NOT NULL,
                    name                    TEXT NOT NULL,
                    artifact_type           TEXT NOT NULL,
                    path                    TEXT NOT NULL,
                    summary                 TEXT NOT NULL DEFAULT '',
                    created_at              REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id                 TEXT PRIMARY KEY,
                    worker_id               TEXT NOT NULL,
                    acquired_at             REAL NOT NULL,
                    expires_at              REAL NOT NULL,
                    heartbeat_at            REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON background_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON task_checkpoints(task_id);
                CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
            """)
            self._conn.commit()

    def save_task(self, task: BackgroundTask) -> None:
        """Insert or update a background task record."""
        with self._lock:
            task.updated_at = time.time()
            task_dict = task.to_dict()
            self._conn.execute(
                """
                INSERT INTO background_tasks (
                    id, title, user_request, status, agent_profile,
                    plan_json, current_step_index, budget_json, context_json,
                    artifacts_json, approval_required, verification_status,
                    steering_json, created_at, updated_at, completed_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    status=excluded.status,
                    agent_profile=excluded.agent_profile,
                    plan_json=excluded.plan_json,
                    current_step_index=excluded.current_step_index,
                    budget_json=excluded.budget_json,
                    context_json=excluded.context_json,
                    artifacts_json=excluded.artifacts_json,
                    approval_required=excluded.approval_required,
                    verification_status=excluded.verification_status,
                    steering_json=excluded.steering_json,
                    updated_at=excluded.updated_at,
                    completed_at=excluded.completed_at,
                    error=excluded.error
                """,
                (
                    task.id,
                    task.title,
                    task.user_request,
                    task.status.value,
                    task.agent_profile,
                    json.dumps(task_dict["plan"]),
                    task.current_step_index,
                    json.dumps(task_dict["budget"]),
                    json.dumps(task.context),
                    json.dumps(task.artifacts),
                    1 if task.approval_required else 0,
                    task.verification_status,
                    json.dumps(task.steering_instructions),
                    task.created_at,
                    task.updated_at,
                    task.completed_at,
                    task.error,
                ),
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        """Fetch a task by ID."""
        with self._lock:
            cur = self._conn.execute("SELECT * FROM background_tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_task(row)

    def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[BackgroundTask]:
        """List tasks ordered by created_at descending."""
        with self._lock:
            if status:
                cur = self._conn.execute(
                    "SELECT * FROM background_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM background_tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_task(r) for r in cur.fetchall()]

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: Optional[str] = None,
    ) -> bool:
        """Update task status and error message."""
        with self._lock:
            completed_at = time.time() if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) else None
            cur = self._conn.execute(
                """
                UPDATE background_tasks
                SET status = ?, error = ?, updated_at = ?, completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status.value, error, time.time(), completed_at, task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def add_steering_instruction(self, task_id: str, instruction: str) -> bool:
        """Append a steering instruction to an active task."""
        with self._lock:
            task = self.get_task(task_id)
            if not task:
                return False
            task.steering_instructions.append(instruction)
            self.save_task(task)
            self.log_event(task_id, "task_steered", {"instruction": instruction})
            return True

    def save_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """Save a step execution checkpoint."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO task_checkpoints (checkpoint_id, task_id, step_index, snapshot_json, timestamp)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    timestamp=excluded.timestamp
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.task_id,
                    checkpoint.step_index,
                    json.dumps(checkpoint.to_dict()),
                    checkpoint.timestamp,
                ),
            )
            self._conn.commit()

    def get_latest_checkpoint(self, task_id: str) -> Optional[TaskCheckpoint]:
        """Fetch the most recent checkpoint for a task."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT snapshot_json FROM task_checkpoints WHERE task_id = ? ORDER BY step_index DESC, timestamp DESC LIMIT 1",
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            data = json.loads(row["snapshot_json"])
            return TaskCheckpoint(
                checkpoint_id=data.get("checkpoint_id", ""),
                task_id=data.get("task_id", ""),
                step_index=data.get("step_index", 0),
                plan=data.get("plan") or [],
                completed_steps=data.get("completed_steps") or [],
                pending_steps=data.get("pending_steps") or [],
                context=data.get("context") or {},
                artifacts=data.get("artifacts") or [],
                timestamp=data.get("timestamp", time.time()),
            )

    def log_event(self, task_id: str, event_type: str, details: Dict[str, Any]) -> None:
        """Record an operational audit event for a task."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO task_events (id, task_id, event_type, details_json, timestamp) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:12], task_id, event_type, json.dumps(details), time.time()),
            )
            self._conn.commit()

    def get_events(self, task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT event_type, details_json, timestamp FROM task_events WHERE task_id = ? ORDER BY timestamp ASC LIMIT ?",
                (task_id, limit),
            )
            return [
                {
                    "event_type": r["event_type"],
                    "details": json.loads(r["details_json"]),
                    "timestamp": r["timestamp"],
                }
                for r in cur.fetchall()
            ]

    def save_artifact(self, task_id: str, name: str, artifact_type: str, path: str, summary: str = "") -> str:
        artifact_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO task_artifacts (id, task_id, name, artifact_type, path, summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, task_id, name, artifact_type, path, summary, time.time()),
            )
            self._conn.commit()
        return artifact_id

    def list_artifacts(self, task_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, artifact_type, path, summary, created_at FROM task_artifacts WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_interrupted_tasks(self) -> List[BackgroundTask]:
        """Find tasks that were running, planning, or recovering when the process exited."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM background_tasks WHERE status IN ('running', 'planning', 'recovering')"
            )
            return [self._row_to_task(r) for r in cur.fetchall()]

    def _row_to_task(self, row: sqlite3.Row) -> BackgroundTask:
        return BackgroundTask.from_dict(
            {
                "id": row["id"],
                "title": row["title"],
                "user_request": row["user_request"],
                "status": row["status"],
                "agent_profile": row["agent_profile"],
                "plan": json.loads(row["plan_json"] or "[]"),
                "current_step_index": row["current_step_index"],
                "budget": json.loads(row["budget_json"] or "{}"),
                "context": json.loads(row["context_json"] or "{}"),
                "artifacts": json.loads(row["artifacts_json"] or "[]"),
                "approval_required": bool(row["approval_required"]),
                "verification_status": row["verification_status"],
                "steering_instructions": json.loads(row["steering_json"] or "[]"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "completed_at": row["completed_at"],
                "error": row["error"],
            }
        )

    # --- Worker Leases & Stale Recovery --------------------------------------

    def acquire_lease(
        self, task_id: str, worker_id: str, ttl_seconds: float = 30.0
    ) -> bool:
        """Acquire an exclusive lease for a worker to execute the task."""
        with self._lock:
            now = time.time()
            cur = self._conn.execute(
                "SELECT worker_id, expires_at FROM task_leases WHERE task_id = ?",
                (task_id,),
            )
            row = cur.fetchone()
            if row and row["expires_at"] > now and row["worker_id"] != worker_id:
                return False  # Active lease held by another worker

            self._conn.execute(
                """
                INSERT INTO task_leases (task_id, worker_id, acquired_at, expires_at, heartbeat_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    worker_id=excluded.worker_id,
                    acquired_at=excluded.acquired_at,
                    expires_at=excluded.expires_at,
                    heartbeat_at=excluded.heartbeat_at
                """,
                (task_id, worker_id, now, now + ttl_seconds, now),
            )
            self._conn.commit()
            return True

    def renew_lease(
        self, task_id: str, worker_id: str, ttl_seconds: float = 30.0
    ) -> bool:
        """Renew active lease and record worker heartbeat."""
        with self._lock:
            now = time.time()
            cur = self._conn.execute(
                "SELECT worker_id FROM task_leases WHERE task_id = ?", (task_id,)
            )
            row = cur.fetchone()
            if not row or row["worker_id"] != worker_id:
                return False

            self._conn.execute(
                "UPDATE task_leases SET expires_at = ?, heartbeat_at = ? WHERE task_id = ? AND worker_id = ?",
                (now + ttl_seconds, now, task_id, worker_id),
            )
            self._conn.commit()
            return True

    def release_lease(
        self, task_id: str, worker_id: Optional[str] = None
    ) -> None:
        """Release lease upon task completion, pause, or worker exit."""
        with self._lock:
            if worker_id:
                self._conn.execute(
                    "DELETE FROM task_leases WHERE task_id = ? AND worker_id = ?",
                    (task_id, worker_id),
                )
            else:
                self._conn.execute(
                    "DELETE FROM task_leases WHERE task_id = ?", (task_id,)
                )
            self._conn.commit()

    def get_lease(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Fetch active lease details for a task."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT task_id, worker_id, acquired_at, expires_at, heartbeat_at FROM task_leases WHERE task_id = ?",
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_stale_tasks(
        self, heartbeat_timeout_seconds: float = 60.0
    ) -> List[BackgroundTask]:
        """Find running tasks whose worker heartbeat has expired."""
        with self._lock:
            now = time.time()
            threshold = now - heartbeat_timeout_seconds
            cur = self._conn.execute(
                """
                SELECT t.* FROM background_tasks t
                JOIN task_leases l ON t.id = l.task_id
                WHERE t.status = 'running' AND l.heartbeat_at < ?
                """,
                (threshold,),
            )
            return [self._row_to_task(r) for r in cur.fetchall()]

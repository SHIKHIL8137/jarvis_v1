"""SQLite persistence store for the Agent Workforce."""

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
from openjarvis.workforce.models import (
    AgentHeartbeat,
    AgentManifest,
    AgentReport,
    AgentRuntimeState,
)

logger = logging.getLogger(__name__)


class WorkforceStore:
    """SQLite-backed persistent store for manifests, live states, heartbeats, reports, and events."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if not db_path:
            db_path = get_config_dir() / "workforce.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_manifests (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runtime_status (
                    agent_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_task_id TEXT,
                    active_model TEXT NOT NULL,
                    runtime_health TEXT NOT NULL DEFAULT 'healthy',
                    last_heartbeat REAL NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY (agent_id) REFERENCES agent_manifests(id)
                );

                CREATE TABLE IF NOT EXISTS agent_heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    workload INTEGER NOT NULL DEFAULT 0,
                    health TEXT NOT NULL DEFAULT 'healthy'
                );

                CREATE TABLE IF NOT EXISTS agent_reports (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    step_id TEXT,
                    report_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS team_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    agent_id TEXT,
                    data_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
            """)
            self._conn.commit()

    # --- Manifest Operations ---

    def save_manifest(self, manifest: AgentManifest) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_manifests (id, display_name, role, manifest_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    manifest.id,
                    manifest.display_name,
                    manifest.role,
                    json.dumps(manifest.to_dict()),
                    time.time(),
                ),
            )
            self._conn.commit()

    def get_manifest(self, agent_id: str) -> Optional[AgentManifest]:
        with self._lock:
            row = self._conn.execute(
                "SELECT manifest_json FROM agent_manifests WHERE id = ?", (agent_id,)
            ).fetchone()
            if row:
                return AgentManifest.from_dict(json.loads(row["manifest_json"]))
            return None

    def list_manifests(self) -> List[AgentManifest]:
        with self._lock:
            rows = self._conn.execute("SELECT manifest_json FROM agent_manifests ORDER BY id").fetchall()
            return [AgentManifest.from_dict(json.loads(r["manifest_json"])) for r in rows]

    # --- Status & Heartbeat Operations ---

    def update_runtime_status(
        self,
        agent_id: str,
        status: AgentRuntimeState,
        current_task_id: Optional[str] = None,
        active_model: str = "",
        runtime_health: str = "healthy",
        last_error: Optional[str] = None,
    ) -> None:
        with self._lock:
            now = time.time()
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_runtime_status
                    (agent_id, status, current_task_id, active_model, runtime_health, last_heartbeat, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    status.value,
                    current_task_id,
                    active_model,
                    runtime_health,
                    now,
                    last_error,
                ),
            )
            # Record historical heartbeat entry
            self._conn.execute(
                """
                INSERT INTO agent_heartbeats (agent_id, status, timestamp, workload, health)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    status.value,
                    now,
                    1 if current_task_id else 0,
                    runtime_health,
                ),
            )
            self._conn.commit()

    def get_runtime_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_runtime_status WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_all_runtime_statuses(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM agent_runtime_status ORDER BY agent_id").fetchall()
            return [dict(r) for r in rows]

    # --- Reports & Messages ---

    def save_report(self, report: AgentReport) -> str:
        with self._lock:
            rep_id = f"rep-{uuid.uuid4().hex[:10]}"
            self._conn.execute(
                """
                INSERT INTO agent_reports (id, agent_id, task_id, step_id, report_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rep_id,
                    report.agent_id,
                    report.task_id,
                    report.step_id,
                    json.dumps(report.to_dict()),
                    report.created_at,
                ),
            )
            self._conn.commit()
            return rep_id

    def list_reports(self, task_id: Optional[str] = None, agent_id: Optional[str] = None) -> List[AgentReport]:
        with self._lock:
            query = "SELECT report_json FROM agent_reports WHERE 1=1"
            params: list[Any] = []
            if task_id:
                query += " AND task_id = ?"
                params.append(task_id)
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            query += " ORDER BY created_at DESC"
            rows = self._conn.execute(query, tuple(params)).fetchall()
            return [AgentReport.from_dict(json.loads(r["report_json"])) for r in rows]

    def save_message(
        self,
        task_id: str,
        from_agent: str,
        to_agent: str,
        message_type: str,
        content: Dict[str, Any],
    ) -> str:
        with self._lock:
            msg_id = f"msg-{uuid.uuid4().hex[:10]}"
            self._conn.execute(
                """
                INSERT INTO agent_messages (id, task_id, from_agent, to_agent, message_type, content_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg_id,
                    task_id,
                    from_agent,
                    to_agent,
                    message_type,
                    json.dumps(content),
                    time.time(),
                ),
            )
            self._conn.commit()
            return msg_id

    def list_messages(self, task_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_messages WHERE task_id = ? ORDER BY timestamp ASC",
                (task_id,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "task_id": r["task_id"],
                    "from_agent": r["from_agent"],
                    "to_agent": r["to_agent"],
                    "message_type": r["message_type"],
                    "content": json.loads(r["content_json"]),
                    "timestamp": r["timestamp"],
                }
                for r in rows
            ]

    def log_team_event(self, event_type: str, data: Dict[str, Any], agent_id: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO team_events (id, event_type, agent_id, data_json, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"evt-{uuid.uuid4().hex[:10]}",
                    event_type,
                    agent_id,
                    json.dumps(data),
                    time.time(),
                ),
            )
            self._conn.commit()

    def list_team_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_events ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "agent_id": r["agent_id"],
                    "data": json.loads(r["data_json"]),
                    "timestamp": r["timestamp"],
                }
                for r in rows
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

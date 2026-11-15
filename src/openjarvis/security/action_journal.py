"""ActionJournal — SQLite-backed action history and compensation/undo engine."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import get_config_dir
from openjarvis.security.types import PermissionLevel

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    id: str
    tool_name: str
    parameters: Dict[str, Any]
    permission_level: str
    approval_id: Optional[str] = None
    target_path: Optional[str] = None
    backup_path: Optional[str] = None
    status: str = "executed"  # executed, undone, failed
    result_summary: str = ""
    reversible: bool = False
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ActionJournal:
    """Manages audit trail of privileged actions and pre-modification file snapshots for rollback."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if not db_path:
            db_path = get_config_dir() / "action_journal.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_dir = get_config_dir() / "backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS action_records (
                id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                parameters TEXT NOT NULL,
                permission_level TEXT NOT NULL,
                approval_id TEXT,
                target_path TEXT,
                backup_path TEXT,
                status TEXT NOT NULL,
                result_summary TEXT,
                reversible INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    def create_file_backup(self, file_path: str | Path, action_id: str) -> Optional[str]:
        """Create a pre-modification snapshot of the file if it exists."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return None
        backup_filename = f"{action_id}_{p.name}"
        backup_dest = self._backup_dir / backup_filename
        try:
            shutil.copy2(p, backup_dest)
            return str(backup_dest.resolve())
        except Exception as exc:
            logger.warning("Failed to create file backup for %s: %s", p, exc)
            return None

    def record_action(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        permission_level: PermissionLevel | str,
        approval_id: Optional[str] = None,
        target_path: Optional[str] = None,
        backup_path: Optional[str] = None,
        status: str = "executed",
        result_summary: str = "",
        reversible: bool = False,
    ) -> ActionRecord:
        action_id = uuid.uuid4().hex[:12]
        perm_str = (
            permission_level.value
            if isinstance(permission_level, PermissionLevel)
            else str(permission_level)
        )
        rec = ActionRecord(
            id=action_id,
            tool_name=tool_name,
            parameters=parameters,
            permission_level=perm_str,
            approval_id=approval_id,
            target_path=target_path,
            backup_path=backup_path,
            status=status,
            result_summary=result_summary,
            reversible=reversible,
            created_at=time.time(),
        )
        self._conn.execute(
            """
            INSERT INTO action_records (
                id, tool_name, parameters, permission_level, approval_id,
                target_path, backup_path, status, result_summary, reversible, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.id,
                rec.tool_name,
                json.dumps(rec.parameters),
                rec.permission_level,
                rec.approval_id,
                rec.target_path,
                rec.backup_path,
                rec.status,
                rec.result_summary,
                1 if rec.reversible else 0,
                rec.created_at,
            ),
        )
        self._conn.commit()
        return rec

    def get_action(self, action_id: str) -> Optional[ActionRecord]:
        row = self._conn.execute(
            """
            SELECT id, tool_name, parameters, permission_level, approval_id,
                   target_path, backup_path, status, result_summary, reversible, created_at
            FROM action_records WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        if not row:
            return None
        return ActionRecord(
            id=row[0],
            tool_name=row[1],
            parameters=json.loads(row[2]) if row[2] else {},
            permission_level=row[3],
            approval_id=row[4],
            target_path=row[5],
            backup_path=row[6],
            status=row[7],
            result_summary=row[8],
            reversible=bool(row[9]),
            created_at=row[10],
        )

    def list_actions(
        self,
        limit: int = 50,
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[ActionRecord]:
        query = (
            "SELECT id, tool_name, parameters, permission_level, approval_id, "
            "target_path, backup_path, status, result_summary, reversible, created_at "
            "FROM action_records WHERE 1=1"
        )
        params: list[Any] = []
        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, tuple(params)).fetchall()
        return [
            ActionRecord(
                id=r[0],
                tool_name=r[1],
                parameters=json.loads(r[2]) if r[2] else {},
                permission_level=r[3],
                approval_id=r[4],
                target_path=r[5],
                backup_path=r[6],
                status=r[7],
                result_summary=r[8],
                reversible=bool(r[9]),
                created_at=r[10],
            )
            for r in rows
        ]

    def undo_action(self, action_id: str) -> Dict[str, Any]:
        """Rollback or compensate an executed action."""
        action = self.get_action(action_id)
        if not action:
            return {"success": False, "error": f"Action {action_id} not found"}
        if action.status == "undone":
            return {"success": False, "error": f"Action {action_id} has already been undone"}
        if not action.reversible:
            return {
                "success": False,
                "error": f"Action {action_id} ({action.tool_name}) is marked as irreversible",
            }

        # Handle file restoration
        if action.target_path:
            target = Path(action.target_path)
            if action.backup_path and Path(action.backup_path).is_file():
                try:
                    shutil.copy2(action.backup_path, target)
                    self._update_status(action_id, "undone")
                    return {
                        "success": True,
                        "message": f"Restored {target} from backup snapshot",
                    }
                except Exception as exc:
                    return {"success": False, "error": f"Failed to restore file: {exc}"}
            elif not action.backup_path and target.is_file():
                try:
                    target.unlink()
                    self._update_status(action_id, "undone")
                    return {"success": True, "message": f"Deleted created file {target}"}
                except Exception as exc:
                    return {"success": False, "error": f"Failed to delete created file: {exc}"}

        self._update_status(action_id, "undone")
        return {"success": True, "message": f"Compensated action {action_id}"}

    def _update_status(self, action_id: str, new_status: str) -> None:
        self._conn.execute(
            "UPDATE action_records SET status = ? WHERE id = ?", (new_status, action_id)
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

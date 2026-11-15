"""ApprovalStore — SQLite-backed store for proactive agent action approvals.

Two tables:
- ``pending_actions``: actions proposed by the proactive agent awaiting user decision
- ``permission_memory``: remembered user decisions keyed by action pattern

Permission key format: ``"{action_type}:{fingerprint}"``
e.g. ``"email_delete:domain:noreply.github.com"``
     ``"sms_draft_reply:contact:+15551234567"``
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import get_config_dir

# ---------------------------------------------------------------------------
# Decision constants
# ---------------------------------------------------------------------------

DECISION_ALWAYS_APPROVE = "always_approve"
DECISION_ALWAYS_DENY = "always_deny"
DECISION_ASK = "ask"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_EXECUTING = "executing"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_DENIED = "denied"
STATUS_OUTCOME_UNKNOWN = "outcome_unknown"

# Tiers govern default ask behavior
TIER_TRIVIAL = "trivial"  # Execute immediately, no ask
TIER_LOW = "low"  # Ask once, then remember
TIER_MEDIUM = "medium"  # Ask each time unless remembered
TIER_HIGH = "high"  # Always ask, never auto-remember


@dataclass
class PendingAction:
    """An action proposed by the proactive agent."""

    id: str
    action_type: str
    description: str
    payload: Dict[str, Any]
    permission_key: str
    tier: str
    status: str = STATUS_PENDING
    created_at: str = ""
    expires_at: str = ""
    notification_sent: bool = False
    decision_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action_type": self.action_type,
            "description": self.description,
            "payload": json.dumps(self.payload),
            "permission_key": self.permission_key,
            "tier": self.tier,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "notification_sent": int(self.notification_sent),
            "decision_at": self.decision_at,
        }

    @classmethod
    def from_row(cls, row: tuple) -> PendingAction:
        (
            id_,
            action_type,
            description,
            payload_json,
            permission_key,
            tier,
            status,
            created_at,
            expires_at,
            notification_sent,
            decision_at,
        ) = row
        return cls(
            id=id_,
            action_type=action_type,
            description=description,
            payload=json.loads(payload_json) if payload_json else {},
            permission_key=permission_key,
            tier=tier,
            status=status,
            created_at=created_at or "",
            expires_at=expires_at or "",
            notification_sent=bool(notification_sent),
            decision_at=decision_at,
        )


@dataclass
class PermissionRule:
    """A remembered user decision for a permission pattern."""

    permission_key: str
    decision: str  # always_approve | always_deny
    times_approved: int = 0
    times_denied: int = 0
    last_updated: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "permission_key": self.permission_key,
            "decision": self.decision,
            "times_approved": self.times_approved,
            "times_denied": self.times_denied,
            "last_updated": self.last_updated,
            "notes": self.notes,
        }

    @classmethod
    def from_row(cls, row: tuple) -> PermissionRule:
        (
            permission_key,
            decision,
            times_approved,
            times_denied,
            last_updated,
            notes,
        ) = row
        return cls(
            permission_key=permission_key,
            decision=decision,
            times_approved=times_approved,
            times_denied=times_denied,
            last_updated=last_updated or "",
            notes=notes or "",
        )


class ApprovalStore:
    """SQLite store for proactive agent action approvals and permission memory."""

    def __init__(self, db_path: str = "") -> None:
        if not db_path:
            db_path = str(get_config_dir() / "approvals.db")
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._conn.commit()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                description TEXT NOT NULL,
                payload TEXT NOT NULL,
                permission_key TEXT NOT NULL,
                tier TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                notification_sent INTEGER NOT NULL DEFAULT 0,
                decision_at TEXT
            );

            CREATE TABLE IF NOT EXISTS permission_memory (
                permission_key TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                times_approved INTEGER NOT NULL DEFAULT 0,
                times_denied INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );
        """)

    # -- Pending actions -------------------------------------------------------

    def queue_action(
        self,
        action_type: str,
        description: str,
        payload: Dict[str, Any],
        permission_key: str,
        tier: str,
        ttl_hours: int = 24,
    ) -> PendingAction:
        """Create and persist a new pending action."""
        now = datetime.now(timezone.utc)
        action = PendingAction(
            id=uuid.uuid4().hex[:12],
            action_type=action_type,
            description=description,
            payload=payload,
            permission_key=permission_key,
            tier=tier,
            status=STATUS_PENDING,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=ttl_hours)).isoformat(),
        )
        self._conn.execute(
            """
            INSERT OR REPLACE INTO pending_actions
                (id, action_type, description, payload, permission_key,
                 tier, status, created_at, expires_at, notification_sent, decision_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action.id,
                action.action_type,
                action.description,
                json.dumps(action.payload),
                action.permission_key,
                action.tier,
                action.status,
                action.created_at,
                action.expires_at,
                int(action.notification_sent),
                action.decision_at,
            ),
        )
        self._conn.commit()
        return action

    def get_action(self, action_id: str) -> Optional[PendingAction]:
        row = self._conn.execute(
            "SELECT id, action_type, description, payload, permission_key, "
            "tier, status, created_at, expires_at, notification_sent, decision_at "
            "FROM pending_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        return PendingAction.from_row(row) if row else None

    def get_action_by_permission_key(self, permission_key: str) -> Optional[PendingAction]:
        row = self._conn.execute(
            "SELECT id, action_type, description, payload, permission_key, "
            "tier, status, created_at, expires_at, notification_sent, decision_at "
            "FROM pending_actions WHERE permission_key = ? ORDER BY created_at DESC LIMIT 1",
            (permission_key,),
        ).fetchone()
        return PendingAction.from_row(row) if row else None

    def list_pending(self) -> List[PendingAction]:
        """Return all non-expired pending actions."""
        now = datetime.now(timezone.utc).isoformat()
        rows = self._conn.execute(
            "SELECT id, action_type, description, payload, permission_key, "
            "tier, status, created_at, expires_at, notification_sent, decision_at "
            "FROM pending_actions WHERE status = ? AND expires_at > ? "
            "ORDER BY created_at",
            (STATUS_PENDING, now),
        ).fetchall()
        return [PendingAction.from_row(r) for r in rows]

    def list_approved(self) -> List[PendingAction]:
        """Return approved-but-not-yet-executed actions."""
        rows = self._conn.execute(
            "SELECT id, action_type, description, payload, permission_key, "
            "tier, status, created_at, expires_at, notification_sent, decision_at "
            "FROM pending_actions WHERE status = ? ORDER BY created_at",
            (STATUS_APPROVED,),
        ).fetchall()
        return [PendingAction.from_row(r) for r in rows]

    def claim_approval_for_execution(
        self,
        action_id: str,
        expected_permission_key: str,
        expected_action_type: str,
    ) -> Tuple[bool, Optional[PendingAction], str]:
        """Atomically claim an approved action for execution using conditional update.
        Guarantees that only ONE concurrent request can transition 'approved' -> 'executing'.
        """
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT id, action_type, description, payload, permission_key, "
                "tier, status, created_at, expires_at, notification_sent, decision_at "
                "FROM pending_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
            if not row:
                return False, None, f"Approval ID '{action_id}' not found."

            action = PendingAction.from_row(row)

            if action.status == STATUS_EXECUTING:
                return False, action, f"Approval ID '{action_id}' is currently executing in another concurrent request."
            if action.status == STATUS_EXECUTED:
                return False, action, f"Approval ID '{action_id}' has already been consumed (single-use constraint)."
            if action.status == STATUS_OUTCOME_UNKNOWN:
                return False, action, (
                    f"Approval ID '{action_id}' has status 'outcome_unknown' (request may have been delivered to remote service). "
                    "Reconciliation is required before any retry to prevent duplicate operations."
                )
            if action.status == STATUS_DENIED:
                return False, action, f"Approval ID '{action_id}' was denied."
            if action.status == STATUS_EXPIRED:
                return False, action, f"Approval ID '{action_id}' has expired."
            if action.status != STATUS_APPROVED:
                return False, action, f"Approval ID '{action_id}' is not approved (status: {action.status})."

            # Check expiration
            if action.expires_at and action.expires_at <= now_iso:
                self._conn.execute("UPDATE pending_actions SET status = ? WHERE id = ?", (STATUS_EXPIRED, action.id))
                self._conn.commit()
                return False, action, f"Approval ID '{action_id}' has expired."

            # Verify action type matches exactly
            if action.action_type != expected_action_type:
                return False, action, (
                    f"Approval action type mismatch: approved for '{action.action_type}', "
                    f"attempted '{expected_action_type}'."
                )

            # Verify permission key matches exactly (target + canonical payload hash)
            if action.permission_key != expected_permission_key:
                return False, action, (
                    "Approval permission mismatch: approval does not authorize this exact target and payload."
                )

            # Prevent agent self-approval
            if isinstance(action.payload, dict) and action.payload.get("_requester") == "agent" and not action.decision_at:
                return False, action, "Agent-generated requests cannot approve themselves."

            # ATOMIC TEST-AND-SET CONDITIONAL UPDATE
            cur = self._conn.execute(
                "UPDATE pending_actions SET status = ?, decision_at = ? WHERE id = ? AND status = ?",
                (STATUS_EXECUTING, now_iso, action.id, STATUS_APPROVED),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                # Concurrent race lost!
                return False, action, f"Approval ID '{action_id}' was claimed concurrently by another request."

            action.status = STATUS_EXECUTING
            return True, action, "Claimed"

    def claim_by_permission_key(
        self,
        permission_key: str,
        expected_action_type: str,
    ) -> Tuple[bool, Optional[PendingAction], str]:
        """Atomically find and claim an unconsumed approved action matching the exact permission key."""
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT id, action_type, description, payload, permission_key, "
                "tier, status, created_at, expires_at, notification_sent, decision_at "
                "FROM pending_actions WHERE permission_key = ? AND status = ? AND (expires_at > ? OR expires_at = '') "
                "ORDER BY created_at DESC LIMIT 1",
                (permission_key, STATUS_APPROVED, now_iso),
            ).fetchone()
            if not row:
                return False, None, "No active approved action for this permission key."

            action = PendingAction.from_row(row)
            return self.claim_approval_for_execution(action.id, permission_key, expected_action_type)

    def complete_approval_execution(
        self,
        action_id: str,
        success: bool,
        error: Optional[str] = None,
        pre_execution_failure: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        """Complete approval execution.
        - If success: transitions to 'executed'
        - If outcome_unknown: transitions to 'outcome_unknown' (NEVER restored to approved without reconciliation!)
        - If pre_execution_failure: restored to 'approved' (definitively never reached remote service)
        - Otherwise: transitions to 'failed'
        """
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            if success:
                new_status = STATUS_EXECUTED
            elif outcome_unknown:
                new_status = STATUS_OUTCOME_UNKNOWN
            elif pre_execution_failure:
                new_status = STATUS_APPROVED  # Do not permanently consume if failed before execution!
            else:
                new_status = STATUS_FAILED

            self._conn.execute(
                "UPDATE pending_actions SET status = ?, decision_at = ? WHERE id = ?",
                (new_status, now_iso, action_id),
            )
            self._conn.commit()

    def reconcile_approval(
        self,
        action_id: str,
        resolved_status: str,
        notes: str = "",
    ) -> Tuple[bool, str]:
        """Reconcile an approval in STATUS_OUTCOME_UNKNOWN to STATUS_EXECUTED, STATUS_FAILED, or STATUS_APPROVED."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status FROM pending_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
            if not row:
                return False, f"Approval ID '{action_id}' not found."
            current_status = row[1]
            if current_status != STATUS_OUTCOME_UNKNOWN:
                return False, (
                    f"Cannot reconcile approval with status '{current_status}'. "
                    f"Only '{STATUS_OUTCOME_UNKNOWN}' approvals require reconciliation."
                )
            if resolved_status not in (STATUS_EXECUTED, STATUS_FAILED, STATUS_APPROVED):
                return False, f"Invalid resolved status '{resolved_status}'. Must be executed, failed, or approved."

            now_iso = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE pending_actions SET status = ?, decision_at = ? WHERE id = ? AND status = ?",
                (resolved_status, now_iso, action_id, STATUS_OUTCOME_UNKNOWN),
            )
            self._conn.commit()
            return True, f"Approval '{action_id}' successfully reconciled to '{resolved_status}'."

    def update_status(
        self,
        action_id: str,
        status: str,
        *,
        notification_sent: Optional[bool] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if notification_sent is not None:
            self._conn.execute(
                "UPDATE pending_actions SET status = ?, decision_at = ?, "
                "notification_sent = ? WHERE id = ?",
                (status, now, int(notification_sent), action_id),
            )
        else:
            self._conn.execute(
                "UPDATE pending_actions SET status = ?, decision_at = ? WHERE id = ?",
                (status, now, action_id),
            )
        self._conn.commit()

    def approve_action(self, action_id: str) -> None:
        """Mark a pending action as approved."""
        self.update_status(action_id, STATUS_APPROVED)

    def deny_action(self, action_id: str) -> None:
        """Mark a pending action as denied."""
        self.update_status(action_id, STATUS_DENIED)

    def expire_stale(self) -> int:
        """Mark past-TTL pending actions as expired. Returns count."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE pending_actions SET status = ? "
            "WHERE status = ? AND expires_at <= ?",
            (STATUS_EXPIRED, STATUS_PENDING, now),
        )
        self._conn.commit()
        return cur.rowcount

    # -- Permission memory -----------------------------------------------------

    def get_permission(self, permission_key: str) -> Optional[PermissionRule]:
        row = self._conn.execute(
            "SELECT permission_key, decision, times_approved, times_denied, "
            "last_updated, notes FROM permission_memory WHERE permission_key = ?",
            (permission_key,),
        ).fetchone()
        return PermissionRule.from_row(row) if row else None

    def set_permission(
        self,
        permission_key: str,
        decision: str,
        *,
        approved: bool = False,
        notes: str = "",
    ) -> None:
        """Upsert a permission rule, incrementing the relevant counter."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_permission(permission_key)
        if existing:
            times_approved = existing.times_approved + (1 if approved else 0)
            times_denied = existing.times_denied + (0 if approved else 1)
            self._conn.execute(
                "UPDATE permission_memory SET decision = ?, times_approved = ?, "
                "times_denied = ?, last_updated = ?, notes = ? "
                "WHERE permission_key = ?",
                (
                    decision,
                    times_approved,
                    times_denied,
                    now,
                    notes or existing.notes,
                    permission_key,
                ),
            )
        else:
            self._conn.execute(
                "INSERT INTO permission_memory "
                "(permission_key, decision, times_approved, times_denied, "
                "last_updated, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    permission_key,
                    decision,
                    1 if approved else 0,
                    0 if approved else 1,
                    now,
                    notes,
                ),
            )
        self._conn.commit()

    def clear_permission(self, permission_key: str) -> None:
        self._conn.execute(
            "DELETE FROM permission_memory WHERE permission_key = ?",
            (permission_key,),
        )
        self._conn.commit()

    def list_permissions(self) -> List[PermissionRule]:
        rows = self._conn.execute(
            "SELECT permission_key, decision, times_approved, times_denied, "
            "last_updated, notes FROM permission_memory ORDER BY last_updated DESC"
        ).fetchall()
        return [PermissionRule.from_row(r) for r in rows]

    def get_seen_ids(self) -> set:
        """Return all doc_ids and message_ids previously queued (any status).

        Used by ``ProactiveAgent`` to skip items it has already proposed so
        they don't resurface on every run while still unread/unanswered.
        """
        seen: set = set()
        rows = self._conn.execute("SELECT payload FROM pending_actions").fetchall()
        for (payload_json,) in rows:
            try:
                payload = json.loads(payload_json) if payload_json else {}
            except json.JSONDecodeError:
                continue
            doc_id = payload.get("doc_id", "")
            if doc_id:
                seen.add(doc_id)
            msg_id = payload.get("message_id", "")
            if msg_id:
                seen.add(f"gmail:{msg_id}")
        return seen

    def close(self) -> None:
        self._conn.close()


# --- Chat-agent confirmation bridge -----------------------------------------
import re  # noqa: E402
# shortcut: safe-command check is a prefix allowlist on single, metachar-free
# commands, not a shell parser; widen or replace if prompts get too noisy.
_SAFE_PREFIXES = (
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "findstr", "grep", "rg",
    "git status", "git diff", "git log", "git branch", "git show",
    "pytest", "python -m pytest", "python -m unittest", "python3 -m unittest", "uv run pytest",
    "ruff", "python --version", "go test", "go vet", "go build", "dotnet test", "dotnet build",
    "mvn test", "gradle test", "pnpm test", "yarn test", "npm run check", "make test",
    "node --version", "npm test", "npm run test", "npm run lint",
    "npm run build", "npm run typecheck", "npx tsc", "tsc", "cargo check",
    "cargo test",
)
_SHELL_META = set(";&|<>`$()\n\r%^")


_CD = r'^cd\s+(?:/d\s+)?("[^"&|;<>`$()%^]*"|[^\s&|;<>`$()%^]+)\s*&&\s*(.+)$'
# Risky even inside a granted folder: deleting, publishing, installing,
# downloading/executing remote code, system-level changes.
_SENSITIVE = re.compile(
    r"\b(rm|del|erase|rmdir|rd|remove-item|format|diskpart|shutdown|taskkill|reg|sudo|chmod|chown)\b"
    r"|\bgit\s+(push|reset\s+--hard|clean|rebase|branch\s+-d|checkout\s+--)"
    r"|\b(pip3?|npm|pnpm|yarn|winget|choco|scoop|apt|brew)\s+(install|add|i|uninstall|remove)\b"
    r"|\b(uv\s+(pip|add)|cargo\s+install|go\s+install)\b"
    r"|\b(curl|wget|iwr|invoke-webrequest|invoke-expression|iex|certutil|bitsadmin)\b"
    r"|\|\s*(sh|bash|powershell|pwsh|cmd)\b",
    re.I,
)


def _shell_args(prompt: str):
    """The args dict of a shell_exec confirmation prompt, else None."""
    import ast

    if "'shell_exec'" not in prompt or " with args " not in prompt:
        return None
    try:
        args = ast.literal_eval(prompt.split(" with args ", 1)[1].rsplit("?", 1)[0])
    except (ValueError, SyntaxError):
        return None
    return args if isinstance(args, dict) else None


def _command_folder(args: dict) -> str:
    """Folder a shell command runs in: working_dir, or a leading ``cd <dir> &&``."""
    wd = str(args.get("working_dir") or "").strip()
    if wd:
        return wd
    m = re.match(_CD, str(args.get("command", "")).strip(), re.I)
    return m.group(1).strip('"') if m else ""


def _is_safe_command(prompt: str) -> bool:
    """True for a lone read-only/test/build shell_exec command."""
    args = _shell_args(prompt)
    if args is None:
        return False
    cmd = str(args.get("command", "")).strip().lower()
    # Models often write "cd <dir> && <cmd>"; judge only the <cmd> part.
    cd = re.match(_CD, cmd)
    if cd:
        cmd = cd.group(2).strip()
    if not cmd or _SHELL_META & set(cmd):
        return False
    return any(cmd == p or cmd.startswith(p + " ") for p in _SAFE_PREFIXES)


def ui_confirm(prompt: str, timeout: float = 600.0) -> bool:
    """Confirm a sensitive tool call: safe ones pass, the rest wait for the GUI bell.

    Blocks the agent's worker thread until the user approves/denies via
    ``/v1/approvals``; no answer within *timeout* seconds counts as a denial.
    """
    import time

    from openjarvis.tools.folder_grants import is_granted

    if _is_safe_command(prompt):
        return True
    m = re.search(r"tool '(desktop_\w+)'", prompt)
    desktop = bool(m)
    if desktop:
        from openjarvis.tools.desktop import control_enabled

        if control_enabled():
            return True
    args = _shell_args(prompt) or {}
    folder = _command_folder(args)
    # A granted folder is trusted for everything except the risky set above.
    if folder and is_granted(folder) and not _SENSITIVE.search(str(args.get("command", ""))):
        return True
    store = ApprovalStore()
    try:
        action = store.queue_action(
            "tool_call", prompt,
            {"folder": folder} if folder else ({"desktop": True} if desktop else {}),
            "tool_call", TIER_HIGH, ttl_hours=1,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = store.get_action(action.id)
            if current and current.status != STATUS_PENDING:
                return current.status in (STATUS_APPROVED, STATUS_EXECUTED)
            time.sleep(1.0)
        store.update_status(action.id, STATUS_EXPIRED)
        return False
    finally:
        store.close()


__all__ = [
    "ui_confirm",
    "ApprovalStore",
    "PendingAction",
    "PermissionRule",
    "DECISION_ALWAYS_APPROVE",
    "DECISION_ALWAYS_DENY",
    "DECISION_ASK",
    "STATUS_PENDING",
    "STATUS_APPROVED",
    "STATUS_DENIED",
    "STATUS_EXPIRED",
    "STATUS_EXECUTED",
    "TIER_TRIVIAL",
    "TIER_LOW",
    "TIER_MEDIUM",
    "TIER_HIGH",
]

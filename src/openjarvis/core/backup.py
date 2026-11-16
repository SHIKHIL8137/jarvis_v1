"""Production database backup, restore, and integrity verification utilities."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def verify_sqlite_integrity(db_path: Path | str) -> bool:
    """Verify that a SQLite database file exists and passes PRAGMA integrity_check."""
    p = Path(db_path)
    if not p.is_file():
        return False
    try:
        conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            row = cursor.fetchone()
            return row is not None and row[0] == "ok"
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Integrity check failed for %s: %s", db_path, exc)
        return False


def backup_sqlite(source_path: Path | str, target_path: Path | str) -> bool:
    """Safely back up an active SQLite database using the SQLite Online Backup API.

    Guarantees a clean, point-in-time snapshot without corruption from concurrent writes.
    """
    src = Path(source_path)
    tgt = Path(target_path)
    if not src.is_file():
        logger.error("Source database does not exist: %s", src)
        return False

    tgt.parent.mkdir(parents=True, exist_ok=True)
    temp_tgt = tgt.with_suffix(f"{tgt.suffix}.tmp-{time.time_ns()}")

    try:
        src_conn = sqlite3.connect(str(src))
        dest_conn = sqlite3.connect(str(temp_tgt))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()

        # Atomic replace
        if temp_tgt.exists():
            if tgt.exists():
                tgt.unlink()
            os.replace(temp_tgt, tgt)

        return verify_sqlite_integrity(tgt)
    except Exception as exc:
        logger.exception("Failed to backup sqlite database %s to %s: %s", src, tgt, exc)
        if temp_tgt.exists():
            try:
                temp_tgt.unlink()
            except OSError:
                pass
        return False


def restore_sqlite(backup_path: Path | str, target_path: Path | str) -> bool:
    """Restore a SQLite database from a verified backup snapshot."""
    src = Path(backup_path)
    tgt = Path(target_path)
    if not src.is_file():
        logger.error("Backup file does not exist: %s", src)
        return False

    if not verify_sqlite_integrity(src):
        logger.error("Backup snapshot failed integrity check; refusing to restore: %s", src)
        return False

    tgt.parent.mkdir(parents=True, exist_ok=True)
    temp_tgt = tgt.with_suffix(f"{tgt.suffix}.restore-tmp-{time.time_ns()}")

    try:
        shutil.copy2(src, temp_tgt)
        if not verify_sqlite_integrity(temp_tgt):
            raise RuntimeError("Restored temporary database failed integrity check")

        if tgt.exists():
            tgt.unlink()
        os.replace(temp_tgt, tgt)
        return True
    except Exception as exc:
        logger.exception("Failed to restore sqlite database from %s to %s: %s", src, tgt, exc)
        if temp_tgt.exists():
            try:
                temp_tgt.unlink()
            except OSError:
                pass
        return False


def create_system_backup(config_dir: Path | str, backup_dest_dir: Path | str) -> Dict[str, Any]:
    """Create a consolidated timestamped archive of all Jarvis state files."""
    cfg = Path(config_dir)
    dest_dir = Path(backup_dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    archive_name = f"openjarvis_backup_{timestamp_str}.zip"
    archive_path = dest_dir / archive_name

    state_files = [
        "tasks.db",
        "scheduler.db",
        "approvals.db",
        "routines.db",
        "action_journal.db",
        "memory_facts.jsonl",
        "config.toml",
    ]

    backed_up: List[str] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in state_files:
            file_path = cfg / fname
            if file_path.is_file():
                if file_path.suffix == ".db":
                    # Snapshot SQLite database cleanly before adding to zip
                    temp_snap = dest_dir / f"temp_{fname}"
                    if backup_sqlite(file_path, temp_snap):
                        zf.write(temp_snap, arcname=fname)
                        backed_up.append(fname)
                    if temp_snap.exists():
                        temp_snap.unlink()
                else:
                    zf.write(file_path, arcname=fname)
                    backed_up.append(fname)

    return {
        "success": True,
        "archive_path": str(archive_path.resolve()),
        "files": backed_up,
        "timestamp": time.time(),
        "size_bytes": archive_path.stat().st_size if archive_path.exists() else 0,
    }


def restore_system_backup(archive_path: Path | str, target_config_dir: Path | str) -> Dict[str, Any]:
    """Restore Jarvis state from a consolidated backup archive."""
    arc = Path(archive_path)
    target_dir = Path(target_config_dir)
    if not arc.is_file():
        return {"success": False, "error": f"Backup archive not found: {arc}"}

    target_dir.mkdir(parents=True, exist_ok=True)
    restored: List[str] = []

    try:
        with zipfile.ZipFile(arc, "r") as zf:
            for member in zf.namelist():
                # Prevent directory traversal attacks from malicious archives
                if ".." in member or member.startswith("/") or member.startswith("\\"):
                    continue
                extracted_path = target_dir / member
                zf.extract(member, path=target_dir)
                if extracted_path.suffix == ".db":
                    if not verify_sqlite_integrity(extracted_path):
                        raise RuntimeError(f"Extracted database failed integrity check: {member}")
                restored.append(member)

        return {
            "success": True,
            "restored_files": restored,
            "target_dir": str(target_dir.resolve()),
            "timestamp": time.time(),
        }
    except Exception as exc:
        logger.exception("Failed to restore backup archive %s: %s", arc, exc)
        return {"success": False, "error": str(exc), "restored_files": restored}

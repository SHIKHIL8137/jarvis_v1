import os
import tempfile
import pytest
from pathlib import Path

from openjarvis.security.action_journal import ActionJournal
from openjarvis.security.types import PermissionLevel


@pytest.fixture
def temp_journal_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_action_journal_record_and_list(temp_journal_dir):
    db_path = temp_journal_dir / "journal.db"
    journal = ActionJournal(db_path=db_path)

    # Record action
    rec = journal.record_action(
        tool_name="shell_exec",
        parameters={"command": "dir"},
        permission_level=PermissionLevel.SAFE_LOCAL,
        status="executed",
        result_summary="Listed files",
        reversible=False,
    )

    assert rec.id is not None
    assert rec.tool_name == "shell_exec"
    assert rec.permission_level == "safe_local"

    # List actions
    actions = journal.list_actions()
    assert len(actions) == 1
    assert actions[0].id == rec.id

    journal.close()


def test_action_journal_file_undo(temp_journal_dir):
    db_path = temp_journal_dir / "journal.db"
    journal = ActionJournal(db_path=db_path)

    # 1. Test modifying an existing file with undo (restoration)
    test_file = temp_journal_dir / "sample.txt"
    test_file.write_text("Original content", encoding="utf-8")

    # Create backup before modification
    backup_path = journal.create_file_backup(test_file, "act1")
    assert backup_path is not None
    assert Path(backup_path).read_text(encoding="utf-8") == "Original content"

    # Modify file
    test_file.write_text("Modified content", encoding="utf-8")

    rec1 = journal.record_action(
        tool_name="file_write",
        parameters={"path": str(test_file), "mode": "write"},
        permission_level=PermissionLevel.REVERSIBLE_LOCAL,
        target_path=str(test_file),
        backup_path=backup_path,
        status="executed",
        reversible=True,
    )

    # Undo modification
    undo_res = journal.undo_action(rec1.id)
    assert undo_res["success"] is True
    assert test_file.read_text(encoding="utf-8") == "Original content"

    # 2. Test undoing a newly created file (deletion)
    new_file = temp_journal_dir / "new.txt"
    new_file.write_text("Brand new file", encoding="utf-8")

    rec2 = journal.record_action(
        tool_name="file_write",
        parameters={"path": str(new_file)},
        permission_level=PermissionLevel.REVERSIBLE_LOCAL,
        target_path=str(new_file),
        backup_path=None,  # Didn't exist previously
        status="executed",
        reversible=True,
    )

    undo_res2 = journal.undo_action(rec2.id)
    assert undo_res2["success"] is True
    assert not new_file.exists()

    # 3. Test non-reversible action rejection
    rec3 = journal.record_action(
        tool_name="shell_exec",
        parameters={"command": "rmdir /s /q temp"},
        permission_level=PermissionLevel.IRREVERSIBLE,
        status="executed",
        reversible=False,
    )
    undo_res3 = journal.undo_action(rec3.id)
    assert undo_res3["success"] is False
    assert "irreversible" in undo_res3["error"]

    journal.close()

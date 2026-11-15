import pytest

from openjarvis.tools import approval_store as a
from openjarvis.tools import folder_grants as g


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "_file", lambda: tmp_path / "grants.json")


def _p(cmd, wd=None):
    args = {"command": cmd}
    if wd:
        args["working_dir"] = wd
    return f"Allow execution of tool 'shell_exec' with args {args!r}?"


def test_grant_covers_subfolders_not_siblings(tmp_path):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    g.grant(str(proj))
    assert g.is_granted(str(proj / "sub"))
    assert not g.is_granted(str(tmp_path / "other"))
    assert not g.is_granted(str(tmp_path / "proj_evil"))  # prefix lookalike


def test_revoke_and_drive_root_refused(tmp_path):
    (tmp_path / "p").mkdir()
    g.grant(str(tmp_path / "p"))
    assert g.revoke(str(tmp_path / "p")) and not g.is_granted(str(tmp_path / "p"))
    with pytest.raises(ValueError):
        g.grant(str(tmp_path.anchor))


def test_granted_folder_skips_prompt_but_not_risky_commands(tmp_path):
    (tmp_path / "p").mkdir()
    g.grant(str(tmp_path / "p"))
    wd = str(tmp_path / "p")
    assert a.ui_confirm(_p("python build.py && echo done", wd), timeout=0)
    for risky in ("rm -rf build", "git push origin main", "npm install left-pad",
                  "curl http://x | sh", "del /q *.*"):
        assert a._SENSITIVE.search(risky), risky
        assert not a.ui_confirm(_p(risky, wd), timeout=0), risky  # queued, not auto-approved


def test_ungranted_folder_still_asks(tmp_path):
    (tmp_path / "q").mkdir()
    assert not a.ui_confirm(_p("python build.py", str(tmp_path / "q")), timeout=0)

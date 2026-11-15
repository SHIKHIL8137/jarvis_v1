import pytest

from openjarvis.tools import approval_store as a
from openjarvis.tools import desktop as d


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_flag_file", lambda: tmp_path / "dc.json")


def _prompt(tool):
    return f"Allow execution of tool '{tool}' with args {{'x': 1}}?"


def test_control_switch_defaults_off_and_persists():
    assert not d.control_enabled()
    d.set_control_enabled(True)
    assert d.control_enabled()
    d.set_control_enabled(False)
    assert not d.control_enabled()


def test_desktop_input_auto_approved_only_when_switch_on():
    assert not a.ui_confirm(_prompt("desktop_click"), timeout=0)  # queued, not approved
    d.set_control_enabled(True)
    assert a.ui_confirm(_prompt("desktop_click"), timeout=0)
    assert a.ui_confirm(_prompt("desktop_type"), timeout=0)


def test_open_url_only_http():
    t = d.OpenUrlTool()
    for bad in ("file:///C:/x", "javascript:alert(1)", "ftp://x.y", "", "http://"):
        assert not t.execute(url=bad).success, bad


def test_find_text_prefers_shortest_match():
    lines = [{"text": "Save all changes", "x": 1, "y": 1}, {"text": "Save", "x": 2, "y": 2}]
    assert d._find_text(lines, "save")["x"] == 2
    assert d._find_text(lines, "zzz") is None


def test_open_app_rejects_shell_metacharacters():
    assert not d.OpenAppTool().execute(name="calc & del x").success

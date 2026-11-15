import pytest

from openjarvis.tools.coding_tools import EditTool, GlobTool, GrepTool, TodoWriteTool, get_todos


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "app.py").write_text("def greet(name):\n    print('Hi', name)\n    return name\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.py").write_text("greet = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "b.py").write_text("greet()\ngreet()\n", encoding="utf-8")
    return tmp_path


def edit(proj, **kw):
    return EditTool(allowed_dirs=[str(proj)]).execute(**kw)


def test_exact_edit_and_diff(proj):
    r = edit(proj, path="app.py", old_string="print('Hi', name)", new_string="print('Hello', name)")
    assert r.success and "Hello" in (proj / "app.py").read_text() and "+    print('Hello'" in r.content


def test_ambiguous_match_is_refused_unless_replace_all(proj):
    r = edit(proj, path="pkg/b.py", old_string="greet()", new_string="hello()")
    assert not r.success and "2 places" in r.content
    r = edit(proj, path="pkg/b.py", old_string="greet()", new_string="hello()", replace_all=True)
    assert r.success and (proj / "pkg" / "b.py").read_text() == "hello()\nhello()\n"


def test_lenient_matching_typographic_and_trailing_whitespace(proj):
    (proj / "t.txt").write_text("say “hi”   \nnext\n", encoding="utf-8")
    r = edit(proj, path="t.txt", old_string='say "hi"', new_string="say hello")
    assert r.success and "typographic" in r.content
    (proj / "w.txt").write_text("a = 1   \nb = 2\n", encoding="utf-8")
    r = edit(proj, path="w.txt", old_string="a = 1\nb = 2", new_string="a = 9\nb = 2")
    assert r.success and (proj / "w.txt").read_text() == "a = 9\nb = 2\n"


def test_crlf_files_keep_their_line_endings(proj):
    (proj / "c.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    r = edit(proj, path="c.txt", old_string="one\ntwo", new_string="1\n2")
    assert r.success and (proj / "c.txt").read_bytes() == b"1\r\n2\r\nthree\r\n"


def test_edit_guards(proj, tmp_path_factory):
    assert not edit(proj, path="app.py", old_string="x", new_string="x").success       # identical
    assert not edit(proj, path="app.py", old_string="", new_string="x").success         # empty
    assert not edit(proj, path="app.py", old_string="nope", new_string="y").success     # not found
    outside = tmp_path_factory.mktemp("outside") / "o.txt"
    outside.write_text("secret")
    r = edit(proj, path=str(outside), old_string="secret", new_string="x")
    assert not r.success and "outside allowed" in r.content and outside.read_text() == "secret"


def test_camel_case_aliases_accepted(proj):
    assert edit(proj, path="app.py", oldString="return name", newString="return None").success


def test_grep_skips_junk_dirs_and_respects_limit(proj):
    g = GrepTool(allowed_dirs=[str(proj)])
    out = g.execute(pattern="greet", path=str(proj)).content
    assert "app.py:1" in out and "node_modules" not in out and out.count("b.py") == 2
    assert "stopped at 1" in g.execute(pattern="greet", path=str(proj), limit=1).content
    assert g.execute(pattern="GREET", path=str(proj), case_sensitive=False, include="*.py").success
    assert g.execute(pattern="zzz-none", path=str(proj)).content == "No matches."
    assert not g.execute(pattern="(", path=str(proj)).success  # bad regex reported, not raised


def test_glob_finds_files_and_skips_node_modules(proj):
    out = GlobTool(allowed_dirs=[str(proj)]).execute(pattern="**/*.py", path=str(proj)).content
    assert "app.py" in out and "b.py" in out and "node_modules" not in out


def test_todo_write_replaces_list_and_validates():
    t = TodoWriteTool()
    r = t.execute(todos=[{"content": "plan", "status": "completed"}, {"content": "build", "status": "in_progress"}])
    assert r.success and "1/2 done" in r.content and get_todos()[1]["status"] == "in_progress"
    assert t.execute(todos='[{"content": "x", "status": "weird"}]').success  # stringified list repaired
    assert get_todos()[0]["status"] == "pending"                           # unknown status -> pending
    assert not t.execute(todos=[{"content": "", "status": "pending"}]).success

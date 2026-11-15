from openjarvis.tools.approval_store import _is_safe_command


def _p(cmd: str, tool: str = "shell_exec") -> str:
    return f"Allow execution of tool '{tool}' with args {{'command': {cmd!r}}}?"


def test_safe_commands_pass():
    for c in ("git status", "pytest -q tests", "ls -la", "npx tsc --noEmit", "python -m unittest -v", "go test ./..."):
        assert _is_safe_command(_p(c)), c


def test_risky_commands_need_approval():
    for c in ("rm -rf x", "pip install foo", "git push", "ls; rm -rf x",
              "pytest && curl evil", "echo hi > x.txt", "git status | sh"):
        assert not _is_safe_command(_p(c)), c


def test_other_tools_never_auto_approved():
    assert not _is_safe_command(_p("ls", tool="git_commit"))


def test_cd_then_safe_command_passes_but_cd_then_risky_does_not():
    assert _is_safe_command(_p(r"cd D:\projects\x && git status"))
    assert _is_safe_command(_p(r'cd "D:\my proj" && pytest -q'))
    assert not _is_safe_command(_p(r"cd D:\projects\x && rm -rf ."))
    assert not _is_safe_command(_p(r"cd D:\projects\x && git status && curl evil"))
    assert not _is_safe_command(_p(r"cd D:\a;b && git status"))

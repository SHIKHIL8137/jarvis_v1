import json

import pytest

from openjarvis.agents._stubs import AgentResult
from openjarvis.coding import commands, compaction, goal, roles, runtime
from openjarvis.core.types import Message, Role, ToolResult


def TR(name="shell_exec", ok=True, text="ok"):
    return ToolResult(tool_name=name, content=text, success=ok)


class FakeAgent:
    """Returns a scripted result per round and records the prompts and the history it was given."""

    chosen_model = "fake/model"

    def __init__(self, results):
        self.results, self.prompts, self.seen = list(results), [], []
        self._engine, self._model = None, "fake/model"

    def run(self, prompt, context=None):
        self.prompts.append(prompt)
        self.seen.append(len(context.conversation.messages) if context else 0)
        return self.results.pop(0)


def judge_of(*verdicts):
    it = iter(verdicts)
    return lambda prompt: json.dumps(next(it))


def R(text="did work", tools=1):
    return AgentResult(content=text, tool_results=[TR() for _ in range(tools)])


# --- goal loop ---------------------------------------------------------------


def test_goal_completes_after_reviewer_asks_to_continue_once():
    agent = FakeAgent([R("first"), R("second")])
    events = []
    res = goal.run_goal(
        "add feature", agent=agent, emit=lambda *a: events.append(a[0]),
        judge=judge_of({"verdict": "continue", "reason": "tests not run", "next_step": "run pytest"},
                       {"verdict": "done", "reason": "pytest: 4 passed"}),
    )
    assert res.status == "complete" and res.rounds == 2 and "4 passed" in res.reason
    assert "Reviewer feedback: tests not run Next step: run pytest" in agent.prompts[1]
    assert agent.seen == [0, 2]  # round 2 sees round 1's exchange
    assert events.count("round") == 2 and events.count("verdict") == 2


def test_blocked_stops_and_surfaces_the_reason():
    res = goal.run_goal("deploy", agent=FakeAgent([R()]),
                        judge=judge_of({"verdict": "blocked", "reason": "need the API key"}))
    assert res.status == "blocked" and res.reason == "need the API key"


def test_stall_detection_pauses_after_three_rounds_without_progress():
    agent = FakeAgent([R(tools=0) for _ in range(6)])
    res = goal.run_goal("x", agent=agent, judge=lambda p: json.dumps({"verdict": "continue", "reason": "still nothing"}))
    assert res.status == "paused" and "Not making progress" in res.reason and res.rounds == 3


def test_repeating_the_same_reason_counts_as_a_stall_even_with_tool_calls():
    agent = FakeAgent([R() for _ in range(6)])
    res = goal.run_goal("x", agent=agent, judge=lambda p: json.dumps({"verdict": "continue", "reason": "Tests fail!"}))
    assert res.status == "paused" and res.rounds == 4  # first reason is new, then 3 repeats


def test_round_limit():
    agent = FakeAgent([R() for _ in range(5)])
    it = iter(range(100))
    res = goal.run_goal("x", agent=agent, max_rounds=3,
                        judge=lambda p: json.dumps({"verdict": "continue", "reason": f"step {next(it)}"}))
    assert res.status == "paused" and "limit of 3" in res.reason


def test_unusable_reviewer_pauses_instead_of_guessing_done():
    res = goal.run_goal("x", agent=FakeAgent([R()]), judge=lambda p: "It looks fine to me!")
    assert res.status == "paused" and "no usable verdict" in res.reason


def test_agent_error_is_reported_not_raised():
    class Boom(FakeAgent):
        def run(self, *a, **k):
            raise RuntimeError("model unavailable")

    res = goal.run_goal("x", agent=Boom([]), judge=judge_of())
    assert res.status == "paused" and "model unavailable" in res.reason


def test_verdict_parsing_is_tolerant():
    assert goal.parse_verdict('```json\n{"verdict":"done","reason":"ok"}\n```')["verdict"] == "done"
    assert goal.parse_verdict('Sure: {"verdict": "continue", "reason": "r", "next_step": "n"} thanks')["next_step"] == "n"
    assert goal.parse_verdict('{"verdict":"maybe"}') is None and goal.parse_verdict("") is None


def test_evidence_includes_failures_and_todos():
    res = AgentResult(content="all good",
                      tool_results=[TR("shell_exec", False, "AssertionError"), TR("edit", True, "Edited")])
    ev = goal.build_evidence(res, [{"content": "write tests", "status": "in_progress"}])
    assert "[FAILED]" in ev and "AssertionError" in ev and "[~] write tests" in ev and "all good" in ev


# --- commands ----------------------------------------------------------------


@pytest.mark.parametrize("text,name", [
    ("/goal fix the build", "goal"), ("/plan add auth", "plan"), ("/review", "review"),
    ("/init focus on tests", "init"),
])
def test_slash_commands(text, name):
    assert commands.parse_command(text).name == name


def test_natural_build_requests_start_goals_but_questions_and_chat_do_not():
    assert commands.parse_command(r"Implement rate limiting in the API at D:\projects\app and add tests").auto
    assert commands.parse_command("jarvis, please fix the failing tests in my project").name == "goal"
    assert commands.parse_command("how do I implement rate limiting in my project?") is None
    assert commands.parse_command("hi there") is None
    assert commands.parse_command("fix the failing tests in my project please", auto_enabled=False) is None


# --- roles -------------------------------------------------------------------


def test_read_only_roles_have_no_write_or_shell_tools():
    risky = {"edit", "file_write", "apply_patch", "shell_exec", "git_commit", "code_interpreter", "subagent"}
    for name in ("explore", "plan", "review"):
        assert not (set(roles.ROLES[name].tools) & risky), name
    assert {"edit", "shell_exec", "subagent"} <= set(roles.ROLES["build"].tools)
    assert "subagent" not in roles.ROLES["general"].tools  # children cannot spawn children


def test_subagent_tool_respects_depth_limit_and_unknown_roles():
    from openjarvis.tools.subagent import SubagentTool

    assert not SubagentTool(depth=1).execute(agent="explore", prompt="x").success  # default limit is 1
    r = SubagentTool().execute(agent="build", prompt="x")
    assert not r.success and "Choose one of" in r.content
    assert not SubagentTool().execute(agent="explore", prompt="").success


def test_build_tools_omit_subagent_at_depth_limit():
    import importlib

    # The shared test fixture clears the registries, so register the tools again.
    from openjarvis.tools import coding_tools, subagent
    importlib.reload(coding_tools)
    importlib.reload(subagent)

    def names(d):
        return {t.spec.name for t in roles.build_tools(roles.ROLES["build"].tools, d)}

    assert "subagent" in names(0) and "subagent" not in names(1)


# --- instructions and compaction ----------------------------------------------


def test_instruction_discovery_nearest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "get_config_dir", lambda: tmp_path / "global")
    (tmp_path / "global").mkdir()
    (tmp_path / "AGENTS.md").write_text("root rules")
    sub = tmp_path / "svc"
    sub.mkdir()
    (sub / "CLAUDE.md").write_text("service rules")
    (tmp_path / "global" / "AGENTS.md").write_text("global rules")
    out = runtime.discover_instructions(sub)
    assert out.index("service rules") < out.index("root rules") < out.index("global rules")
    assert runtime.project_dir_from(f"work in {sub} please") == sub


def test_compaction_replaces_old_turns_with_a_checkpoint_and_keeps_the_tail():
    class Eng:
        def generate(self, msgs, **kw):
            assert "## Objective" in msgs[0].content  # the structured template is used
            return {"content": "## Objective\n- build it"}

    msgs = [Message(role=Role.USER, content=f"m{i}") for i in range(6)]
    out = compaction.compact(Eng(), "m", msgs)
    assert len(out) == 1 + compaction.KEEP_TAIL and "build it" in out[0].content and out[-1].content == "m5"
    assert compaction.compact(Eng(), "m", msgs[:2]) == msgs[:2]  # too short: untouched


def test_tool_calls_are_reported_as_progress():
    from openjarvis.core.events import EventType

    seen = []
    bus = runtime.tool_progress_bus(lambda *a: seen.append(a))
    bus.publish(EventType.TOOL_CALL_START, {"tool": "shell_exec", "arguments": {"command": "python -m unittest\n-v"}})
    bus.publish(EventType.TOOL_CALL_START, {"tool": "edit", "arguments": {"path": "D:/p/calc.py", "old_string": "x"}})
    assert seen == [("tool", "shell_exec", "python -m unittest -v"), ("tool", "edit", "D:/p/calc.py")]
    assert commands.render_event("tool", "edit", "D:/p/calc.py") == "  · edit D:/p/calc.py\n"

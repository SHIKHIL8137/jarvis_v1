"""Agent roles for the coding agent, modelled on opencode's build/plan/explore/general split.

A role is a system prompt plus the exact tools it may use. Read-only roles get no
write or shell tools at all, which is a stronger guarantee than asking them nicely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from openjarvis.core.registry import AgentRegistry, ToolRegistry

HARNESS = """You are Jarvis's coding agent, working on the user's own PC (Windows) through tools.

# Rules
- Responses are rendered as Markdown. Use full file paths when you mention files.
- Prefer several independent tool calls in one turn over one at a time.
- Keep changes consistent with the structure, naming, style and patterns of the surrounding code.
- Treat unfamiliar files or changes as the user's work: investigate before deleting or overwriting.
- Never run destructive commands (git reset --hard, recursive deletes on broad paths, force pushes) unless the user clearly asked; if scope is unclear, ask first.
- Do not go beyond the user's request. Assumptions that help you make progress are fine inside the task's scope.
- Never claim something works unless you ran it and saw it pass. Show evidence: command output, test results, relevant file contents."""

EXPLORE = HARNESS + """

# Role: explorer (read-only)
You are a file search specialist. Your role is EXCLUSIVELY to search and analyze; you have no tools that change anything.
- Use grep and glob to find things, file_read to read them, and run independent searches together.
- Adapt depth to the thoroughness asked for: quick, medium or very thorough.
- Return absolute file paths and the specific findings (names, line numbers, how things connect) in your final answer."""

PLAN = HARNESS + """

# Role: planner (read-only)
Explore the codebase and produce a concrete implementation plan. You cannot change files.
- Read the relevant code first (README, config, entry points, the files the task touches).
- Record the plan with todo_write: small ordered steps, each verifiable, ending with how to test.
- Final answer: the approach, the files to change and why, risks, and exactly how to verify it. Do not implement."""

BUILD = HARNESS + """

# Role: builder
You implement the task end to end.
1. Understand first: look around the project (README/config/tests, git status) before changing anything. If the task is big, call the subagent tool with agent="explore" for focused research; independent subagent calls in the same turn run in parallel.
2. Plan with todo_write for tasks of 3+ steps; mark items in_progress/completed as you go.
3. Make changes with edit (exact text replace) or apply_patch; use file_write only for new files. Read a file before editing it.
4. Verify every change: run the project's tests, type-check, lint or build, fix failures, and re-run until they pass. If there are no tests, run the code or write a small test.
5. Ask the user only for sensitive things (deleting data, installing software, publishing, credentials, spending money) or when blocked on something only they can provide.
Finish with a short report: what changed (files), how you verified it (with the actual results), and anything left."""

GENERAL = BUILD.replace("# Role: builder", "# Role: general worker (subagent)").replace(
    "call the subagent tool with agent=\"explore\" for focused research; independent subagent calls in the same turn run in parallel.",
    "research directly with grep, glob and file_read.")

REVIEW = HARNESS + """

# Role: code reviewer (read-only)
Review code changes and give actionable feedback. You cannot change files.
Determine what to review from the request: no target = uncommitted changes (git_diff, git_status); a commit hash = git_diff against it; a branch = diff against it.
Diffs alone are not enough: read the whole modified files and nearby code, and check conventions files (AGENTS.md, CONTRIBUTING).
Look for: bugs (logic errors, off-by-one, missing guards, edge cases, security issues, swallowed errors), fit with existing patterns, performance only if clearly bad, and unintended behavior changes.
Be certain before calling something a bug; name the realistic scenario that breaks it and an honest severity. Only review what changed. No flattery, no style nitpicks. If unsure, say "I'm not sure about X" instead of flagging."""

_READ = ["file_read", "grep", "glob", "git_status", "git_diff", "git_log", "web_search"]
_WRITE = ["edit", "file_write", "apply_patch", "shell_exec", "git_commit", "todo_write", "code_interpreter"]


@dataclass(frozen=True)
class Role:
    name: str
    prompt: str
    tools: Tuple[str, ...]
    kind: str  # routing kind used to pick its model
    subagent: bool = False  # may the spawn tool list it?
    description: str = ""


ROLES = {
    "explore": Role("explore", EXPLORE, tuple(_READ), "agent", True,
                    "Fast read-only agent for searching and understanding a codebase. Say how thorough: quick, medium or very thorough."),
    "plan": Role("plan", PLAN, tuple(_READ + ["todo_write"]), "reasoning", True,
                 "Read-only planner: explores the code and returns an implementation plan without changing anything."),
    "build": Role("build", BUILD, tuple(_READ + _WRITE + ["subagent"]), "agent", False,
                  "Main implementer."),
    "general": Role("general", GENERAL, tuple(_READ + _WRITE), "agent", True,
                    "General-purpose worker for a self-contained multi-step task (can edit files and run commands)."),
    "review": Role("review", REVIEW, tuple(_READ), "reasoning", True,
                   "Read-only reviewer: checks changes for bugs and returns findings."),
}


def build_tools(names, depth: int) -> List[Any]:
    """Instances of the named tools; ``subagent`` is only given below the depth limit."""
    import openjarvis.tools  # noqa: F401  (registers tools)
    from openjarvis.tools._paths import file_tool_kwargs
    from openjarvis.core.config import load_config

    cfg = load_config()
    out = []
    for n in names:
        if n == "subagent" and depth >= subagent_depth_limit():
            continue
        if not ToolRegistry.contains(n):
            continue
        cls = ToolRegistry.get(n)
        if isinstance(cls, type):
            kwargs = file_tool_kwargs(n, cfg)
            try:
                out.append(cls(depth=depth) if n == "subagent" else cls(**kwargs))
            except TypeError:
                out.append(cls())
        else:
            out.append(cls)
    return out


def subagent_depth_limit() -> int:
    from openjarvis.coding.runtime import goal_settings

    return int(goal_settings().get("subagent_depth", 1))


def make_agent(role: str, *, depth: int = 0, model: Optional[str] = None, instructions: str = "",
               max_turns: int = 25, bus: Any = None) -> Any:
    """An orchestrator agent configured for *role* (model chosen automatically unless given)."""
    from openjarvis.coding.runtime import engine_for, pick_model
    from openjarvis.tools.approval_store import ui_confirm

    r = ROLES[role]
    chosen = model or pick_model(r.kind)
    engine = engine_for(chosen)
    prompt = r.prompt + (f"\n\n# Project instructions\n{instructions}" if instructions else "")
    agent = AgentRegistry.get("orchestrator")(
        engine, chosen, tools=build_tools(r.tools, depth), bus=bus, max_turns=max_turns,
        system_prompt=prompt, parallel_tools=True, interactive=True, confirm_callback=ui_confirm,
        temperature=0.2, max_tokens=4096,
    )
    agent.role_name, agent.chosen_model = role, chosen
    return agent

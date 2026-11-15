"""Chat commands for the coding agent: /goal, /plan, /review, /init (and auto-goals).

``/goal <objective>``  work until a reviewer confirms it with evidence
``/plan <task>``       read-only exploration and an implementation plan
``/review [target]``   review uncommitted changes, a commit, a branch or a PR
``/init [focus]``      create/update the project's AGENTS.md

A plain message that is clearly an instruction to build or change something (for
example a spoken "implement X in D:\\projects\\app") also starts a goal when
``[goal] auto`` is on (the default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REVIEW_PROMPT = """Review code changes and give actionable feedback.

Target: {arg}

Determine what to review: no target = all uncommitted changes (git_diff and git_status, plus read untracked files); a commit hash = that commit; a branch name = diff against it.
Read whole files for context, not just diffs. Check AGENTS.md/CONTRIBUTING for conventions.
Report bugs first (logic errors, missing guards, edge cases, security, swallowed errors), then structure and any behavior changes. Be certain before flagging; say "I'm not sure about X" rather than guessing. Name the realistic scenario and an honest severity for each finding. No flattery."""

INIT_PROMPT = """Create or update `AGENTS.md` in {path} (the project root).

It is a compact instruction file that helps future agents avoid mistakes and ramp up quickly. Every line must answer: "Would an agent likely miss this without help?" If not, leave it out.

User focus (honor this): {arg}

Investigate first: README, manifests (package.json/pyproject), lockfiles, build/test/lint/typecheck config, CI workflows, existing instruction files (AGENTS.md, CLAUDE.md, .cursorrules). Prefer executable sources of truth over prose. If architecture is unclear, read a few representative entry-point files.

Capture: exact dev commands (especially non-obvious), how to run a single test, required command order (lint -> typecheck -> test), repo/package boundaries and real entry points, toolchain quirks (codegen, migrations, env loading), testing prerequisites, and constraints from existing instruction files.
Exclude generic advice, long tutorials, file trees and anything you could not verify. Short sections and bullets. If AGENTS.md already exists, improve it in place and keep what is verified and useful.
Verify the commands you list actually exist in the project before writing them."""

_BUILD_VERBS = (r"implement|build|create|add|fix|refactor|set up|setup|develop|write|make|migrate|"
                r"convert|integrate|update|upgrade|debug|optimi[sz]e|improve")
_AUTO = re.compile(rf"^\s*(?:please\s+|can you\s+|could you\s+|jarvis[,\s]+)*(?:{_BUILD_VERBS})\b", re.I)
_PROJECT_HINT = re.compile(r"[A-Za-z]:[\\/]|\b(project|repo|repository|codebase|app|feature|endpoint|component|bug|tests?)\b", re.I)


@dataclass
class Command:
    name: str   # goal | plan | review | init
    arg: str
    auto: bool = False


def parse_command(text: str, auto_enabled: bool = True) -> Optional[Command]:
    t = (text or "").strip()
    m = re.match(r"^/(goal|plan|review|init)\b\s*(.*)$", t, re.S | re.I)
    if m:
        return Command(m.group(1).lower(), m.group(2).strip())
    # Natural-language build requests start a goal; questions never do.
    if auto_enabled and len(t) >= 40 and "?" not in t[-3:] and _AUTO.match(t) and _PROJECT_HINT.search(t):
        return Command("goal", t, auto=True)
    return None


def run_command(cmd: Command, emit: Callable[..., None], model: Optional[str] = None) -> str:
    """Run a command, reporting progress through *emit*; returns the final markdown."""
    from openjarvis.coding.goal import run_goal
    from openjarvis.coding.roles import make_agent
    from openjarvis.coding.runtime import discover_instructions, project_dir_from, tool_progress_bus
    from openjarvis.tools import coding_tools

    pdir = project_dir_from(cmd.arg)
    if cmd.name == "goal":
        if not cmd.arg:
            return "Usage: `/goal <objective>`, e.g. `/goal add input validation to D:\\projects\\app and make the tests pass`."
        res = run_goal(cmd.arg, emit=emit, model=model)
        head = {"complete": "Goal complete.", "blocked": "Blocked, I need you:", "paused": "Paused:"}[res.status]
        todos = f"\n\n**Plan**\n{coding_tools.format_todos(res.todos)}" if res.todos else ""
        return f"**{head}** {res.reason}{todos}\n\n{res.final_text}".strip()

    role, prompt = {
        "plan": ("plan", cmd.arg or "Plan the work for this project based on what you find."),
        "review": ("review", REVIEW_PROMPT.format(arg=cmd.arg or "(uncommitted changes)")),
        "init": ("build", INIT_PROMPT.format(path=pdir or Path.cwd(), arg=cmd.arg or "(none)")),
    }[cmd.name]
    if cmd.name == "plan" and not cmd.arg:
        return "Usage: `/plan <task>`, e.g. `/plan add rate limiting to the API in D:\\projects\\app`."
    coding_tools.ACTIVE_SCOPE["id"] = f"{cmd.name}-run"
    try:
        agent = make_agent(role, model=model, instructions=discover_instructions(pdir), max_turns=30,
                           bus=tool_progress_bus(emit))
        emit("round", 1, agent.chosen_model)
        result = agent.run(prompt)
        todos = coding_tools.get_todos()
        if todos:
            emit("agent", "", len(result.tool_results or []), todos)
        return result.content or "(no output)"
    finally:
        coding_tools.ACTIVE_SCOPE["id"] = "main"


def render_event(kind: str, *args) -> str:
    """Markdown progress line for an emitted event."""
    from openjarvis.tools.coding_tools import format_todos

    if kind == "round":
        return f"\n**Round {args[0]}**" + (f" · `{args[1]}`" if args[1] else "") + "\n"
    if kind == "tool":
        return f"  · {args[0]} {args[1]}\n"
    if kind == "agent":
        _, tools, todos = args
        out = f"- worked, {tools} tool call(s)\n"
        return out + (f"\n```\n{format_todos(todos)}\n```\n" if todos else "")
    if kind == "verdict":
        v = args[0]
        icon = {"done": "✅", "continue": "↻", "blocked": "⛔", "paused": "⏸"}.get(v["verdict"], "")
        extra = f" → {v['next_step']}" if v.get("next_step") else ""
        return f"- reviewer {icon} **{v['verdict']}**: {v['reason']}{extra}\n"
    if kind == "compact":
        return "- (compacting long context)\n"
    return ""

"""subagent: hand a focused job to a specialist agent with fresh context.

Modelled on opencode's subagent tool: the child starts with only the prompt you
give it, uses its own role-limited toolset (explore/review are read-only), and
has a depth limit so agents cannot spawn agents forever. Several subagent calls
in one turn run in parallel, each on the model the router picks for it.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_RESULT = 9000


@ToolRegistry.register("subagent")
class SubagentTool(BaseTool):
    tool_id = "subagent"

    def __init__(self, depth: int = 0) -> None:
        self._depth = depth

    @property
    def spec(self) -> ToolSpec:
        from openjarvis.coding.roles import ROLES

        listing = "\n".join(f"- {r.name}: {r.description}" for r in ROLES.values() if r.subagent)
        return ToolSpec(
            name="subagent",
            description=(
                "Spawn a specialist agent in a fresh context to work on one task and return its"
                " result. The child sees ONLY your prompt, so include every detail it needs (paths,"
                " goals, constraints). Issue several subagent calls in the same turn to run them in"
                " parallel. Do not use it for trivial lookups you can do yourself.\n"
                f"Available agents:\n{listing}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "explore, plan, review or general."},
                    "description": {"type": "string", "description": "3-5 word label for the task."},
                    "prompt": {"type": "string", "description": "Complete, self-contained task."},
                },
                "required": ["agent", "prompt"],
            },
            category="agents",
            timeout_seconds=900.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.coding.roles import ROLES, make_agent, subagent_depth_limit
        from openjarvis.coding.runtime import discover_instructions, project_dir_from

        name = str(params.get("agent", "")).strip().lower()
        prompt = str(params.get("prompt", "")).strip()
        role = ROLES.get(name)
        if role is None or not role.subagent:
            allowed = ", ".join(r.name for r in ROLES.values() if r.subagent)
            return ToolResult(tool_name="subagent", success=False,
                              content=f"Unknown agent '{name}'. Choose one of: {allowed}.")
        if not prompt:
            return ToolResult(tool_name="subagent", success=False, content="prompt is required.")
        if self._depth >= subagent_depth_limit():
            return ToolResult(tool_name="subagent", success=False,
                              content=f"Subagent depth limit ({subagent_depth_limit()}) reached; do this work yourself.")
        try:
            instructions = discover_instructions(project_dir_from(prompt))
            agent = make_agent(name, depth=self._depth + 1, instructions=instructions, max_turns=20)
            result = agent.run("You are a subagent spawned by another agent.\n\n" + prompt)
        except Exception as exc:  # a failing child must not crash the parent's turn
            return ToolResult(tool_name="subagent", success=False, content=f"Subagent failed: {exc}")
        text = (result.content or "").strip() or "(the subagent returned nothing)"
        if len(text) > _MAX_RESULT:
            text = text[:_MAX_RESULT] + "\n[truncated]"
        return ToolResult(
            tool_name="subagent", success=True,
            content=f"<subagent agent={name} model={agent.chosen_model}>\n{text}\n</subagent>",
            metadata={"agent": name, "model": agent.chosen_model, "turns": result.turns},
        )

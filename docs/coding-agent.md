# Coding agent

Jarvis can take a coding task, plan it, split the research, implement it, test it and only stop when a
reviewer confirms it with evidence. The design follows [opencode](https://opencode.ai) (goal loop, plan
mode, read-only subagents, exact edit tool, tool-input repair, AGENTS.md discovery, structured
context compaction), rebuilt natively in Python.

## Use it

In chat (typed or by voice):

| Message | What happens |
|---|---|
| `/goal <objective>` | Builder works in rounds; a reviewer judges only evidence (test output, file contents, todo list) and answers done / continue / blocked. |
| `/plan <task>` | Read-only exploration and a written plan. Nothing is changed. |
| `/review [commit\|branch\|PR]` | Read-only code review of uncommitted changes (default). |
| `/init [focus]` | Creates or improves the project's `AGENTS.md`. |
| "Implement X in D:\projects\app" | A clear build request starts a `/goal` automatically (`[goal] auto = true`). Questions never do. |

## How it works

- **Roles** (`coding/roles.py`): `build` (edits, runs commands, may spawn subagents), `general`,
  and read-only `explore`, `plan`, `review` that simply have no write or shell tools.
- **Subagents** (`tools/subagent.py`): fresh context, one level deep (`[goal] subagent_depth`),
  several in one turn run in parallel. Each role's model is picked by the auto router.
- **Goal loop** (`coding/goal.py`): stops on done, on a blocker only the user can fix, after 3 stalled
  rounds (same complaint again, or no tool calls), or at `[goal] max_rounds` (default 12).
- **Tools**: `edit` (exact replace, lenient matching, CRLF-safe, refuses ambiguous matches),
  `grep`, `glob`, `todo_write`. Malformed tool arguments from cheaper models are repaired
  (`tools/arg_repair.py`) and foreign tool names (`bash`, `read`...) map to ours.
- **Instructions**: the nearest `AGENTS.md`/`CLAUDE.md` up from the project folder, plus
  `~/.openjarvis/AGENTS.md`, are given to every agent.
- **Long runs**: past ~40k characters the older conversation is replaced by a structured
  checkpoint (objective, decisions, work state, next move, files).
- **Safety**: shell commands outside the safe list (tests, lint, builds, read-only commands) and anything
  risky wait for your approval in the bell; granted folders skip routine prompts. Stop the chat to stop a goal
  at its next step.

## Settings (`~/.openjarvis/config.toml`)

```toml
[goal]
max_rounds = 12
subagent_depth = 1
auto = true
```

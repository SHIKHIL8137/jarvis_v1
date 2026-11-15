"""Context compaction with opencode's structured checkpoint template.

When a long goal run's conversation grows past a budget, older turns are replaced
by one structured summary (objective, decisions, work state, next move, files)
so the agent can keep going without losing the thread.
"""

from __future__ import annotations

from typing import List

from openjarvis.core.types import Message, Role

SUMMARY_TEMPLATE = """You MUST use this format for your response (omit sections that aren't applicable).
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Requirements
- [constraints, preferences and scope boundaries stated by the user, or "(none)"]

## Decisions
- [decisions already made and why, or "(none)"]

## Work State
### Completed
- [goals completed, or "(none)"]
### Active
- [goals being worked on, or "(none)"]
### Blocked
- [what blocks progress and why, or "(none)"]

## Next Move
1. [ordered next actions, or "(none)"]

## Relevant Files
- `[path]`: [why it matters]  (at most 15, most important first, or "(none)")

## Important Context
- [facts the next agent cannot continue without and cannot easily find, or "(none)"]"""

RULES = """Rules:
- Terse single-line bullets, no prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs and identifiers.
- Carry forward only user requests that are still unanswered. Preserve workflow state (what is changed, tested, committed).
- Do not mention the summary process. Do not continue the task or call tools. Return only the summary."""

KEEP_TAIL = 2  # most recent messages kept verbatim beside the summary


def size(messages: List[Message]) -> int:
    return sum(len(m.content or "") for m in messages)


def compact(engine, model: str, messages: List[Message], previous_summary: str = "") -> List[Message]:
    """Summarise all but the last few messages; returns the shortened conversation."""
    if len(messages) <= KEEP_TAIL + 1:
        return messages
    old, tail = messages[:-KEEP_TAIL], messages[-KEEP_TAIL:]
    transcript = "\n\n".join(f"[{m.role.value}] {(m.content or '')[:6000]}" for m in old)
    ask = (
        "Summarize the conversation below into a structured checkpoint another agent can resume from.\n\n"
        + (f"Existing checkpoint to update (newer history wins):\n{previous_summary}\n\n" if previous_summary else "")
        + f"<conversation>\n{transcript}\n</conversation>\n\n{SUMMARY_TEMPLATE}\n\n{RULES}"
    )
    res = engine.generate([Message(role=Role.USER, content=ask)], model=model, temperature=0.0, max_tokens=1500)
    summary = (res.get("content") or "").strip()
    if not summary:
        return messages
    return [Message(role=Role.USER, content="<conversation-checkpoint>\n" + summary + "\n</conversation-checkpoint>")] + list(tail)

"""delegate_parallel: fan independent subtasks out to different models at once."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import Message, Role, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_TASKS = 5
_WORKER_PROMPT = (
    "You are a focused specialist working on ONE subtask of a bigger job. "
    "Do only this subtask, be concrete and concise, and return just your result."
)


def _run_one(item: dict) -> dict:
    from openjarvis.core.config import load_config
    from openjarvis.engine._discovery import get_engine
    from openjarvis.routing.auto_router import classify, model_for

    task = str(item.get("task", "")).strip()
    kind = str(item.get("kind") or "") or classify(task)[0]
    model = model_for(kind if kind in ("quick", "code", "reasoning", "research", "vision", "agent") else "agent")
    t0 = time.time()
    try:
        from openjarvis.server.cloud_router import is_cloud_model

        cfg = load_config()
        found = get_engine(cfg, "cloud") if is_cloud_model(model) else get_engine(cfg, model=model)
        if not found:
            raise RuntimeError(f"no engine can serve {model}")
        result = found[1].generate(
            [Message(role=Role.SYSTEM, content=_WORKER_PROMPT), Message(role=Role.USER, content=task)],
            model=model, temperature=0.3, max_tokens=900,
        )
        return {"task": task, "model": model, "ok": True, "text": (result.get("content") or "").strip(),
                "secs": round(time.time() - t0, 1)}
    except Exception as exc:  # one failing worker must not sink the others
        return {"task": task, "model": model, "ok": False, "text": f"failed: {exc}", "secs": round(time.time() - t0, 1)}


@ToolRegistry.register("delegate_parallel")
class DelegateParallelTool(BaseTool):
    tool_id = "delegate_parallel"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="delegate_parallel",
            description=(
                "Run several INDEPENDENT thinking/writing subtasks at the same time, each on the best"
                " model for its kind (code, reasoning, research, quick). Workers cannot use tools or"
                " see the chat: put everything they need in each task. Use it to split a big request"
                " into parts, then combine the results yourself. Do not use it for file/app actions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "maxItems": _MAX_TASKS,
                        "description": "Independent subtasks (2 to 5).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "Self-contained instructions."},
                                "kind": {"type": "string", "description": "code, reasoning, research or quick."},
                            },
                            "required": ["task"],
                        },
                    }
                },
                "required": ["tasks"],
            },
            category="agents",
            timeout_seconds=180.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        tasks: List[dict] = [t for t in (params.get("tasks") or []) if isinstance(t, dict) and t.get("task")]
        if not tasks:
            return ToolResult(tool_name="delegate_parallel", content="Give at least one task.", success=False)
        tasks = tasks[:_MAX_TASKS]
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            results = list(pool.map(_run_one, tasks))
        body = "\n\n".join(
            f"### Subtask {i + 1} (by {r['model']}, {r['secs']}s)\n{r['text']}" for i, r in enumerate(results)
        )
        return ToolResult(
            tool_name="delegate_parallel", content=body, success=any(r["ok"] for r in results),
            metadata={"models": [r["model"] for r in results]},
        )

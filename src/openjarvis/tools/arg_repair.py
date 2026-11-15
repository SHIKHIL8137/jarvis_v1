"""Repair common tool-call mistakes from weaker models (ported from opencode).

Applied only when the tool's JSON schema makes the fix unambiguous:

* stringified object/array  ``'{"n": 1}'`` -> ``{"n": 1}``
* numeric/boolean strings    ``"20"`` -> ``20``, ``"false"`` -> ``False``
* ``null`` / ``{}`` placeholder for an optional plain field -> field dropped
* a single value where an array is expected -> wrapped in a list
* unknown keys dropped when the schema is closed (``additionalProperties: false``)

Also maps tool names borrowed from other harnesses (``bash``, ``read`` ...) onto
ours when no tool has that exact name.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

ALIASES: Dict[str, str] = {
    "bash": "shell_exec", "shell": "shell_exec", "run_command": "shell_exec",
    "read": "file_read", "read_file": "file_read",
    "write": "file_write", "write_file": "file_write",
    "patch": "apply_patch", "search": "grep", "find": "glob",
    "todowrite": "todo_write", "task": "subagent", "websearch": "web_search",
}
_MAX_DEPTH = 6
_COMPOSED = ("anyOf", "oneOf", "allOf", "enum", "const", "$ref", "nullable")


def resolve_alias(name: str, available) -> Optional[str]:
    """Our tool name for a foreign *name*, if exactly one mapping applies."""
    target = ALIASES.get(name)
    return target if target in available and name not in available else None


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def repair_args(value: Any, schema: Dict[str, Any], depth: int = 0) -> Any:
    if depth > _MAX_DEPTH or not isinstance(schema, dict):
        return value
    kind = schema.get("type")
    if kind in ("integer", "number"):
        if isinstance(value, str) and value.strip():
            try:
                num = float(value)
            except ValueError:
                return value
            if kind == "integer":
                return int(num) if num == int(num) else value
            return num
        return value
    if kind == "boolean":
        return {"true": True, "false": False}.get(value, value) if isinstance(value, str) else value
    if kind == "object":
        parsed = _loads(value)
        if not isinstance(parsed, dict):
            return value
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        closed = schema.get("additionalProperties") is False and not any(k in schema for k in ("anyOf", "oneOf", "allOf"))
        out: Dict[str, Any] = {}
        for key, val in parsed.items():
            if key not in props:
                if closed:
                    continue
                out[key] = val
                continue
            prop = props[key]
            plain = isinstance(prop.get("type"), str) and not any(k in prop for k in _COMPOSED)
            if key not in required and plain and (val is None or (val == {} and prop.get("type") != "object")):
                continue
            out[key] = repair_args(val, prop, depth + 1)
        return out
    if kind == "array":
        items = schema.get("items") or {}
        parsed = _loads(value)
        if parsed is None and isinstance(value, str):  # not JSON: a bare single value
            parsed = value
        if isinstance(parsed, list):
            return [repair_args(v, items, depth + 1) for v in parsed]
        if parsed is not None and isinstance(items, dict) and items.get("type") in ("string", "integer", "number", "object"):
            fixed = repair_args(parsed, items, depth + 1)
            ok = {"string": str, "object": dict, "integer": int, "number": (int, float)}[items["type"]]
            if isinstance(fixed, ok) and not isinstance(fixed, bool):
                return [fixed]
        return value
    return value

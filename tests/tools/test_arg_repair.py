import json

from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.arg_repair import repair_args

SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer"}, "on": {"type": "boolean"}, "name": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "opts": {"type": "object", "properties": {"n": {"type": "number"}}},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def test_string_numbers_and_booleans_are_coerced():
    assert repair_args({"name": "x", "limit": "20", "on": "false"}, SCHEMA) == {"name": "x", "limit": 20, "on": False}


def test_nulls_for_optional_fields_and_unknown_keys_dropped():
    assert repair_args({"name": "x", "limit": None, "on": {}, "opts": {}, "extra": 1}, SCHEMA) == {
        "name": "x", "opts": {}}  # {} is a legitimate value for an object field


def test_stringified_object_array_and_single_value_wrapping():
    assert repair_args(json.dumps({"name": "x", "tags": '["a","b"]', "opts": '{"n": "2.5"}'}), SCHEMA) == {
        "name": "x", "tags": ["a", "b"], "opts": {"n": 2.5}}
    assert repair_args({"name": "x", "tags": "solo"}, SCHEMA)["tags"] == ["solo"]


def test_unrepairable_values_are_left_alone():
    assert repair_args({"name": "x", "limit": "abc", "on": "maybe"}, SCHEMA) == {"name": "x", "limit": "abc", "on": "maybe"}
    assert repair_args({"name": "x", "limit": "2.5"}, SCHEMA)["limit"] == "2.5"  # not a whole number


class Echo(BaseTool):
    tool_id = "shell_exec"

    @property
    def spec(self):
        return ToolSpec(name="shell_exec", description="d", parameters={
            "type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
            "required": ["command"]})

    def execute(self, **p):
        return ToolResult(tool_name="shell_exec", content=repr(p), success=True)


def test_executor_repairs_args_and_maps_foreign_tool_names():
    ex = ToolExecutor([Echo()])
    r = ex.execute(ToolCall(id="1", name="bash", arguments=json.dumps({"command": "ls", "timeout": "5"})))
    assert r.success and "'timeout': 5" in r.content and 'ran as "shell_exec"' in r.content
    assert not ex.execute(ToolCall(id="2", name="nonexistent", arguments="{}")).success

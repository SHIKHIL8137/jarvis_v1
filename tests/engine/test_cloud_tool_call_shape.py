from types import SimpleNamespace as NS

from openjarvis.engine.cloud import CloudEngine


def test_openrouter_tool_calls_are_flat_and_nested(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    tc = NS(id="c1", type="function", function=NS(name="file_read", arguments='{"path":"a"}'))
    msg = NS(content="", tool_calls=[tc])
    resp = NS(
        model="m", usage=None,
        choices=[NS(message=msg, finish_reason="tool_calls")],
    )
    eng = CloudEngine()
    monkeypatch.setattr(
        "openjarvis.engine.cloud._chat_completion_with_temperature_retry",
        lambda *a, **k: resp,
    )
    out = eng._generate_openrouter([], model="openrouter/x/y", temperature=0.0, max_tokens=10)
    call = out["tool_calls"][0]
    assert call["name"] == "file_read" and call["arguments"] == '{"path":"a"}'
    assert call["function"]["name"] == "file_read"


def test_toplevel_anyof_is_stripped_for_openai_backed_models():
    from openjarvis.engine.cloud import _strip_toplevel_schema_keywords as strip

    tool = {"type": "function", "function": {"name": "t", "parameters": {
        "type": "object", "properties": {"a": {"type": "string"}},
        "anyOf": [{"required": ["a"]}]}}}
    out = strip([tool])[0]["function"]["parameters"]
    assert "anyOf" not in out and out["properties"]["a"]["type"] == "string"
    assert "anyOf" in tool["function"]["parameters"]  # original untouched

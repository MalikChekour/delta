import json

import pytest

from hermes.llm import AnthropicClient, ToolCall


def test_tool_call_parses_escaped_json():
    call = ToolCall(id="1", name="shell", arguments='{"command": "echo \\"hi\\""}')
    assert call.parsed() == {"command": 'echo "hi"'}


def test_tool_call_empty_arguments():
    assert ToolCall(id="1", name="x", arguments="").parsed() == {}


def test_tool_call_invalid_json_raises():
    with pytest.raises(ValueError):
        ToolCall(id="1", name="x", arguments="{not json").parsed()


def test_anthropic_merges_consecutive_tool_results():
    """Anthropic exige que les tool_result d'un meme tour tiennent dans un seul message."""
    internal = [
        {"role": "user", "content": "fais deux choses"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "a", "type": "function",
                 "function": {"name": "shell", "arguments": json.dumps({"command": "ls"})}},
                {"id": "b", "type": "function",
                 "function": {"name": "shell", "arguments": json.dumps({"command": "pwd"})}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "out-a"},
        {"role": "tool", "tool_call_id": "b", "content": "out-b"},
    ]
    converted = AnthropicClient._to_anthropic(internal)

    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
    assert len(converted[2]["content"]) == 2
    assert {b["tool_use_id"] for b in converted[2]["content"]} == {"a", "b"}
    assert converted[1]["content"][0]["type"] == "tool_use"
    assert converted[1]["content"][0]["input"] == {"command": "ls"}


def test_anthropic_assistant_never_has_empty_content():
    converted = AnthropicClient._to_anthropic([{"role": "assistant", "content": ""}])
    assert converted[0]["content"]  # une liste vide ferait echouer l'API

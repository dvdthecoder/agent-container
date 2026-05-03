"""Unit tests for the Responses API → Chat Completions proxy.

The proxy logic lives in agent/opencode_runner.py (_ProxyHandler and the
conversion helpers).  These tests cover the pure conversion functions and the
translation methods without spinning up an HTTP server.

All tests are self-contained — no network calls, no Modal, no external
services required.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

# Import the pure functions and the handler class directly.  The module-level
# globals (TASK, BASE_URL, etc.) are fine — they just read from env/argv.
from agent.opencode_runner import (
    _convert_input_items,
    _convert_tools,
    _ProxyHandler,
)

# ---------------------------------------------------------------------------
# _convert_tools
# ---------------------------------------------------------------------------


class TestConvertTools:
    def test_flat_function_wrapped_in_function_key(self):
        tools = [{"type": "function", "name": "read", "parameters": {"type": "object"}}]
        result = _convert_tools(tools)
        assert result == [
            {
                "type": "function",
                "function": {"name": "read", "parameters": {"type": "object"}},
            }
        ]

    def test_already_wrapped_tool_passed_through(self):
        tool = {"type": "function", "function": {"name": "edit", "parameters": {}}}
        result = _convert_tools([tool])
        assert result == [tool]

    def test_non_dict_skipped(self):
        result = _convert_tools(["not-a-dict", None])  # type: ignore[list-item]
        assert result == []

    def test_empty_list(self):
        assert _convert_tools([]) == []

    def test_multiple_tools_all_converted(self):
        tools = [
            {"type": "function", "name": "read", "parameters": {}},
            {"type": "function", "name": "edit", "parameters": {}},
        ]
        result = _convert_tools(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "read"
        assert result[1]["function"]["name"] == "edit"


# ---------------------------------------------------------------------------
# _convert_input_items
# ---------------------------------------------------------------------------


class TestConvertInputItems:
    def test_string_item_becomes_user_message(self):
        result = _convert_input_items(["hello world"])
        assert result == [{"role": "user", "content": "hello world"}]

    def test_plain_message_item(self):
        item = {"type": "message", "role": "user", "content": "fix it"}
        result = _convert_input_items([item])
        assert result == [{"role": "user", "content": "fix it"}]

    def test_developer_role_mapped_to_system(self):
        item = {"type": "message", "role": "developer", "content": "You are a coder."}
        result = _convert_input_items([item])
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a coder."

    def test_tool_result_becomes_tool_message(self):
        item = {"type": "tool_result", "call_id": "call_abc", "output": "file contents"}
        result = _convert_input_items([item])
        assert result == [{"role": "tool", "tool_call_id": "call_abc", "content": "file contents"}]

    def test_function_call_becomes_assistant_with_tool_calls(self):
        item = {
            "type": "function_call",
            "id": "call_123",
            "call_id": "call_123",
            "name": "read",
            "arguments": '{"path": "/foo.py"}',
        }
        result = _convert_input_items([item])
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        tc = msg["tool_calls"][0]
        assert tc["id"] == "call_123"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "read"
        assert tc["function"]["arguments"] == '{"path": "/foo.py"}'

    def test_list_content_joined(self):
        item = {
            "type": "message",
            "role": "user",
            "content": [{"text": "hello"}, {"text": "world"}],
        }
        result = _convert_input_items([item])
        assert result[0]["content"] == "hello world"

    def test_unknown_type_serialized_as_json(self):
        item = {"type": "mystery", "role": "user", "data": 42}
        result = _convert_input_items([item])
        assert result[0]["role"] == "user"
        # content is the JSON serialisation of the whole item
        parsed = json.loads(result[0]["content"])
        assert parsed["type"] == "mystery"

    def test_empty_list(self):
        assert _convert_input_items([]) == []


# ---------------------------------------------------------------------------
# _ProxyHandler._translate_chat_response
# ---------------------------------------------------------------------------


def _make_handler() -> _ProxyHandler:
    """Return a _ProxyHandler instance without wiring up an HTTP server."""
    handler = _ProxyHandler.__new__(_ProxyHandler)
    return handler


class TestTranslateChatResponse:
    def _chat_response(
        self,
        text: str = "",
        tool_calls: list | None = None,
        model: str = "qwen2.5",
    ) -> bytes:
        message: dict = {"content": text or None, "role": "assistant"}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return json.dumps(
            {
                "id": "chatcmpl-abc",
                "model": model,
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        ).encode()

    def test_text_response_produces_message_output(self):
        handler = _make_handler()
        raw = self._chat_response(text="Here is the fix.")
        result = json.loads(handler._translate_chat_response(raw))

        assert result["object"] == "response"
        assert len(result["output"]) == 1
        item = result["output"][0]
        assert item["type"] == "message"
        assert item["content"][0]["text"] == "Here is the fix."

    def test_tool_call_response_produces_function_call_output(self):
        handler = _make_handler()
        raw = self._chat_response(
            tool_calls=[
                {
                    "id": "call_xyz",
                    "type": "function",
                    "function": {"name": "edit", "arguments": '{"path":"/a.py"}'},
                }
            ]
        )
        result = json.loads(handler._translate_chat_response(raw))

        assert result["object"] == "response"
        fn_items = [o for o in result["output"] if o["type"] == "function_call"]
        assert len(fn_items) == 1
        fc = fn_items[0]
        assert fc["name"] == "edit"
        assert fc["call_id"] == "call_xyz"
        assert fc["arguments"] == '{"path":"/a.py"}'

    def test_empty_response_produces_empty_message(self):
        handler = _make_handler()
        raw = self._chat_response(text="")
        result = json.loads(handler._translate_chat_response(raw))

        assert len(result["output"]) == 1
        assert result["output"][0]["type"] == "message"

    def test_usage_field_preserved(self):
        handler = _make_handler()
        raw = self._chat_response(text="hi")
        result = json.loads(handler._translate_chat_response(raw))

        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["total_tokens"] == 15

    def test_invalid_json_returned_as_is(self):
        handler = _make_handler()
        raw = b"not json"
        result = handler._translate_chat_response(raw)
        assert result == raw


# ---------------------------------------------------------------------------
# _ProxyHandler._stream_chat_to_responses
# ---------------------------------------------------------------------------


def _sse_lines(*chunks: dict) -> list[bytes]:
    """Build SSE byte lines from a sequence of Chat Completions delta chunks."""
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}\n".encode())
    lines.append(b"data: [DONE]\n")
    return lines


def _delta_chunk(
    text: str | None = None,
    tool_index: int | None = None,
    tool_id: str | None = None,
    tool_name: str | None = None,
    tool_args: str | None = None,
) -> dict:
    delta: dict = {}
    if text is not None:
        delta["content"] = text
    if tool_index is not None:
        tc: dict = {"index": tool_index}
        if tool_id:
            tc["id"] = tool_id
        fn: dict = {}
        if tool_name:
            fn["name"] = tool_name
        if tool_args:
            fn["arguments"] = tool_args
        if fn:
            tc["function"] = fn
        delta["tool_calls"] = [tc]
    return {"choices": [{"delta": delta}]}


def _capture_streaming_sse(sse_lines: list[bytes]) -> list[dict]:
    """Run _stream_chat_to_responses and collect parsed SSE events."""
    handler = _make_handler()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    buf = io.BytesIO()
    handler.wfile = buf

    mock_resp = MagicMock()
    mock_resp.__iter__ = MagicMock(return_value=iter(sse_lines))

    handler._stream_chat_to_responses(mock_resp)

    buf.seek(0)
    events = []
    for raw in buf.read().decode().split("\n\n"):
        raw = raw.strip()
        if raw.startswith("data:"):
            try:
                events.append(json.loads(raw[5:].strip()))
            except json.JSONDecodeError:
                pass
    return events


class TestStreamChatToResponses:
    def test_text_delta_emitted_as_output_text_delta(self):
        lines = _sse_lines(
            _delta_chunk(text="Hello"),
            _delta_chunk(text=" world"),
        )
        events = _capture_streaming_sse(lines)

        delta_events = [e for e in events if e.get("type") == "response.output_text.delta"]
        assert len(delta_events) == 2
        assert delta_events[0]["delta"] == "Hello"
        assert delta_events[1]["delta"] == " world"

    def test_tool_call_emits_full_event_sequence(self):
        lines = _sse_lines(
            _delta_chunk(tool_index=0, tool_id="call_1", tool_name="read", tool_args=""),
            _delta_chunk(tool_index=0, tool_args='{"path":"/a.py"}'),
        )
        events = _capture_streaming_sse(lines)

        types = [e.get("type") for e in events]
        assert "response.output_item.added" in types
        assert "response.function_call_arguments.delta" in types
        assert "response.function_call_arguments.done" in types
        assert "response.output_item.done" in types
        assert "response.completed" in types

    def test_response_completed_emitted_after_tool_call(self):
        lines = _sse_lines(
            _delta_chunk(tool_index=0, tool_id="call_99", tool_name="edit", tool_args="{}"),
        )
        events = _capture_streaming_sse(lines)

        completed = [e for e in events if e.get("type") == "response.completed"]
        assert len(completed) == 1
        output = completed[0]["response"]["output"]
        fn_items = [o for o in output if o.get("type") == "function_call"]
        assert len(fn_items) == 1
        assert fn_items[0]["name"] == "edit"

    def test_response_completed_emitted_for_text_only(self):
        lines = _sse_lines(
            _delta_chunk(text="Done."),
        )
        events = _capture_streaming_sse(lines)

        completed = [e for e in events if e.get("type") == "response.completed"]
        assert len(completed) == 1

    def test_arguments_accumulated_across_chunks(self):
        lines = _sse_lines(
            _delta_chunk(tool_index=0, tool_id="c1", tool_name="read", tool_args=""),
            _delta_chunk(tool_index=0, tool_args='{"pa'),
            _delta_chunk(tool_index=0, tool_args='th":"/x"}'),
        )
        events = _capture_streaming_sse(lines)

        done_events = [
            e for e in events if e.get("type") == "response.function_call_arguments.done"
        ]
        assert len(done_events) == 1
        assert done_events[0]["arguments"] == '{"path":"/x"}'

    def test_empty_stream_produces_empty_message_in_completed(self):
        lines = [b"data: [DONE]\n"]
        events = _capture_streaming_sse(lines)

        completed = [e for e in events if e.get("type") == "response.completed"]
        assert len(completed) == 1
        output = completed[0]["response"]["output"]
        assert len(output) == 1
        assert output[0]["type"] == "message"

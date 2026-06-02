"""Tests for P0-2: tool_use JSON parse failure handling.

TS behavior (normalizeContentFromAPI): silently falls back to {} on parse failure.
Python should match — log warning but don't crash or propagate error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest

from cc.api.claude import stream_response
from cc.core.events import ToolUseStart


@dataclass
class MockContentBlock:
    type: str
    id: str = ""
    name: str = ""


@dataclass
class MockDelta:
    type: str
    text: str = ""
    partial_json: str = ""
    stop_reason: str | None = None


@dataclass
class MockUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class MockMessage:
    usage: MockUsage


@dataclass
class MockEvent:
    type: str
    index: int = 0
    content_block: MockContentBlock | None = None
    delta: MockDelta | None = None
    message: MockMessage | None = None
    usage: MockUsage | None = None


class MockStream:
    def __init__(self, events: list[MockEvent]) -> None:
        self.events = events

    async def __aenter__(self) -> MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> AsyncIterator[MockEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[MockEvent]:
        for event in self.events:
            yield event


class TestToolInputJsonParseFallback:
    """P0-2: JSON parse failure falls back to {} (matches TS behavior)."""

    @pytest.mark.asyncio
    async def test_malformed_json_falls_back_to_empty_dict(self) -> None:
        """Malformed JSON input should not crash — tool gets {} like TS."""
        events = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(
                type="content_block_start", index=0,
                content_block=MockContentBlock(type="tool_use", id="tu_1", name="bash"),
            ),
            MockEvent(
                type="content_block_delta", index=0,
                delta=MockDelta(type="input_json_delta", partial_json='{bad json!!!'),
            ),
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=MockDelta(type="message_delta", stop_reason="tool_use"),
                usage=MockUsage(output_tokens=5),
            ),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=MockStream(events))

        result_events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        tool_starts = [e for e in result_events if isinstance(e, ToolUseStart)]
        assert len(tool_starts) == 1
        assert tool_starts[0].input == {}  # Falls back to empty dict
        assert tool_starts[0].tool_name == "bash"

    @pytest.mark.asyncio
    async def test_empty_json_string_gives_empty_dict(self) -> None:
        """Empty input_json string should produce {}."""
        events = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(
                type="content_block_start", index=0,
                content_block=MockContentBlock(type="tool_use", id="tu_1", name="bash"),
            ),
            # No input_json_delta events at all
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=MockDelta(type="message_delta", stop_reason="tool_use"),
                usage=MockUsage(output_tokens=5),
            ),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=MockStream(events))

        result_events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        tool_starts = [e for e in result_events if isinstance(e, ToolUseStart)]
        assert len(tool_starts) == 1
        assert tool_starts[0].input == {}

    @pytest.mark.asyncio
    async def test_valid_json_parses_correctly(self) -> None:
        """Valid JSON input should parse normally."""
        events = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(
                type="content_block_start", index=0,
                content_block=MockContentBlock(type="tool_use", id="tu_1", name="bash"),
            ),
            MockEvent(
                type="content_block_delta", index=0,
                delta=MockDelta(type="input_json_delta", partial_json='{"command": "ls -la"}'),
            ),
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=MockDelta(type="message_delta", stop_reason="tool_use"),
                usage=MockUsage(output_tokens=5),
            ),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=MockStream(events))

        result_events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        tool_starts = [e for e in result_events if isinstance(e, ToolUseStart)]
        assert tool_starts[0].input == {"command": "ls -la"}

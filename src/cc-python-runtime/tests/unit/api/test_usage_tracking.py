"""Tests for P0-5: Usage token tracking.

Confirmed: cache tokens only come from message_start, output_tokens from message_delta.
This is correct API behavior, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest

from cc.api.claude import stream_response
from cc.core.events import TurnComplete


@dataclass
class MockContentBlock:
    type: str = "text"


@dataclass
class MockDelta:
    type: str = "text_delta"
    text: str = ""
    stop_reason: str | None = None


@dataclass
class MockStartUsage:
    """Usage from message_start — has all token fields."""
    input_tokens: int = 100
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class MockDeltaUsage:
    """Usage from message_delta — only has output_tokens."""
    output_tokens: int = 50


@dataclass
class MockMessage:
    usage: MockStartUsage


@dataclass
class MockEvent:
    type: str
    index: int = 0
    content_block: MockContentBlock | None = None
    delta: MockDelta | None = None
    message: MockMessage | None = None
    usage: MockDeltaUsage | None = None


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


class TestUsageTracking:
    @pytest.mark.asyncio
    async def test_full_usage_from_both_events(self) -> None:
        """input + cache tokens from message_start, output from message_delta."""
        events = [
            MockEvent(
                type="message_start",
                message=MockMessage(usage=MockStartUsage(
                    input_tokens=200,
                    cache_creation_input_tokens=50,
                    cache_read_input_tokens=100,
                )),
            ),
            MockEvent(type="content_block_start", index=0, content_block=MockContentBlock()),
            MockEvent(type="content_block_delta", index=0, delta=MockDelta(text="hi")),
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=MockDelta(type="message_delta", stop_reason="end_turn"),
                usage=MockDeltaUsage(output_tokens=10),
            ),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=MockStream(events))

        result_events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in result_events if isinstance(e, TurnComplete)]
        assert turns[0].usage.input_tokens == 200
        assert turns[0].usage.output_tokens == 10
        assert turns[0].usage.cache_creation_input_tokens == 50
        assert turns[0].usage.cache_read_input_tokens == 100

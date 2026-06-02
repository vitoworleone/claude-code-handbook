"""Tests for P0-1: stop_reason must not be masked by fallback defaults.

TS original (services/api/claude.ts):
- Initializes stopReason = null
- Extracts delta.stop_reason directly, no fallback
- "tool_use" stop_reason must propagate correctly

Bug: Python claude.py:183 uses `or "end_turn"` which masks falsy values,
and initializes to "end_turn" instead of None.
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


def _make_events_with_stop_reason(stop_reason: str | None) -> list[MockEvent]:
    """Create minimal stream events with a specific stop_reason in message_delta."""
    return [
        MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
        MockEvent(type="content_block_start", index=0, content_block=MockContentBlock(type="text")),
        MockEvent(type="content_block_delta", index=0, delta=MockDelta(type="text_delta", text="hi")),
        MockEvent(type="content_block_stop", index=0),
        MockEvent(
            type="message_delta",
            delta=MockDelta(type="message_delta", stop_reason=stop_reason),
            usage=MockUsage(output_tokens=5),
        ),
    ]


def _make_client(events: list[MockEvent]) -> MagicMock:
    client = MagicMock()
    client.messages.stream = MagicMock(return_value=MockStream(events))
    return client


class TestStopReasonNotMasked:
    """P0-1: stop_reason values must propagate without being masked."""

    @pytest.mark.asyncio
    async def test_tool_use_stop_reason_preserved(self) -> None:
        """stop_reason='tool_use' must NOT be replaced with 'end_turn'."""
        client = _make_client(_make_events_with_stop_reason("tool_use"))
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert len(turns) == 1
        assert turns[0].stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_max_tokens_stop_reason_preserved(self) -> None:
        """stop_reason='max_tokens' must propagate."""
        client = _make_client(_make_events_with_stop_reason("max_tokens"))
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert turns[0].stop_reason == "max_tokens"

    @pytest.mark.asyncio
    async def test_end_turn_stop_reason_preserved(self) -> None:
        """stop_reason='end_turn' still works normally."""
        client = _make_client(_make_events_with_stop_reason("end_turn"))
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert turns[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_none_stop_reason_defaults_to_end_turn(self) -> None:
        """If API returns None stop_reason, default to 'end_turn' in TurnComplete."""
        client = _make_client(_make_events_with_stop_reason(None))
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert turns[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stop_sequence_stop_reason_preserved(self) -> None:
        """stop_reason='stop_sequence' must propagate."""
        client = _make_client(_make_events_with_stop_reason("stop_sequence"))
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert turns[0].stop_reason == "stop_sequence"

    @pytest.mark.asyncio
    async def test_no_message_delta_defaults_to_end_turn(self) -> None:
        """If no message_delta event arrives at all, stop_reason should be 'end_turn'.

        This tests the initialization value. TS inits to null then converts;
        Python should init to None internally but yield 'end_turn' in TurnComplete.
        """
        # Stream with no message_delta event
        events_no_delta = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(type="content_block_start", index=0, content_block=MockContentBlock(type="text")),
            MockEvent(type="content_block_delta", index=0, delta=MockDelta(type="text_delta", text="hi")),
            MockEvent(type="content_block_stop", index=0),
            # No message_delta event at all
        ]
        client = _make_client(events_no_delta)
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert len(turns) == 1
        assert turns[0].stop_reason == "end_turn"


class TestStopReasonNoMessageDeltaAttr:
    """Edge case: message_delta.delta exists but has no stop_reason attr."""

    @pytest.mark.asyncio
    async def test_delta_without_stop_reason_attr(self) -> None:
        """If delta object lacks stop_reason attribute entirely, don't crash."""

        @dataclass
        class DeltaNoStopReason:
            type: str = "message_delta"
            # Intentionally no stop_reason attribute

        events_raw = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(type="content_block_start", index=0, content_block=MockContentBlock(type="text")),
            MockEvent(type="content_block_delta", index=0, delta=MockDelta(type="text_delta", text="hi")),
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=DeltaNoStopReason(),  # type: ignore[arg-type]
                usage=MockUsage(output_tokens=5),
            ),
        ]
        client = _make_client(events_raw)
        events = [e async for e in stream_response(
            client, messages=[{"role": "user", "content": "x"}], system="s",
        )]
        turns = [e for e in events if isinstance(e, TurnComplete)]
        assert len(turns) == 1
        # Should default to "end_turn" when attr missing, not crash
        assert turns[0].stop_reason == "end_turn"

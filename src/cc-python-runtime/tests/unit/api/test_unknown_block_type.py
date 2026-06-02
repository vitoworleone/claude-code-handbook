"""Tests for P0-6: Unknown content_block types should log warning, not silently skip."""

from __future__ import annotations

import logging
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


class TestUnknownBlockTypeWarning:
    @pytest.mark.asyncio
    async def test_unknown_block_type_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown content_block type should produce a warning log."""
        events = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(
                type="content_block_start", index=0,
                content_block=MockContentBlock(type="unknown_future_type"),
            ),
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=MockDelta(type="message_delta", stop_reason="end_turn"),
                usage=MockUsage(output_tokens=5),
            ),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=MockStream(events))

        with caplog.at_level(logging.WARNING, logger="cc.api.claude"):
            result_events = [e async for e in stream_response(
                client, messages=[{"role": "user", "content": "x"}], system="s",
            )]

        # Should still complete without crash
        turns = [e for e in result_events if isinstance(e, TurnComplete)]
        assert len(turns) == 1

        # Should have logged a warning about the unknown type
        assert any("unknown_future_type" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_known_types_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Known block types (text, tool_use, thinking) should NOT produce warnings."""
        events = [
            MockEvent(type="message_start", message=MockMessage(usage=MockUsage())),
            MockEvent(type="content_block_start", index=0, content_block=MockContentBlock(type="text")),
            MockEvent(type="content_block_delta", index=0, delta=MockDelta(type="text_delta", text="hi")),
            MockEvent(type="content_block_stop", index=0),
            MockEvent(
                type="message_delta",
                delta=MockDelta(type="message_delta", stop_reason="end_turn"),
                usage=MockUsage(output_tokens=5),
            ),
        ]
        client = MagicMock()
        client.messages.stream = MagicMock(return_value=MockStream(events))

        with caplog.at_level(logging.WARNING, logger="cc.api.claude"):
            [e async for e in stream_response(
                client, messages=[{"role": "user", "content": "x"}], system="s",
            )]

        warning_records = [r for r in caplog.records if "Unknown content_block" in r.message]
        assert len(warning_records) == 0

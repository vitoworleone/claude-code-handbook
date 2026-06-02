"""Tests for P3b: ExtractionCoordinator coalescing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from cc.core.events import TextDelta, TurnComplete
from cc.memory.extractor import ExtractionCoordinator
from cc.models.content_blocks import TextBlock
from cc.models.messages import AssistantMessage, Message, Usage, UserMessage

if TYPE_CHECKING:
    from pathlib import Path


def _make_messages(n: int) -> list[Message]:
    """Create n pairs of user+assistant messages."""
    msgs: list[Message] = []
    for i in range(n):
        msgs.append(UserMessage(content=f"msg {i}"))
        msgs.append(AssistantMessage(content=[TextBlock(text=f"reply {i}")]))
    return msgs


def _make_mock_call_model(delay: float = 0.0) -> Any:
    """Mock call_model that returns empty memories response."""
    async def mock(**kwargs: Any) -> Any:
        if delay:
            await asyncio.sleep(delay)
        yield TextDelta(text='{"memories": []}')
        yield TurnComplete(stop_reason="end_turn", usage=Usage())
    return mock


class TestExtractionCoordinator:
    @pytest.mark.asyncio
    async def test_basic_extraction_runs(self, tmp_path: Path) -> None:
        coord = ExtractionCoordinator()
        msgs = _make_messages(5)
        result = await coord.request_extraction(
            msgs, "/test", _make_mock_call_model(), claude_dir=tmp_path,
        )
        assert isinstance(result, list)
        assert coord.last_extracted_count == 10  # 5 pairs = 10 visible

    @pytest.mark.asyncio
    async def test_short_conversation_skipped(self, tmp_path: Path) -> None:
        coord = ExtractionCoordinator()
        msgs = _make_messages(1)  # Only 2 visible — below MIN_NEW_MESSAGES
        result = await coord.request_extraction(
            msgs, "/test", _make_mock_call_model(), claude_dir=tmp_path,
        )
        assert result == []
        assert coord.last_extracted_count == 0

    @pytest.mark.asyncio
    async def test_concurrent_request_coalesced(self, tmp_path: Path) -> None:
        """Second request while first is running should be coalesced (dirty flag)."""
        coord = ExtractionCoordinator()
        msgs = _make_messages(5)

        call_count = 0
        async def slow_call(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            yield TextDelta(text='{"memories": []}')
            yield TurnComplete(stop_reason="end_turn", usage=Usage())

        # Start first extraction
        task1 = asyncio.create_task(
            coord.request_extraction(msgs, "/test", slow_call, claude_dir=tmp_path)
        )
        await asyncio.sleep(0.02)  # Let it start

        # Second request while first is running — should coalesce
        task2 = asyncio.create_task(
            coord.request_extraction(msgs, "/test", slow_call, claude_dir=tmp_path)
        )

        _r1, r2 = await asyncio.gather(task1, task2)
        # Second should return immediately (coalesced)
        assert r2 == []
        # But dirty flag may cause rerun, so call_count >= 1
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_dirty_flag_causes_rerun(self, tmp_path: Path) -> None:
        """If dirty flag is set during extraction AND new messages arrive, rerun."""
        coord = ExtractionCoordinator()
        msgs = _make_messages(5)

        run_count = 0
        async def counting_call(**kwargs: Any) -> Any:
            nonlocal run_count
            run_count += 1
            # On first run, simulate new messages arriving + dirty flag
            if run_count == 1:
                # Add more messages to make increment >= MIN_NEW_MESSAGES on rerun
                msgs.extend(_make_messages(3))
                coord._dirty = True
            await asyncio.sleep(0.01)
            yield TextDelta(text='{"memories": []}')
            yield TurnComplete(stop_reason="end_turn", usage=Usage())

        await coord.request_extraction(msgs, "/test", counting_call, claude_dir=tmp_path)
        # Should have run at least twice: original + dirty rerun with new messages
        assert run_count >= 2

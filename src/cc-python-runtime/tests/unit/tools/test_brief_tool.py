"""Tests for BriefTool (P5-8).

Verifies conversation summary stats and topic focus.
"""

from __future__ import annotations

import pytest

from cc.tools.brief.brief_tool import BriefTool


class TestBriefTool:
    def test_name(self) -> None:
        tool = BriefTool()
        assert tool.get_name() == "Brief"

    def test_schema(self) -> None:
        tool = BriefTool()
        schema = tool.get_schema()
        assert schema.name == "Brief"
        assert "topic" in schema.input_schema["properties"]

    def test_concurrency_safe(self) -> None:
        tool = BriefTool()
        assert tool.is_concurrency_safe({}) is True

    @pytest.mark.asyncio
    async def test_execute_no_messages(self) -> None:
        tool = BriefTool(message_count=0)
        result = await tool.execute({})
        assert not result.is_error
        assert "Total messages: 0" in result.content
        assert "No messages" in result.content

    @pytest.mark.asyncio
    async def test_execute_short_conversation(self) -> None:
        tool = BriefTool(message_count=3)
        result = await tool.execute({})
        assert not result.is_error
        assert "Total messages: 3" in result.content
        assert "short conversation" in result.content

    @pytest.mark.asyncio
    async def test_execute_moderate_conversation(self) -> None:
        tool = BriefTool(message_count=10)
        result = await tool.execute({})
        assert not result.is_error
        assert "Total messages: 10" in result.content
        assert "moderate-length" in result.content

    @pytest.mark.asyncio
    async def test_execute_long_conversation(self) -> None:
        tool = BriefTool(message_count=25)
        result = await tool.execute({})
        assert not result.is_error
        assert "Total messages: 25" in result.content
        assert "/compact" in result.content

    @pytest.mark.asyncio
    async def test_execute_with_topic(self) -> None:
        tool = BriefTool(message_count=5)
        result = await tool.execute({"topic": "debugging"})
        assert not result.is_error
        assert "debugging" in result.content

    @pytest.mark.asyncio
    async def test_update_message_count(self) -> None:
        tool = BriefTool(message_count=0)
        tool.update_message_count(15)
        result = await tool.execute({})
        assert "Total messages: 15" in result.content

    @pytest.mark.asyncio
    async def test_execute_empty_topic(self) -> None:
        tool = BriefTool(message_count=5)
        result = await tool.execute({"topic": ""})
        assert not result.is_error
        # Empty topic should not add a focus line
        assert "Requested topic focus" not in result.content

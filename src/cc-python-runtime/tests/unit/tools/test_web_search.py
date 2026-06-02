"""Tests for WebSearchTool.

Verifies schema and stub behavior.
"""

import pytest

from cc.tools.web_search.web_search_tool import WebSearchTool


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_search_returns_stub_error(self) -> None:
        tool = WebSearchTool()
        result = await tool.execute({"query": "python testing"})
        assert result.is_error
        assert "API key" in result.content

    @pytest.mark.asyncio
    async def test_empty_query_error(self) -> None:
        tool = WebSearchTool()
        result = await tool.execute({"query": ""})
        assert result.is_error
        assert "required" in result.content

    @pytest.mark.asyncio
    async def test_schema(self) -> None:
        tool = WebSearchTool()
        assert tool.get_name() == "WebSearch"
        schema = tool.get_schema()
        assert schema.name == "WebSearch"
        assert "query" in schema.input_schema["properties"]
        assert "max_results" in schema.input_schema["properties"]
        assert "query" in schema.input_schema["required"]

    @pytest.mark.asyncio
    async def test_concurrency_safe(self) -> None:
        tool = WebSearchTool()
        assert tool.is_concurrency_safe({}) is True

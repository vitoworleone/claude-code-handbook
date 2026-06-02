"""Tests for ToolSearchTool.

Verifies searching registered tools by name and description.
"""

import pytest

from cc.tools.base import ToolRegistry
from cc.tools.tool_search.tool_search_tool import ToolSearchTool


class _DummyTool:
    """Minimal tool for testing."""

    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description

    def get_name(self) -> str:
        return self._name

    def get_schema(self):
        from cc.tools.base import ToolSchema

        return ToolSchema(
            name=self._name,
            description=self._description,
            input_schema={"type": "object", "properties": {}},
        )

    async def execute(self, tool_input: dict) -> object:
        pass


def _make_registry_with_tools() -> ToolRegistry:
    """Create a registry with some dummy tools."""
    registry = ToolRegistry()
    registry.register(_DummyTool("Read", "Reads a file from the filesystem"))  # type: ignore[arg-type]
    registry.register(_DummyTool("Write", "Writes content to a file"))  # type: ignore[arg-type]
    registry.register(_DummyTool("Bash", "Execute a bash command"))  # type: ignore[arg-type]
    registry.register(_DummyTool("WebSearch", "Search the web for information"))  # type: ignore[arg-type]
    registry.register(_DummyTool("WebFetch", "Fetch a URL and return content"))  # type: ignore[arg-type]
    return registry


class TestToolSearchTool:
    @pytest.mark.asyncio
    async def test_search_by_name(self) -> None:
        registry = _make_registry_with_tools()
        tool = ToolSearchTool(registry)

        result = await tool.execute({"query": "Read"})
        assert not result.is_error
        assert "Read" in result.content

    @pytest.mark.asyncio
    async def test_search_by_description(self) -> None:
        registry = _make_registry_with_tools()
        tool = ToolSearchTool(registry)

        result = await tool.execute({"query": "file"})
        assert not result.is_error
        assert "Read" in result.content
        assert "Write" in result.content

    @pytest.mark.asyncio
    async def test_search_web_tools(self) -> None:
        registry = _make_registry_with_tools()
        tool = ToolSearchTool(registry)

        result = await tool.execute({"query": "web"})
        assert not result.is_error
        assert "WebSearch" in result.content
        assert "WebFetch" in result.content

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        registry = _make_registry_with_tools()
        tool = ToolSearchTool(registry)

        result = await tool.execute({"query": "nonexistent_xyz"})
        assert not result.is_error
        assert "No tools found" in result.content

    @pytest.mark.asyncio
    async def test_search_max_results(self) -> None:
        registry = _make_registry_with_tools()
        tool = ToolSearchTool(registry)

        result = await tool.execute({"query": "a", "max_results": 2})
        assert not result.is_error
        # Should return at most 2 results
        lines = [line for line in result.content.split("\n") if line.startswith("- ")]
        assert len(lines) <= 2

    @pytest.mark.asyncio
    async def test_search_empty_query(self) -> None:
        registry = _make_registry_with_tools()
        tool = ToolSearchTool(registry)

        result = await tool.execute({"query": ""})
        assert result.is_error
        assert "required" in result.content

    @pytest.mark.asyncio
    async def test_schema(self) -> None:
        registry = ToolRegistry()
        tool = ToolSearchTool(registry)

        assert tool.get_name() == "ToolSearch"
        schema = tool.get_schema()
        assert schema.name == "ToolSearch"
        assert "query" in schema.input_schema["properties"]
        assert "max_results" in schema.input_schema["properties"]

    @pytest.mark.asyncio
    async def test_concurrency_safe(self) -> None:
        registry = ToolRegistry()
        tool = ToolSearchTool(registry)
        assert tool.is_concurrency_safe({}) is True

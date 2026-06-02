"""Tests for LSPTool.

Verifies schema, stub behavior, and registration.
"""

import pytest

from cc.tools.lsp.lsp_tool import LSP_TOOL_NAME, LSPTool


class TestLSPTool:
    @pytest.mark.asyncio
    async def test_stub_returns_config_message(self) -> None:
        tool = LSPTool()
        result = await tool.execute({
            "action": "diagnostics",
            "file_path": "/tmp/test.py",
            "line": 0,
        })
        assert result.is_error
        assert "language server" in result.content.lower()
        assert "settings.json" in result.content

    @pytest.mark.asyncio
    async def test_invalid_action_error(self) -> None:
        tool = LSPTool()
        result = await tool.execute({
            "action": "invalid",
            "file_path": "/tmp/test.py",
            "line": 0,
        })
        assert result.is_error
        assert "invalid action" in result.content.lower()

    @pytest.mark.asyncio
    async def test_missing_file_path_error(self) -> None:
        tool = LSPTool()
        result = await tool.execute({
            "action": "hover",
            "file_path": "",
            "line": 5,
        })
        assert result.is_error
        assert "file_path" in result.content

    @pytest.mark.asyncio
    async def test_all_actions_return_stub(self) -> None:
        tool = LSPTool()
        for action in ("diagnostics", "hover", "definition", "references"):
            result = await tool.execute({
                "action": action,
                "file_path": "/tmp/test.py",
                "line": 10,
                "character": 5,
            })
            assert result.is_error
            assert "language server" in result.content.lower()

    def test_schema(self) -> None:
        tool = LSPTool()
        assert tool.get_name() == LSP_TOOL_NAME
        assert tool.get_name() == "LSP"

        schema = tool.get_schema()
        assert schema.name == "LSP"
        props = schema.input_schema["properties"]
        assert "action" in props
        assert "file_path" in props
        assert "line" in props
        assert "character" in props
        assert props["action"]["enum"] == ["diagnostics", "hover", "definition", "references"]
        assert schema.input_schema["required"] == ["action", "file_path", "line"]

    def test_concurrency_safe(self) -> None:
        tool = LSPTool()
        assert tool.is_concurrency_safe({}) is True

    def test_registered_in_build_registry(self) -> None:
        """LSPTool should be registered by _build_registry."""
        from cc.main import _build_registry

        registry = _build_registry(cwd="/tmp")
        lsp = registry.get("LSP")
        assert lsp is not None
        assert isinstance(lsp, LSPTool)

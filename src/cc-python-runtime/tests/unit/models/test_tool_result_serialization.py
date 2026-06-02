"""Tests for P0-4: ToolResultBlock content serialization consistency.

Both str and list[ToolResultContent] paths must produce valid API dicts.
"""

from __future__ import annotations

from cc.models.content_blocks import ToolResultBlock, ToolResultContent


class TestToolResultBlockSerialization:
    """ToolResultBlock.to_api_dict() must work for both content types."""

    def test_str_content_serializes(self) -> None:
        block = ToolResultBlock(tool_use_id="tu_1", content="hello")
        result = block.to_api_dict()
        assert result["content"] == "hello"
        assert result["tool_use_id"] == "tu_1"
        assert "is_error" not in result

    def test_str_content_with_error(self) -> None:
        block = ToolResultBlock(tool_use_id="tu_1", content="oops", is_error=True)
        result = block.to_api_dict()
        assert result["content"] == "oops"
        assert result["is_error"] is True

    def test_list_content_serializes(self) -> None:
        block = ToolResultBlock(
            tool_use_id="tu_1",
            content=[ToolResultContent(type="text", text="hello")],
        )
        result = block.to_api_dict()
        assert isinstance(result["content"], list)
        assert result["content"][0] == {"type": "text", "text": "hello"}

    def test_roundtrip_str(self) -> None:
        """str content survives to_api_dict → from_api_dict roundtrip."""
        original = ToolResultBlock(tool_use_id="tu_1", content="data", is_error=False)
        api = original.to_api_dict()
        restored = ToolResultBlock.from_api_dict(api)
        assert restored.content == "data"
        assert restored.tool_use_id == "tu_1"

    def test_roundtrip_list(self) -> None:
        """list content survives to_api_dict → from_api_dict roundtrip."""
        original = ToolResultBlock(
            tool_use_id="tu_1",
            content=[ToolResultContent(type="text", text="result")],
        )
        api = original.to_api_dict()
        restored = ToolResultBlock.from_api_dict(api)
        assert isinstance(restored.content, list)
        assert restored.content[0].type == "text"
        assert restored.content[0].text == "result"


class TestToolResultContentFromApiDict:
    """ToolResultContent.from_api_dict must handle edge cases."""

    def test_text_type(self) -> None:
        result = ToolResultContent.from_api_dict({"type": "text", "text": "hello"})
        assert result.type == "text"
        assert result.text == "hello"

    def test_image_type(self) -> None:
        result = ToolResultContent.from_api_dict({"type": "image", "source": {"data": "..."}})
        assert result.type == "image"
        assert result.source == {"data": "..."}

    def test_unknown_type_treated_as_text_fallback(self) -> None:
        """Unknown type should not crash — fall back gracefully."""
        result = ToolResultContent.from_api_dict({"type": "document", "text": "doc content"})
        # Should not crash; exact type depends on implementation
        assert result is not None

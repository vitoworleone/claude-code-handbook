"""Tests for P0-3: message normalization tool_use/tool_result pairing.

TS behavior (utils/messages.ts ensureToolResultPairing):
- Orphaned tool_result (no matching tool_use): REMOVED
- Orphaned tool_use (no matching tool_result): ADD SYNTHETIC ERROR RESULT
"""

from __future__ import annotations

from typing import Any

from cc.models.messages import normalize_messages_for_api

# Constants matching TS
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"


def _make_assistant_with_tool_use(tool_use_id: str = "tu_1") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": tool_use_id, "name": "bash", "input": {"command": "ls"}},
        ],
    }


def _make_user_with_tool_result(tool_use_id: str = "tu_1", content: str = "file.txt") -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": content},
        ],
    }


class TestOrphanedToolResult:
    """Orphaned tool_results (no matching tool_use) should be removed."""

    def test_orphaned_tool_result_removed(self) -> None:
        """tool_result without matching tool_use gets stripped."""
        from cc.models.content_blocks import TextBlock, ToolResultBlock
        from cc.models.messages import AssistantMessage, UserMessage

        messages = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="hi")]),  # No tool_use
            UserMessage(content=[ToolResultBlock(tool_use_id="orphan_1", content="data")]),
        ]
        result = normalize_messages_for_api(messages)

        # The orphaned tool_result should be removed
        last_user = result[-1]
        assert last_user["role"] == "user"
        content = last_user["content"]
        if isinstance(content, list):
            tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            assert len(tool_results) == 0


class TestOrphanedToolUse:
    """Orphaned tool_uses (no matching tool_result) should get synthetic error result."""

    def test_synthetic_result_added_for_orphaned_tool_use(self) -> None:
        """tool_use without tool_result gets synthetic error tool_result injected."""
        from cc.models.content_blocks import TextBlock, ToolUseBlock
        from cc.models.messages import AssistantMessage, UserMessage

        messages = [
            UserMessage(content="list files"),
            AssistantMessage(content=[
                TextBlock(text="Let me check."),
                ToolUseBlock(id="tu_1", name="bash", input={"command": "ls"}),
            ]),
            # No user message with tool_result for tu_1!
            UserMessage(content="thanks"),
        ]
        result = normalize_messages_for_api(messages)

        # Find the user message after the assistant with tool_use
        # It should contain a synthetic tool_result for tu_1
        found_synthetic = False
        for msg in result:
            if msg["role"] == "user":
                content = msg["content"]
                if isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and block.get("tool_use_id") == "tu_1"
                            and block.get("is_error") is True
                        ):
                            found_synthetic = True
                            assert SYNTHETIC_TOOL_RESULT_PLACEHOLDER in block.get("content", "")

        assert found_synthetic, "Expected synthetic error tool_result for orphaned tool_use tu_1"

    def test_matched_pair_unchanged(self) -> None:
        """Properly paired tool_use/tool_result should not be modified."""
        from cc.models.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
        from cc.models.messages import AssistantMessage, UserMessage

        messages = [
            UserMessage(content="list files"),
            AssistantMessage(content=[
                TextBlock(text="Let me check."),
                ToolUseBlock(id="tu_1", name="bash", input={"command": "ls"}),
            ]),
            UserMessage(content=[ToolResultBlock(tool_use_id="tu_1", content="file.txt")]),
        ]
        result = normalize_messages_for_api(messages)

        # Find the tool_result — it should be preserved
        user_after = result[-1]
        assert user_after["role"] == "user"
        content = user_after["content"]
        assert isinstance(content, list)
        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["tool_use_id"] == "tu_1"
        assert tool_results[0].get("is_error") is not True

    def test_multiple_tool_uses_partial_results(self) -> None:
        """Two tool_uses but only one tool_result — missing one gets synthetic."""
        from cc.models.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
        from cc.models.messages import AssistantMessage, UserMessage

        messages = [
            UserMessage(content="do two things"),
            AssistantMessage(content=[
                TextBlock(text="OK"),
                ToolUseBlock(id="tu_1", name="bash", input={"command": "ls"}),
                ToolUseBlock(id="tu_2", name="bash", input={"command": "pwd"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="tu_1", content="file.txt"),
                # Missing tu_2 result!
            ]),
        ]
        result = normalize_messages_for_api(messages)

        # Find all tool_results in the user message after assistant
        user_msg = result[-1]
        content = user_msg["content"]
        assert isinstance(content, list)
        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]

        # Should have both: tu_1 (real) and tu_2 (synthetic)
        tu_ids = {tr["tool_use_id"] for tr in tool_results}
        assert "tu_1" in tu_ids
        assert "tu_2" in tu_ids

        # tu_2 should be synthetic error
        tu_2_result = next(tr for tr in tool_results if tr["tool_use_id"] == "tu_2")
        assert tu_2_result["is_error"] is True

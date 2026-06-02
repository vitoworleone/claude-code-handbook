"""Tests for P0.5b: Session recovery — transcript validation and repair."""

from __future__ import annotations

from cc.models.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from cc.models.messages import AssistantMessage, Message, UserMessage
from cc.session.recovery import validate_transcript


class TestValidateTranscriptTruncation:
    """Transcript ending with assistant tool_use (crash during tool execution)."""

    def test_truncated_transcript_gets_synthetic_result(self) -> None:
        """If transcript ends with assistant tool_use, add synthetic error result."""
        messages: list[Message] = [
            UserMessage(content="list files"),
            AssistantMessage(content=[
                TextBlock(text="Let me check."),
                ToolUseBlock(id="tu_1", name="bash", input={"command": "ls"}),
            ]),
            # Process crashed here — no tool result
        ]
        result = validate_transcript(messages)

        assert len(result) == 3  # Original 2 + synthetic user message
        last = result[-1]
        assert isinstance(last, UserMessage)
        assert isinstance(last.content, list)
        tool_results = [b for b in last.content if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_use_id == "tu_1"
        assert tool_results[0].is_error is True

    def test_normal_transcript_unchanged(self) -> None:
        """Properly paired transcript should not be modified."""
        messages: list[Message] = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="hi")]),
        ]
        result = validate_transcript(messages)
        assert len(result) == 2

    def test_empty_transcript(self) -> None:
        assert validate_transcript([]) == []


class TestValidateTranscriptMidOrphans:
    """Mid-transcript orphaned tool_uses (assistant has tool_use, next user lacks result)."""

    def test_mid_transcript_missing_result_patched(self) -> None:
        messages: list[Message] = [
            UserMessage(content="do it"),
            AssistantMessage(content=[
                ToolUseBlock(id="tu_1", name="bash", input={}),
            ]),
            UserMessage(content="thanks"),  # No tool_result for tu_1!
            AssistantMessage(content=[TextBlock(text="done")]),
        ]
        result = validate_transcript(messages)

        # The "thanks" message should now include a synthetic tool_result
        user_msg = result[2]
        assert isinstance(user_msg, UserMessage)
        assert isinstance(user_msg.content, list)
        tool_results = [b for b in user_msg.content if isinstance(b, ToolResultBlock)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_use_id == "tu_1"
        assert tool_results[0].is_error is True

    def test_properly_paired_not_patched(self) -> None:
        messages: list[Message] = [
            UserMessage(content="do it"),
            AssistantMessage(content=[
                ToolUseBlock(id="tu_1", name="bash", input={}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="tu_1", content="result"),
            ]),
        ]
        result = validate_transcript(messages)
        # Should be unchanged
        assert len(result) == 3
        user_msg = result[2]
        assert isinstance(user_msg, UserMessage)
        assert isinstance(user_msg.content, list)
        assert len(user_msg.content) == 1  # Only the original result

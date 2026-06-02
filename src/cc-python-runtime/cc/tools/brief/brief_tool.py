"""BriefTool — provide a brief summary of the current conversation.

Corresponds to TS: tools/Brief (P5-8).
"""

from __future__ import annotations

import logging
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

BRIEF_TOOL_NAME = "Brief"


class BriefTool(Tool):
    """Return a brief summary / stats of the current conversation.

    Since tools don't have direct access to the full conversation history,
    this provides basic stats based on an injected message count or returns
    a placeholder summary.

    该工具的设计目标是让模型感知对话的"体量"：短对话无需压缩，
    长对话时应建议用户使用 /compact 命令压缩上下文，以避免
    超出 token 上限或降低响应质量。
    """

    def __init__(self, message_count: int = 0) -> None:
        # 消息计数由引擎在每轮对话后注入更新，
        # 而不是让工具直接访问对话历史（保持解耦）
        self._message_count = message_count

    def get_name(self) -> str:
        return BRIEF_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=BRIEF_TOOL_NAME,
            description="Return a brief summary of the current conversation with basic stats.",
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Optional topic to focus the summary on",
                    },
                },
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 只读取内部计数器，不修改任何状态，可安全并发
        return True

    def update_message_count(self, count: int) -> None:
        """Update the current message count (called by the engine).
        由引擎在对话循环中调用，保持消息计数与实际对话同步。
        """
        self._message_count = count

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        topic = tool_input.get("topic", "")
        count = self._message_count

        lines = [
            "Conversation Summary",
            f"Total messages: {count}",
        ]

        if topic:
            lines.append(f"Requested topic focus: {topic}")

        # 根据消息数量分级给出建议，阈值参考了典型对话的上下文消耗
        if count == 0:
            lines.append("No messages in the conversation yet.")
        elif count < 5:
            lines.append("This is a short conversation so far.")
        elif count < 20:
            lines.append("This is a moderate-length conversation.")
        else:
            # 长对话容易触及上下文窗口限制，建议压缩
            lines.append("This is a lengthy conversation. Consider using /compact to reduce context.")

        return ToolResult(content="\n".join(lines))

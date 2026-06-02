"""Context compaction — summarize old messages to free context.

Corresponds to TS: services/compact/compact.ts + autoCompact.ts.

上下文压缩模块：当对话历史消耗的 token 接近上下文窗口上限时，
将较旧的消息汇总为一段简短摘要，腾出空间给后续对话使用。
这是长对话能持续进行的关键机制。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cc.models.messages import (
    AssistantMessage,
    CompactBoundaryMessage,
    Message,
    UserMessage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from cc.core.events import QueryEvent

logger = logging.getLogger(__name__)

# 对应 TS: services/compact/autoCompact.ts 中的阈值常量
# 当已使用 token 超过（上下文窗口 - 缓冲区）时触发自动压缩
AUTO_COMPACT_BUFFER = 13_000  # 保留 13000 token 的缓冲区，为新的用户输入和模型回复留出空间
# 连续压缩失败达到此次数后停止重试，避免无限循环浪费 API 调用
MAX_CONSECUTIVE_FAILURES = 3
# 压缩后保留最近 N 轮对话（一轮 = user + assistant），
# 保留最近的上下文是因为模型需要知道"刚才在做什么"以保持连贯性
POST_COMPACT_KEEP_TURNS = 4  # Keep last N user-assistant pairs


# 压缩用的系统提示词 —— 对应 TS: services/compact/compact.ts
# 指导模型生成结构化摘要，重点保留文件路径、函数名等具体信息，
# 而非笼统的描述。这样压缩后模型仍能准确地引用之前提到的代码位置。
COMPACT_SYSTEM_PROMPT = """You are a conversation summarizer. Given a conversation between a user and an assistant, create a concise summary that preserves:

1. Key decisions and outcomes
2. Important file paths, function names, and code changes
3. Current state of the task
4. Any unresolved issues or next steps

Be factual and specific. Include exact file paths, line numbers, and code identifiers mentioned. Do not editorialize or add opinions. Output only the summary text."""


async def compact_messages(
    messages: list[Message],
    call_model: Callable[..., AsyncIterator[QueryEvent]],
) -> list[Message]:
    """Compact a conversation by summarizing old messages.

    Corresponds to TS: services/compact/compact.ts core compaction logic.

    Keeps the last POST_COMPACT_KEEP_TURNS of conversation and summarizes
    the rest into a CompactBoundaryMessage.

    压缩流程：
    1. 将消息列表拆分为"旧消息"（待压缩）和"最近消息"（保留）
    2. 将旧消息格式化为文本，调用模型生成摘要
    3. 用 CompactBoundaryMessage（包含摘要）+ 最近消息 替换原始列表
    4. 如果压缩失败（模型错误、空摘要），返回原始消息列表（安全降级）

    Args:
        messages: Full conversation messages.
        call_model: API call function for generating the summary.

    Returns:
        Compacted message list with summary boundary.
    """
    # 消息太少时不值得压缩（至少需要保留的轮数 + 1 轮可压缩的）
    if len(messages) < POST_COMPACT_KEEP_TURNS * 2 + 2:
        # Not enough messages to compact
        return messages

    # 按轮数拆分：保留最近 N 轮（每轮 = user + assistant 两条消息）
    keep_count = POST_COMPACT_KEEP_TURNS * 2  # user + assistant pairs
    old_messages = messages[:-keep_count]
    recent_messages = messages[-keep_count:]

    # 将待压缩的旧消息转换为纯文本格式
    conversation_text = _messages_to_text(old_messages)

    # 调用模型生成摘要
    from cc.core.events import TextDelta, TurnComplete
    from cc.models.messages import normalize_messages_for_api

    summary_messages: list[Message] = [
        UserMessage(content=f"Summarize this conversation:\n\n{conversation_text}"),
    ]
    api_messages = normalize_messages_for_api(summary_messages)

    # 流式收集摘要文本
    summary_parts: list[str] = []
    try:
        async for event in call_model(
            messages=api_messages,
            system=COMPACT_SYSTEM_PROMPT,
            tools=None,
        ):
            if isinstance(event, TextDelta):
                summary_parts.append(event.text)
            elif isinstance(event, TurnComplete):
                break
    except Exception as e:
        # 压缩失败时安全降级，返回原始消息列表，不中断用户对话
        logger.warning("Compact failed: %s", e)
        return messages  # Return original on failure

    summary = "".join(summary_parts)
    if not summary.strip():
        # 空摘要说明模型未能有效总结，可能是输入太短或模型异常
        logger.warning("Compact produced empty summary")
        return messages

    # 用压缩边界消息替代所有旧消息
    # CompactBoundaryMessage 在后续对话中会被展开为系统提示的一部分
    boundary = CompactBoundaryMessage(summary=summary)
    return [boundary, *recent_messages]


def should_auto_compact(
    estimated_tokens: int,
    context_window: int = 200_000,
    consecutive_failures: int = 0,
) -> bool:
    """Check if auto-compaction should trigger.

    Corresponds to TS: services/compact/autoCompact.ts shouldAutoCompact().

    触发条件：
    1. 连续失败次数未超过上限（避免死循环）
    2. 已使用 token 数达到阈值（上下文窗口 - 缓冲区）

    缓冲区的意义：留出空间让用户能继续输入，而不是在 token 恰好满时才压缩，
    那时可能已经没有足够空间处理新消息了。
    """
    # 连续失败过多次后停止重试，避免浪费 API 调用
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return False

    # 阈值 = 上下文窗口大小 - 缓冲区
    threshold = context_window - AUTO_COMPACT_BUFFER
    return estimated_tokens >= threshold


def _messages_to_text(messages: list[Message]) -> str:
    """Convert messages to plain text for summarization.

    FIX (check.md #9): Include tool result details instead of folding to "[tool results]".

    将结构化消息转换为人类可读的文本格式，供压缩模型阅读。
    与 extractor 中的 _format_messages_for_extraction 不同，这里会展开
    tool_result 的具体内容（截断到 500 字符），因为压缩摘要需要保留
    工具执行的关键结果信息（如文件内容、命令输出等）。
    """
    from cc.models.content_blocks import ToolResultBlock

    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                parts.append(f"User: {msg.content}")
            else:
                # 展开 content blocks，特别是 tool_result 的内容
                sub_parts = []
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        content_preview = block.content if isinstance(block.content, str) else "[structured]"
                        # 截断过长的工具输出，防止摘要请求的 token 超限
                        if len(content_preview) > 500:
                            content_preview = content_preview[:500] + "..."
                        sub_parts.append(f"  tool_result({block.tool_use_id}): {content_preview}")
                    else:
                        sub_parts.append(f"  {block}")
                parts.append("User (tool results):\n" + "\n".join(sub_parts))
        elif isinstance(msg, AssistantMessage):
            text = msg.get_text()
            tool_uses = msg.get_tool_use_blocks()
            lines = []
            if text:
                lines.append(text)
            # 记录模型调用了哪些工具及其输入参数，保留在摘要中
            for tu in tool_uses:
                lines.append(f"  [called {tu.name}({tu.input})]")
            if lines:
                parts.append("Assistant: " + "\n".join(lines))
        elif isinstance(msg, CompactBoundaryMessage):
            # 如果旧消息中已包含之前的压缩摘要，将其嵌入到当前文本中
            parts.append(f"[Previous summary: {msg.summary}]")
    return "\n\n".join(parts)

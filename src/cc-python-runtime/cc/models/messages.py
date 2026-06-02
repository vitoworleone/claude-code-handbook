"""Message types for the conversation system.

Corresponds to TS: types/message.ts (DCE'd) + utils/messages.ts.
Reconstructed from usage patterns in query.ts, utils/messages.ts, Tool.ts.

本模块定义了对话系统中的四种消息类型：
  1. UserMessage      — 用户发送的消息
  2. AssistantMessage  — 助手（模型）的回复
  3. SystemMessage     — 系统级通知（信息/警告/错误）
  4. CompactBoundaryMessage — 压缩边界标记（标记该点之前的消息已被摘要压缩）

核心函数：
  - normalize_messages_for_api(): 将内部消息列表转换为 Anthropic API 兼容格式
  - get_messages_after_compact_boundary(): 截取压缩边界之后的消息
  - 工厂函数 create_user_message / create_assistant_message / create_tool_result_message
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Literal
from uuid import uuid4

from .content_blocks import (
    AssistantContentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserContentBlock,
)

_logger = logging.getLogger(__name__)


# =============================================================================
# Token 使用量统计
# =============================================================================
@dataclass
class Usage:
    """Token usage statistics from API response.

    Corresponds to TS: @anthropic-ai/sdk BetaUsage.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    # 以下两个字段与 Prompt Caching 相关：
    # cache_creation_input_tokens: 本次请求中被写入缓存的 token 数
    cache_creation_input_tokens: int = 0
    # cache_read_input_tokens: 本次请求中从缓存命中读取的 token 数
    cache_read_input_tokens: int = 0


# =============================================================================
# 四种消息类型定义
# =============================================================================

@dataclass
class UserMessage:
    """A user message in the conversation.

    Corresponds to TS: types/message.ts UserMessage.
    """

    # content 可以是纯文本字符串，也可以是 UserContentBlock 列表（如包含 tool_result 时）
    content: str | list[UserContentBlock]
    # 每条消息的唯一标识，用于会话存储和恢复
    uuid: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = ""
    type: Literal["user"] = "user"
    # is_meta 标记这是否为系统自动生成的元消息（如权限确认、hook 反馈等），
    # 而非用户真正输入的内容
    is_meta: bool = False
    # is_compact_summary 标记该消息是否为压缩摘要，用于上下文管理
    is_compact_summary: bool = False

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to Anthropic API MessageParam format."""
        if isinstance(self.content, str):
            api_content: str | list[dict[str, Any]] = self.content
        else:
            api_content = [block.to_api_dict() for block in self.content]
        return {"role": "user", "content": api_content}


@dataclass
class AssistantMessage:
    """An assistant message in the conversation.

    Corresponds to TS: types/message.ts AssistantMessage.
    Contains the raw API response message plus metadata.
    """

    # 内容块列表，一次回复可能包含多个块（如 thinking + text + tool_use）
    content: list[AssistantContentBlock] = field(default_factory=list)
    uuid: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = ""
    type: Literal["assistant"] = "assistant"
    # stop_reason 记录模型停止生成的原因：
    # "end_turn"（正常结束）/ "tool_use"（需要调用工具）/ "max_tokens"（达到上限）等
    stop_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    # 记录生成此消息所用的模型标识符（如 "claude-sonnet-4-20250514"）
    model: str = ""
    # 标记该消息是否因 API 错误而生成（如速率限制、服务器错误）
    is_api_error: bool = False

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to Anthropic API MessageParam format."""
        return {
            "role": "assistant",
            "content": [block.to_api_dict() for block in self.content],
        }

    def get_text(self) -> str:
        """Extract concatenated text from all text blocks.

        将所有 TextBlock 的文本拼接返回，忽略 thinking/tool_use 等其他块。
        用于从混合内容中提取模型的文字回复。
        """
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    def get_tool_use_blocks(self) -> list[ToolUseBlock]:
        """Extract all tool use blocks.

        用于在 query_loop 中判断模型是否请求了工具调用，以决定下一步状态转移。
        """
        return [block for block in self.content if isinstance(block, ToolUseBlock)]


@dataclass
class SystemMessage:
    """A system-level message (informational, error, compact boundary, etc.).

    Corresponds to TS: types/message.ts SystemMessage union.

    系统消息不会发送给 API，仅在本地对话历史中存在，用于向用户展示系统级通知。
    在 normalize_messages_for_api() 中会被过滤掉。
    """

    content: str
    # level 决定 UI 中的展示样式（信息/警告/错误）
    level: Literal["info", "warning", "error"] = "info"
    type: Literal["system"] = "system"
    uuid: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = ""


@dataclass
class CompactBoundaryMessage:
    """Marks a compaction boundary. Messages before this were summarized.

    Corresponds to TS: types/message.ts SystemCompactBoundaryMessage.

    压缩边界标记——在自动压缩（auto-compact）触发后，将之前的对话摘要
    存入 summary 字段，然后在消息列表中插入此标记。后续处理时，
    get_messages_after_compact_boundary() 会从最后一个边界处截取，
    而 normalize_messages_for_api() 会将其转换为包含摘要的 user 消息。
    """

    # 对之前对话内容的压缩摘要文本
    summary: str
    type: Literal["compact_boundary"] = "compact_boundary"
    uuid: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = ""


# 对话中所有消息类型的联合
Message = UserMessage | AssistantMessage | SystemMessage | CompactBoundaryMessage


# =============================================================================
# normalize_messages_for_api — 消息规范化（三步修复）
#
# Anthropic Messages API 有严格的消息格式要求：
#   (a) 消息必须 user/assistant 交替出现
#   (b) 对话必须以 user 消息开头
#   (c) 每个 tool_use 都必须有配对的 tool_result
#
# 本函数负责将内部自由格式的消息列表修复为符合上述要求的格式。
# =============================================================================
def normalize_messages_for_api(messages: list[Message]) -> list[dict[str, Any]]:
    """Normalize conversation messages into Anthropic API format.

    Corresponds to TS: utils/messages.ts normalizeMessagesForAPI().

    Ensures:
    - Only user and assistant messages are included
    - Messages alternate between user and assistant
    - tool_use / tool_result blocks are properly paired
    - No orphaned tool_results
    """
    api_messages: list[dict[str, Any]] = []

    # 第一步：遍历所有消息，转换为 API dict 格式，并处理角色交替问题
    for msg in messages:
        if isinstance(msg, (SystemMessage, CompactBoundaryMessage)):
            # SystemMessage 直接跳过（不发给 API）
            # CompactBoundaryMessage 转换为包含摘要的 user 消息，保留对话上下文
            if isinstance(msg, CompactBoundaryMessage):
                api_msg = {"role": "user", "content": f"[Previous conversation summary]\n{msg.summary}"}
            else:
                continue
        elif isinstance(msg, (UserMessage, AssistantMessage)):
            api_msg = msg.to_api_dict()
        else:
            continue

        # 确保角色交替：连续相同角色的消息需要修复
        if api_messages and api_messages[-1]["role"] == api_msg["role"]:
            if api_msg["role"] == "user":
                # 连续 user 消息 → 合并到前一条（避免插入无意义的 assistant 占位符）
                _merge_user_messages(api_messages[-1], api_msg)
                continue
            else:
                # 连续 assistant 消息 → 中间插入占位 user 消息以满足交替要求
                api_messages.append({"role": "user", "content": "Continue."})

        api_messages.append(api_msg)

    # 第二步：确保对话以 user 消息开头（API 要求）
    if api_messages and api_messages[0]["role"] == "assistant":
        api_messages.insert(0, {"role": "user", "content": "Begin."})

    # 第三步：修复 tool_use / tool_result 配对关系
    _ensure_tool_result_pairing(api_messages)

    return api_messages


def _merge_user_messages(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge source user message content into target.

    将两条连续的 user 消息合并为一条。
    需要先将纯字符串 content 统一转为 list 格式，然后拼接。
    """
    target_content = target["content"]
    source_content = source["content"]

    # 统一转为 list 格式后再拼接，确保类型一致
    if isinstance(target_content, str):
        target_content = [{"type": "text", "text": target_content}]
    if isinstance(source_content, str):
        source_content = [{"type": "text", "text": source_content}]

    target["content"] = target_content + source_content


# P0-3: 与 TS 原版保持一致的常量——当 tool_use 缺少对应 tool_result 时的占位文本
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"


def _ensure_tool_result_pairing(messages: list[dict[str, Any]]) -> None:
    """Ensure every tool_result has a matching tool_use and vice versa.

    Corresponds to TS: utils/messages.ts ensureToolResultPairing().

    三步双向修复逻辑：
      Pass 1: 收集所有 tool_use ID 和 tool_result ID
      Pass 2: 移除孤儿 tool_result（没有对应的 tool_use）
      Pass 3: 为孤儿 tool_use（没有对应的 tool_result）补充合成错误结果

    为什么需要这个修复？
    - 对话可能因压缩、中断、或手动编辑而产生不配对的 tool_use/tool_result
    - API 要求严格配对，缺配对会导致 400 错误
    - 通过自动修复避免这类运行时崩溃
    """
    # Pass 1: 收集所有 tool_use 和 tool_result 的 ID，用于交叉比对
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if msg["role"] == "assistant":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_use_ids.add(block["id"])
        elif msg["role"] == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_result_ids.add(block.get("tool_use_id", ""))

    # Pass 2: 移除孤儿 tool_result（指向不存在的 tool_use_id）
    # 这种情况可能发生在压缩时 assistant 消息被截断但 tool_result 保留了下来
    for msg in messages:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                msg["content"] = [
                    block
                    for block in content
                    if not (
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        and block.get("tool_use_id") not in tool_use_ids
                    )
                ]
                # 过滤后如果 content 为空，补充占位文本避免 API 报错
                if not msg["content"]:
                    msg["content"] = [{"type": "text", "text": "(no content)"}]

    # Pass 3: 为孤儿 tool_use 补充合成错误结果
    # 找出有 tool_use 但缺少对应 tool_result 的 ID
    missing_ids = tool_use_ids - tool_result_ids
    if missing_ids:
        _logger.warning("tool_result_pairing_repaired: adding synthetic results for %s", missing_ids)
        # 遍历每条 assistant 消息，找到其中的孤儿 tool_use，
        # 然后在紧随其后的 user 消息中注入合成的 tool_result
        for i, msg in enumerate(messages):
            if msg["role"] != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            orphaned_in_msg = [
                block["id"]
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use" and block["id"] in missing_ids
            ]
            if not orphaned_in_msg:
                continue

            # 构造合成的 tool_result 块，标记为错误
            synthetic_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                    "is_error": True,
                }
                for tid in orphaned_in_msg
            ]

            # 向后查找最近的 user 消息，将合成结果注入其中
            injected = False
            for j in range(i + 1, len(messages)):
                if messages[j]["role"] == "user":
                    user_content = messages[j]["content"]
                    if isinstance(user_content, str):
                        # 字符串 content 需要转为 list 格式后再追加
                        messages[j]["content"] = [{"type": "text", "text": user_content}, *synthetic_blocks]
                    elif isinstance(user_content, list):
                        # 合成结果放在前面，确保 tool_result 出现在对应的 user 消息中
                        messages[j]["content"] = synthetic_blocks + user_content
                    injected = True
                    break

            # 如果后面没有 user 消息（tool_use 是最后一条），创建新的 user 消息
            if not injected:
                messages.append({"role": "user", "content": synthetic_blocks})


def get_messages_after_compact_boundary(messages: list[Message]) -> list[Message]:
    """Return messages after the last compact boundary.

    Corresponds to TS: query.ts getMessagesAfterCompactBoundary().

    从消息列表末尾向前搜索，找到最后一个 CompactBoundaryMessage，
    返回从该边界（含）到末尾的所有消息。这样做的目的是：
    - 压缩边界之前的消息已被摘要，不需要再发给 API
    - 边界本身会被 normalize_messages_for_api() 转为包含摘要的 user 消息
    - 如果没有压缩边界，返回完整的消息列表副本
    """
    # 从后往前找，因为可能有多次压缩，只关心最后一次
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], CompactBoundaryMessage):
            return messages[i:]
    return list(messages)


# =============================================================================
# 工厂函数 — 便捷创建各类消息
# 封装了时间戳生成、类型包装等样板逻辑。
# =============================================================================

def create_user_message(content: str | list[UserContentBlock], **kwargs: Any) -> UserMessage:
    """Factory for creating user messages.

    Corresponds to TS: utils/messages.ts createUserMessage().

    自动填充 timestamp（如果调用者未提供），避免每处创建消息时都手动获取时间。
    """
    from datetime import datetime

    return UserMessage(
        content=content,
        timestamp=kwargs.get("timestamp", datetime.now(UTC).isoformat()),
        # 将 kwargs 中除 timestamp 以外的参数传递给 UserMessage（如 is_meta 等）
        **{k: v for k, v in kwargs.items() if k != "timestamp"},
    )


def create_assistant_message(
    content: str | list[AssistantContentBlock],
    usage: Usage | None = None,
    stop_reason: str | None = None,
) -> AssistantMessage:
    """Factory for creating assistant messages.

    Corresponds to TS: utils/messages.ts createAssistantMessage().

    支持传入纯字符串（自动包装为 TextBlock）或预构建的 content block 列表。
    """
    from datetime import datetime

    # 纯字符串便捷模式：自动包装为单个 TextBlock
    if isinstance(content, str):
        blocks: list[AssistantContentBlock] = [TextBlock(text=content)]
    else:
        blocks = content

    return AssistantMessage(
        content=blocks,
        usage=usage or Usage(),
        stop_reason=stop_reason,
        timestamp=datetime.now(UTC).isoformat(),
    )


def create_tool_result_message(tool_use_id: str, content: str, is_error: bool = False) -> UserMessage:
    """Create a user message containing a tool result.

    Corresponds to TS pattern of wrapping tool_result in UserMessage.

    Anthropic API 要求 tool_result 必须包裹在 user 角色的消息中发送。
    此函数封装了这一 API 约定，调用者只需提供 tool_use_id 和结果内容。
    """
    return create_user_message(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)],
    )

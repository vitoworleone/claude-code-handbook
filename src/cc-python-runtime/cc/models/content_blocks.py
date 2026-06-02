"""Content block types for API communication.

Corresponds to TS: @anthropic-ai/sdk ContentBlock types + custom extensions.
These map directly to the Anthropic Messages API content block format.

本模块定义了与 Anthropic Messages API 通信时使用的 7 种内容块（ContentBlock）类型。
每种类型都实现了 to_api_dict() / from_api_dict() 这对序列化/反序列化方法，
保证对象 ↔ API dict 之间可以无损往返转换（roundtrip）。

类型归属关系：
  - AssistantContentBlock（助手消息可包含的块）：TextBlock, ToolUseBlock, ThinkingBlock, RedactedThinkingBlock
  - UserContentBlock（用户消息可包含的块）：TextBlock, ToolResultBlock, ImageBlock
  - ContentBlock（所有块类型的联合）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


# =============================================================================
# 1. TextBlock — 纯文本内容块
# =============================================================================
@dataclass
class TextBlock:
    """A text content block."""

    # 文本内容，是最基本的内容块类型，助手和用户消息都可以包含
    text: str
    # type 字段使用 Literal 类型约束，保证序列化时类型标识正确
    type: Literal["text"] = "text"

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> TextBlock:
        return cls(text=data["text"])


# =============================================================================
# 2. ToolUseBlock — 工具调用请求块（由模型生成，出现在助手消息中）
# =============================================================================
@dataclass
class ToolUseBlock:
    """A tool use content block (model requesting tool execution)."""

    # id 是 API 分配的唯一标识符，后续 ToolResultBlock 通过 tool_use_id 与之配对
    id: str
    # 工具名称，如 "Bash", "Read", "Edit" 等
    name: str
    # 工具输入参数，结构由各工具的 JSON Schema 定义
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "name": self.name, "input": self.input}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> ToolUseBlock:
        return cls(id=data["id"], name=data["name"], input=data["input"])


# =============================================================================
# 3. ToolResultContent — 工具结果中的具体内容（可以是文本或图片）
#    ToolResultBlock 的 content 字段可以是字符串，也可以是 ToolResultContent 列表。
#    当工具返回多段内容（如文本+截图）时使用列表形式。
# =============================================================================
@dataclass
class ToolResultContent:
    """Content within a tool result - can be text or image."""

    type: Literal["text", "image"]
    text: str | None = None
    # 图片数据源（base64 编码），仅当 type == "image" 时有值
    source: dict[str, Any] | None = None

    def to_api_dict(self) -> dict[str, Any]:
        if self.type == "text":
            return {"type": "text", "text": self.text or ""}
        return {"type": "image", "source": self.source or {}}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> ToolResultContent:
        # P0-4: 对未知类型做优雅降级处理，而不是静默地假设为 "image"。
        # 这是一个防御性设计——API 未来可能新增类型，这里不应崩溃。
        block_type = data.get("type", "text")
        if block_type == "text":
            return cls(type="text", text=data.get("text", ""))
        if block_type == "image":
            return cls(type="image", source=data.get("source"))
        # 未知类型 — 降级为文本表示，避免数据丢失
        return cls(type="text", text=data.get("text", str(data)))


# =============================================================================
# 4. ToolResultBlock — 工具执行结果块（出现在用户消息中，回传给模型）
#    通过 tool_use_id 与对应的 ToolUseBlock 配对。
#    API 要求每个 tool_use 都必须有对应的 tool_result，否则会报错。
# =============================================================================
@dataclass
class ToolResultBlock:
    """A tool result content block (returning results to the model)."""

    # 关联的 ToolUseBlock.id，API 通过此字段匹配请求与结果
    tool_use_id: str
    # 内容可以是简单字符串（常见情况），也可以是包含文本/图片的混合列表
    content: str | list[ToolResultContent]
    # 标记工具执行是否出错，出错时模型会尝试修正或换种方式调用
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"

    def to_api_dict(self) -> dict[str, Any]:
        # content 字段支持两种格式：纯字符串直接传递，列表则逐项序列化
        api_content: str | list[dict[str, Any]] = (
            self.content if isinstance(self.content, str) else [c.to_api_dict() for c in self.content]
        )
        result: dict[str, Any] = {
            "type": self.type,
            "tool_use_id": self.tool_use_id,
            "content": api_content,
        }
        # 只在出错时才加 is_error 字段，减少冗余
        if self.is_error:
            result["is_error"] = True
        return result

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> ToolResultBlock:
        raw_content = data["content"]
        # 根据 content 的实际类型决定解析方式（字符串 vs 列表）
        if isinstance(raw_content, str):
            content: str | list[ToolResultContent] = raw_content
        else:
            content = [ToolResultContent.from_api_dict(c) for c in raw_content]
        return cls(
            tool_use_id=data["tool_use_id"],
            content=content,
            is_error=data.get("is_error", False),
        )


# =============================================================================
# 5. ThinkingBlock — 扩展思考块（Extended Thinking）
#    当启用 extended thinking 功能时，模型在生成最终回复前的思考过程。
#    signature 字段用于验证思考内容的真实性（由 API 签名）。
# =============================================================================
@dataclass
class ThinkingBlock:
    """An extended thinking content block."""

    # 模型的思考文本内容
    thinking: str
    # API 提供的签名，用于验证思考内容未被篡改
    signature: str = ""
    type: Literal["thinking"] = "thinking"

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": self.type, "thinking": self.thinking, "signature": self.signature}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> ThinkingBlock:
        return cls(thinking=data["thinking"], signature=data.get("signature", ""))


# =============================================================================
# 6. RedactedThinkingBlock — 被隐藏的思考块
#    当模型的思考内容因安全策略被 API 隐藏时返回此块。
#    data 字段是不透明的加密数据，客户端无法解读但需原样保留以维持对话连续性。
# =============================================================================
@dataclass
class RedactedThinkingBlock:
    """A redacted thinking block (content hidden by API)."""

    data: str = ""
    type: Literal["redacted_thinking"] = "redacted_thinking"

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> RedactedThinkingBlock:
        return cls(data=data.get("data", ""))


# =============================================================================
# 7. ImageBlock + ImageSource — 图片内容块
#    用于在用户消息中传递截图、图表等视觉信息给多模态模型。
#    目前仅支持 base64 编码方式。
# =============================================================================
@dataclass
class ImageBlock:
    """An image content block."""

    source: ImageSource
    type: Literal["image"] = "image"

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": self.type, "source": self.source.to_api_dict()}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> ImageBlock:
        return cls(source=ImageSource.from_api_dict(data["source"]))


@dataclass
class ImageSource:
    """Image source data."""

    # 目前 API 仅支持 base64 编码，未来可能扩展为 URL 等方式
    type: Literal["base64"] = "base64"
    # MIME 类型，如 "image/png", "image/jpeg"
    media_type: str = "image/png"
    # base64 编码后的图片数据
    data: str = ""

    def to_api_dict(self) -> dict[str, Any]:
        return {"type": self.type, "media_type": self.media_type, "data": self.data}

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> ImageSource:
        return cls(
            type=data.get("type", "base64"),
            media_type=data.get("media_type", "image/png"),
            data=data.get("data", ""),
        )


# =============================================================================
# Union 类型定义 — 用于类型标注，区分不同角色可使用的内容块
# =============================================================================

# 所有内容块类型的联合（6 种）
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | RedactedThinkingBlock | ImageBlock

# 助手消息中允许出现的内容块类型
# 注意：ToolResultBlock 和 ImageBlock 不在此列，它们只能出现在用户消息中
AssistantContentBlock = TextBlock | ToolUseBlock | ThinkingBlock | RedactedThinkingBlock

# 用户消息中允许出现的内容块类型
# 注意：ToolUseBlock 不在此列，它只能由模型生成
UserContentBlock = TextBlock | ToolResultBlock | ImageBlock


def content_block_from_api_dict(data: dict[str, Any]) -> ContentBlock:
    """Deserialize a content block from API dict format.

    根据 dict 中的 "type" 字段分派到对应类的 from_api_dict() 方法。
    这是反序列化的统一入口——调用者无需关心具体类型，由此函数自动路由。
    """
    block_type = data.get("type")
    match block_type:
        case "text":
            return TextBlock.from_api_dict(data)
        case "tool_use":
            return ToolUseBlock.from_api_dict(data)
        case "tool_result":
            return ToolResultBlock.from_api_dict(data)
        case "thinking":
            return ThinkingBlock.from_api_dict(data)
        case "redacted_thinking":
            return RedactedThinkingBlock.from_api_dict(data)
        case "image":
            return ImageBlock.from_api_dict(data)
        case _:
            # 遇到未知类型直接抛异常，因为静默忽略可能导致对话数据丢失
            raise ValueError(f"Unknown content block type: {block_type}")

"""Query loop event types.

These are yielded by the query loop and consumed by the UI layer.
The core loop never directly writes to stdout — all output goes through events.

=== 设计意图 ===

本模块定义了 query_loop 状态机与外部世界（UI 层、调用方）的唯一通信协议。
所有事件均为不可变 dataclass，通过 async generator 的 yield 传递。

这是一个典型的「事件溯源」模式——query_loop 只产出事件，不关心谁消费、如何渲染。
UI 层（ui/renderer.py）通过 match 语句逐个处理这些事件来驱动终端输出。

QueryEvent 使用 Union 类型（而非基类继承），是因为：
  1. 事件种类有限且封闭（不需要外部扩展）
  2. Union 配合 isinstance 检查比 visitor 模式更 Pythonic
  3. mypy 能对 Union 做穷举检查，漏处理某个事件类型会报错

=== 事件生命周期 ===

一次完整的对话轮次（turn）中，事件的产生顺序：
  TextDelta/ThinkingDelta → ToolUseStart → TurnComplete → ToolResultReady → (下一轮)
  特殊情况：CompactOccurred 在 API 调用前触发，ErrorEvent 可在任意阶段触发

=== 模块关系 ===

  产出方: cc/core/query_loop.py（主循环）、cc/api/claude.py（流式解析）
  消费方: cc/ui/renderer.py（渲染）、cc/main.py（token 统计）、cc/core/query_engine.py（透传）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cc.models.content_blocks import AssistantContentBlock
    from cc.models.messages import Usage


# === 流式内容事件（Phase 2: call_model 阶段产出） ===

@dataclass
class TextDelta:
    """Streaming text increment from the model.

    在 claude.py 的 stream_response 中，每收到一个 SSE text_delta 事件就 yield 一次。
    UI 层收到后立即追加显示，实现逐字打印效果。
    """

    text: str


@dataclass
class ThinkingDelta:
    """Streaming thinking text increment.

    与 TextDelta 分开是因为 thinking 内容在 UI 中的渲染方式不同
    （通常用灰色/折叠显示），且 thinking 不会写入最终 assistant message 的 text 部分。
    """

    text: str


# === 工具相关事件（Phase 2 末尾 + Phase 4 产出） ===

@dataclass
class ToolUseStart:
    """A tool call has been initiated.

    在 claude.py 的 content_block_stop 中产出（而非 content_block_start），
    因为需要等 input_json 完整拼接后才能拿到完整的 input dict。
    query_loop 收到后会启动 StreamingToolExecutor 提前执行工具。
    """

    tool_name: str
    tool_id: str
    input: dict[str, object]  # 工具调用的完整参数，已从 JSON 字符串解析为 dict


@dataclass
class ToolResultReady:
    """A tool has finished execution.

    在 query_loop Phase 4（工具执行阶段）中，每个工具执行完毕后 yield。
    content 被截断到 500 字符，仅用于 UI 预览——完整结果写入 transcript。
    """

    tool_id: str
    content: str  # 截断后的文本预览（最多 500 字符）
    is_error: bool = False


# === 系统级事件 ===

@dataclass
class CompactOccurred:
    """Context was compacted (messages summarized).

    在 query_loop Phase 1（准备阶段）中，检测到 token 超阈值时触发自动压缩后 yield。
    也可能在 Phase 3（错误恢复）中，prompt_too_long 触发响应式压缩后 yield。
    """

    summary_preview: str


@dataclass
class TurnComplete:
    """The current turn has finished.

    每轮 API 调用完成后 yield（无论是否有工具调用）。
    stop_reason 决定后续行为：
      - "end_turn": 模型主动结束，query_loop 退出
      - "tool_use": 有工具需要执行，query_loop 继续
      - "max_tokens": 输出截断，可能触发 recovery（escalate max_tokens 或请求继续）
    """

    stop_reason: str  # "end_turn" | "tool_use" | "max_turns" | "aborted" | ...
    usage: Usage  # 本轮的 token 消耗统计
    content: list[AssistantContentBlock] | None = None
    # 完整 assistant content blocks（由 API adapter 在流结束时提供）。
    # query_loop 用它把 thinking/signature/redacted_thinking 原样写回 transcript。
    # 为 None 时，query_loop 会兼容旧 mock call_model，按 TextDelta/ToolUseStart 重建内容。


@dataclass
class ErrorEvent:
    """An error occurred during the query loop.

    is_recoverable 为 True 时，query_loop 会自动重试（429/529 等瞬时错误）。
    为 False 时，query_loop 直接 yield 此事件并 return，由调用方决定如何处理。
    """

    message: str
    is_recoverable: bool = False  # True: 429(限流)/529(过载) 等可重试错误


# QueryEvent 是所有事件的联合类型，query_loop 的 async generator 签名为:
#   async def query_loop(...) -> AsyncIterator[QueryEvent]
# 消费方通过 isinstance 检查来分派处理，mypy 会确保穷举检查。
QueryEvent = TextDelta | ThinkingDelta | ToolUseStart | ToolResultReady | CompactOccurred | TurnComplete | ErrorEvent

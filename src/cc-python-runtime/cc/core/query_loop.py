"""Core conversation query loop.

Corresponds to TS: query.ts — the main while(true) state machine.
Includes error recovery (T5.3) and auto-compact integration (T5.4).

=== 架构角色 ===

query_loop 是整个系统的「心脏」——一个 while(true) 状态机，
驱动 "调用模型 → 解析响应 → 执行工具 → 拼回结果 → 再次调用模型" 的循环。

它是纯函数式的（所有依赖通过参数注入），不持有任何长期状态，
这使得它可以被 QueryEngine、AgentTool、测试 mock 等多种场景复用。

=== 状态机四阶段 ===

每次循环迭代包含四个阶段：

  Phase 1 (准备): normalize_messages_for_api() 转换消息格式 + auto-compact 检查
  Phase 2 (调用): call_model() 流式调用 API，期间通过 StreamingToolExecutor 提前启动工具
  Phase 3 (恢复): 错误处理与恢复（prompt_too_long / max_output_tokens / 429/529 重试）
  Phase 4 (执行): 收集工具执行结果，拼装 ToolResultBlock，追加到 messages

=== 循环的三个 continue 和退出条件 ===

  continue 1: Phase 3 错误恢复成功后 → 重试本轮（turn_count 不增加）
  continue 2: Phase 2 max_tokens 正常截断 → 追加 "请继续" 消息后继续
  continue 3: Phase 4 有工具调用 → 拼回结果后继续下一轮

  退出 1: Phase 3 不可恢复错误 → yield ErrorEvent + return
  退出 2: Phase 4 后无工具调用（stop_reason != tool_use）→ return（正常结束）
  退出 3: turn_count >= max_turns → yield ErrorEvent（超限）

=== transcript 写入点（6处） ===

  1. Phase 1: compact 后 messages.clear() + messages.extend(compacted)
  2. Phase 3: prompt_too_long reactive compact 后同上
  3. Phase 3: max_output_tokens recovery 追加 assistant + "请继续" 消息
  4. Phase 2 后: max_tokens 正常截断追加 assistant + "请继续" 消息
  5. Phase 4 前: 追加 AssistantMessage（模型本轮的输出）
  6. Phase 4: 追加 UserMessage（工具执行结果）

=== 模块关系 ===

  依赖: events.py（事件类型）、token_estimation.py、compact/compact.py、
        streaming_executor.py（P1b 流式工具执行）
  被依赖: query_engine.py（唯一调用方）
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence  # noqa: TC003
from typing import TYPE_CHECKING

from cc.api.token_estimation import estimate_messages_tokens
from cc.compact.compact import should_auto_compact
from cc.core.events import (
    CompactOccurred,
    ErrorEvent,
    QueryEvent,
    TextDelta,
    ThinkingDelta,
    ToolResultReady,
    ToolUseStart,
    TurnComplete,
)
from cc.models.content_blocks import (
    AssistantContentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from cc.models.messages import (
    AssistantMessage,
    Message,
    Usage,
    UserMessage,
    normalize_messages_for_api,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from cc.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

# === 错误恢复常量（对应 TS: query.ts 中的同名常量） ===
MAX_OUTPUT_TOKENS_RECOVERY = 3       # max_tokens 截断后最多重试 3 次（追加 "请继续" 消息）
ESCALATED_MAX_TOKENS = 65536         # 第一次 max_tokens 截断时，将限制从 16K 提升到 64K
DEFAULT_CONTEXT_WINDOW = 200_000     # Claude 3.5 的上下文窗口大小，用于 auto-compact 阈值计算


async def query_loop(
    *,
    messages: list[Message],
    system_prompt: str,
    tools: ToolRegistry,
    call_model: Callable[..., AsyncIterator[QueryEvent]],
    max_turns: int = 100,
    auto_compact_fn: Callable[..., object] | None = None,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    hooks: Sequence[object] | None = None,  # Sequence[HookConfig] at runtime
    permission_checker: Callable[..., object] | None = None,  # P2a wiring
) -> AsyncIterator[QueryEvent]:
    """Execute the core conversation loop.

    Corresponds to TS: query.ts:307-1728 (queryLoop main while(true)).

    Includes:
    - T5.3: Error recovery (prompt_too_long, max_output_tokens, retries)
    - T5.4: Auto-compact integration (token threshold detection)
    - Hooks: passed through to run_tools() for pre/post tool execution

    Bug fixes per check.md:
    - Recoverable errors no longer silently consume turns → tracked separately
    - Tool follow-up checks tool_use_blocks presence, not just stop_reason
    """
    # === 状态机变量初始化 ===
    turn_count = 0                          # 有效轮次计数（成功的 API 调用才 +1）
    retry_count = 0                         # 重试计数器：与 turn_count 分离，重试不消耗轮次预算
    max_retry = 5                           # 重试上限：防止 429/529 无限重试
    max_output_recovery_count = 0           # max_tokens 截断恢复次数（最多 MAX_OUTPUT_TOKENS_RECOVERY 次）
    has_attempted_reactive_compact = False   # 是否已尝试过响应式压缩（只允许一次，防止循环压缩）
    compact_consecutive_failures = 0        # 连续 compact 失败次数（超过阈值后 should_auto_compact 会放弃）
    current_max_tokens = 16384              # 当前 max_tokens，可能被 escalate 到 ESCALATED_MAX_TOKENS
    last_error: ErrorEvent | None = None    # 最后一次错误，用于 max_turns 耗尽时的错误报告

    while turn_count < max_turns:
        turn_count += 1

        # === Phase 1: 消息规范化 + auto-compact 检查 ===
        # normalize_messages_for_api 将内部 Message 对象转为 Anthropic API 所需的 dict 格式
        api_messages = normalize_messages_for_api(messages)
        tool_schemas = tools.get_api_schemas()

        # T5.4: Auto-compact — 在 API 调用前检查 token 量是否接近上下文窗口上限
        # 如果超过阈值（通常是 context_window 的 70%），则调用 compact_messages 压缩历史
        estimated_tokens = estimate_messages_tokens(api_messages)
        if (
            should_auto_compact(estimated_tokens, context_window, compact_consecutive_failures)
            and auto_compact_fn is not None
        ):
            try:
                from cc.compact.compact import compact_messages

                # compact_messages 用低配 call_model（max_tokens=4096）生成摘要替换旧消息
                compacted = await compact_messages(messages, auto_compact_fn)  # type: ignore[arg-type]
                if len(compacted) < len(messages):
                    # transcript 写入点 1: 压缩后替换整个 messages 列表
                    messages.clear()
                    messages.extend(compacted)
                    compact_consecutive_failures = 0
                    yield CompactOccurred(summary_preview="Context auto-compacted")
                    # 压缩后必须重新 normalize，因为 messages 内容已变
                    api_messages = normalize_messages_for_api(messages)
                else:
                    # compact 返回的消息数不少于原来 → 压缩无效，计为失败
                    compact_consecutive_failures += 1
            except Exception as e:
                logger.warning("Auto-compact failed: %s", e)
                compact_consecutive_failures += 1

        # === Phase 2: 调用模型（流式） ===
        # P1b: StreamingToolExecutor 实现「流式提前执行」优化——
        # 当模型还在输出后续 token 时，已完成解析的工具就开始执行，
        # 减少了工具执行的等待时间（尤其是 BashTool 这种耗时工具）。
        from cc.tools.streaming_executor import StreamingToolExecutor

        executor = StreamingToolExecutor(
            tools, hooks=hooks, permission_checker=permission_checker,  # type: ignore[arg-type]
        )
        accumulated_text = ""           # 累积模型输出的文本（用于构建 AssistantMessage）
        usage = Usage()                 # 本轮 token 消耗统计
        stop_reason = "end_turn"        # 默认停止原因，会被 TurnComplete 事件覆盖
        tool_use_blocks: list[ToolUseBlock] = []  # 本轮所有工具调用块
        assistant_content_from_api: list[AssistantContentBlock] | None = None
        error_event: ErrorEvent | None = None     # 本轮的错误（如果有）

        async for event in call_model(
            messages=api_messages,
            system=system_prompt,
            tools=tool_schemas if tool_schemas else None,  # 无工具时传 None 而非空列表（API 要求）
            max_tokens=current_max_tokens,
        ):
            if isinstance(event, (TextDelta, ThinkingDelta)):
                if isinstance(event, TextDelta):
                    accumulated_text += event.text  # 仅累积 text，thinking 不进入最终输出
                yield event  # 立即透传给 UI 层实现逐字显示

            elif isinstance(event, ToolUseStart):
                yield event  # 通知 UI 层显示工具调用信息
                block = ToolUseBlock(id=event.tool_id, name=event.tool_name, input=event.input)
                tool_use_blocks.append(block)
                # P1b: 工具在流式过程中就开始执行，不等整个响应完成
                executor.add_tool(block)

            elif isinstance(event, TurnComplete):
                stop_reason = event.stop_reason
                usage = event.usage
                if event.content is not None:
                    assistant_content_from_api = event.content
                # 注意: 不在这里 yield TurnComplete——Phase 4 完成后才 yield

            elif isinstance(event, ErrorEvent):
                error_event = event  # 暂存错误，Phase 3 统一处理

        # === Phase 3: 错误恢复 ===
        # 设计原则: 可恢复错误不消耗轮次预算（turn_count -= 1），防止重试耗尽用户的 max_turns
        if error_event is not None:
            last_error = error_event
            recovered = False

            # 恢复策略 1: prompt_too_long (HTTP 413) → 响应式压缩
            # 与 Phase 1 的主动压缩不同，这里是 API 已经拒绝了请求后的被动应对
            if "413" in error_event.message or "prompt_too_long" in error_event.message:
                if not has_attempted_reactive_compact and auto_compact_fn is not None:
                    has_attempted_reactive_compact = True  # 只尝试一次，避免压缩-重试-压缩死循环
                    try:
                        from cc.compact.compact import compact_messages

                        compacted = await compact_messages(messages, auto_compact_fn)  # type: ignore[arg-type]
                        if len(compacted) < len(messages):
                            # transcript 写入点 2: 响应式压缩替换 messages
                            messages.clear()
                            messages.extend(compacted)
                            yield CompactOccurred(summary_preview="Reactive compact after prompt_too_long")
                            recovered = True
                    except Exception as e:
                        logger.warning("Reactive compact failed: %s", e)

            # 恢复策略 2: max_output_tokens → 提升限制 或 追加"请继续"消息
            # 分两步走：第一次 escalate max_tokens（16K→64K），后续追加续写请求
            elif "max_output_tokens" in error_event.message or stop_reason == "max_tokens":
                if max_output_recovery_count == 0:
                    # 第一次截断：仅提升 max_tokens 限制，不追加消息
                    current_max_tokens = ESCALATED_MAX_TOKENS
                    max_output_recovery_count += 1
                    recovered = True
                elif max_output_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY:
                    # 后续截断：保存已有输出，追加"请继续"让模型接续
                    max_output_recovery_count += 1
                    if accumulated_text:
                        # transcript 写入点 3: 保存截断的部分输出 + 续写请求
                        messages.append(AssistantMessage(
                            content=[TextBlock(text=accumulated_text)], usage=usage,
                        ))
                        messages.append(UserMessage(content="Please continue from where you left off."))
                    recovered = True

            # 恢复策略 3: 瞬时错误 (429 限流 / 529 过载) → 指数退避重试
            elif error_event.is_recoverable and retry_count < max_retry:
                    retry_count += 1
                    # 退避时间: 2s, 4s, 6s, 8s, 10s（线性增长，上限 10s）
                    await asyncio.sleep(min(2.0 * retry_count, 10.0))
                    recovered = True

            if recovered:
                turn_count -= 1  # 恢复成功 → 回退轮次计数，相当于"这轮不算"
                continue  # continue 1: 重新进入 Phase 1

            # 不可恢复: 直接终止循环，yield 原始错误（而非笼统的 "max turns" 错误）
            yield error_event
            return  # 退出 1: 不可恢复错误

        # API 调用成功 → 重置重试状态
        retry_count = 0
        last_error = None

        # 处理正常的 max_tokens 截断（不是错误，而是 stop_reason == "max_tokens"）
        # 与 Phase 3 的 max_output_tokens 错误不同：这里是 API 正常返回但输出被截断
        if (
            stop_reason == "max_tokens"
            and max_output_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY
            and accumulated_text
        ):
            # transcript 写入点 4: 保存截断的输出 + 续写请求
            messages.append(AssistantMessage(
                content=[TextBlock(text=accumulated_text)], usage=usage,
            ))
            messages.append(UserMessage(content="Please continue from where you left off."))
            max_output_recovery_count += 1
            if max_output_recovery_count == 1:
                current_max_tokens = ESCALATED_MAX_TOKENS  # 首次截断时提升限制
            yield TurnComplete(stop_reason="max_tokens", usage=usage)
            continue  # continue 2: 带着续写请求重新进入 Phase 1

        # 构建 AssistantMessage：将模型本轮输出（文本 + 工具调用）写入 transcript
        # 文本块在前、工具调用块在后，保持与 API 返回顺序一致
        if assistant_content_from_api is not None:
            assistant_blocks = assistant_content_from_api
        else:
            assistant_blocks = []
            if accumulated_text:
                assistant_blocks.append(TextBlock(text=accumulated_text))
            assistant_blocks.extend(tool_use_blocks)

        # transcript 写入点 5: 模型输出写入 messages
        assistant_msg = AssistantMessage(
            content=assistant_blocks,
            usage=usage,
            stop_reason=stop_reason,
        )
        messages.append(assistant_msg)

        yield TurnComplete(stop_reason=stop_reason, usage=usage)

        # === Phase 4: 工具执行 + 结果拼回 ===
        # P1b: 工具已在 Phase 2 流式过程中通过 executor.add_tool() 提前启动，
        # 这里只需等待所有工具完成并收集结果。
        if tool_use_blocks:
            tool_results = await executor.get_results()

            result_blocks: list[ToolResultBlock] = []
            for tool_id, result in tool_results:
                yield ToolResultReady(
                    tool_id=tool_id,
                    content=result.text[:500],  # 截断到 500 字符，仅供 UI 预览
                    is_error=result.is_error,
                )
                # P0-4: 统一内容处理——工具可能返回富内容（如图片）或纯文本
                if isinstance(result.content, list):
                    from cc.models.content_blocks import ToolResultContent

                    try:
                        # 尝试将每个 dict 解析为 ToolResultContent（支持 text/image 等类型）
                        rich = [ToolResultContent.from_api_dict(b) for b in result.content]
                        result_blocks.append(
                            ToolResultBlock(tool_use_id=tool_id, content=rich, is_error=result.is_error)
                        )
                    except (KeyError, TypeError) as e:
                        # 防御性降级：解析失败时退回纯文本，确保工具错误不中断循环
                        logger.warning("Failed to parse rich tool result, falling back to text: %s", e)
                        result_blocks.append(
                            ToolResultBlock(tool_use_id=tool_id, content=result.text, is_error=result.is_error)
                        )
                else:
                    result_blocks.append(
                        ToolResultBlock(tool_use_id=tool_id, content=result.content, is_error=result.is_error)
                    )

            # transcript 写入点 6: 工具结果作为 UserMessage 追加
            # 为什么用 UserMessage？因为 Anthropic API 要求 tool_result 在 user role 中
            tool_result_msg = UserMessage(content=list(result_blocks))
            messages.append(tool_result_msg)
            continue  # continue 3: 有工具调用 → 带着结果继续下一轮

        # 无工具调用 → 模型主动结束对话，退出循环
        return  # 退出 2: 正常结束

    # 退出 3: while 循环自然结束 → turn_count >= max_turns
    # FIX (check.md #1): 如果是因为重试耗尽而到达这里，报告最后一次真实错误而非笼统的 "max turns"
    if last_error is not None:
        yield ErrorEvent(message=f"Gave up after retries. Last error: {last_error.message}", is_recoverable=False)
    else:
        yield ErrorEvent(message=f"Max turns ({max_turns}) reached", is_recoverable=False)

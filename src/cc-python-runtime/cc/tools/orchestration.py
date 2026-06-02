"""Tool orchestration — concurrent/serial dispatch with hooks integration.

Corresponds to TS: services/tools/toolOrchestration.ts + toolExecution.ts.
"""

# 本模块实现了工具调用的「批次编排」策略：
# 当模型在单次响应中返回多个 tool_use 时，这里决定哪些工具可以并行执行、
# 哪些必须串行执行，以及如何在执行前后触发 hooks。
#
# 核心思路：将工具调用序列按"并发安全性"分组成多个批次（batch），
# 同一批次内的工具并行执行，不同批次之间严格顺序执行。
# 这是 run_tools() 的「批处理」模式，适用于 API 响应已完整接收后的场景。
# 对于流式场景（API 响应还在接收中就开始执行工具），使用 streaming_executor.py。

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import ToolRegistry, ToolResult

if TYPE_CHECKING:
    from cc.hooks.hook_runner import HookConfig
    from cc.models.content_blocks import ToolUseBlock

logger = logging.getLogger(__name__)

# 最大并发度限制，防止同时执行过多工具导致系统资源耗尽。
# 例如模型一次返回 20 个 FileRead 调用，信号量会将其限制为最多 10 个同时执行。
MAX_CONCURRENCY = 10


async def run_tools(
    tool_use_blocks: list[ToolUseBlock],
    registry: ToolRegistry,
    hooks: list[HookConfig] | None = None,
) -> list[tuple[str, ToolResult]]:
    """Execute tool calls, respecting concurrency safety and hooks.

    Corresponds to TS: services/tools/toolOrchestration.ts runTools().

    Hooks integration: PreToolUse hooks can block execution, PostToolUse hooks
    are called after each tool completes.
    """
    # 返回值是 (tool_use_id, ToolResult) 的列表，与输入的 tool_use_blocks 一一对应。
    # tool_use_id 用于将结果与 API 请求中的 tool_use block 匹配，
    # 因为 API 要求 tool_result 必须携带对应的 tool_use_id。
    results: list[tuple[str, ToolResult]] = []
    # 先将工具调用序列分组为批次
    batches = _partition_batches(tool_use_blocks, registry)

    for batch in batches:
        if len(batch) == 1:
            # 单个工具无需并发控制，直接执行。
            # 这也是非并发安全工具（如 BashTool）的执行路径。
            tu = batch[0]
            result = await _execute_one(tu, registry, hooks)
            results.append((tu.id, result))
        else:
            # 批次内有多个并发安全的工具，使用信号量限制并发度后并行执行。
            # asyncio.gather 会等待批次内所有工具完成后才继续下一个批次。
            sem = asyncio.Semaphore(MAX_CONCURRENCY)
            batch_results = await asyncio.gather(
                *[_execute_with_sem(sem, b, registry, hooks) for b in batch]
            )
            results.extend(batch_results)

    return results


async def _execute_with_sem(
    sem: asyncio.Semaphore,
    block: ToolUseBlock,
    registry: ToolRegistry,
    hooks: list[HookConfig] | None,
) -> tuple[str, ToolResult]:
    """Execute a tool within a semaphore-bounded context."""
    # 信号量确保即使批次中有大量工具，同时执行的数量也不超过 MAX_CONCURRENCY。
    # 这对于文件系统操作尤为重要——过多的并发 I/O 可能导致文件描述符耗尽。
    async with sem:
        return (block.id, await _execute_one(block, registry, hooks))


def _partition_batches(
    blocks: list[ToolUseBlock],
    registry: ToolRegistry,
) -> list[list[ToolUseBlock]]:
    """Partition tool use blocks into execution batches."""
    # 分组算法核心逻辑：
    # 遍历工具调用列表，将连续的并发安全工具合并为一个批次，
    # 遇到非并发安全工具时，先刷出（flush）当前积累的并发批次，
    # 然后将非并发安全工具作为独立的单元素批次。
    #
    # 例如输入序列: [Read, Read, Edit, Read, Bash]
    # 分组结果: [[Read, Read], [Edit], [Read], [Bash]]
    # 执行顺序: Read+Read 并行 → Edit 独占 → Read 单独 → Bash 独占
    #
    # 这种设计保证了写操作的原子性——Edit 执行时没有其他工具在并行运行，
    # 避免了"读到写了一半的文件"等竞态条件。
    batches: list[list[ToolUseBlock]] = []
    current_concurrent: list[ToolUseBlock] = []

    for block in blocks:
        tool = registry.get(block.name)
        # 工具不存在时视为非并发安全（会在 _execute_one 中返回错误）
        is_safe = tool is not None and tool.is_concurrency_safe(block.input)

        if is_safe:
            # 累积到当前并发批次中
            current_concurrent.append(block)
        else:
            # 遇到非并发安全工具：先刷出已累积的并发批次
            if current_concurrent:
                batches.append(current_concurrent)
                current_concurrent = []
            # 非并发安全工具独立成一个批次，确保独占执行
            batches.append([block])

    # 别忘了刷出最后一批并发工具
    if current_concurrent:
        batches.append(current_concurrent)

    return batches


async def _execute_one(
    block: ToolUseBlock,
    registry: ToolRegistry,
    hooks: list[HookConfig] | None,
) -> ToolResult:
    """Execute a single tool call with pre/post hooks."""
    # 单个工具的完整执行流程：查找 → PreHook → 执行 → PostHook
    tool = registry.get(block.name)
    if tool is None:
        # 工具未注册（可能是 MCP 工具被移除，或模型产生了幻觉工具名）
        return ToolResult(content=f"Error: Unknown tool '{block.name}'", is_error=True)

    # Run PreToolUse hooks
    # PreToolUse hooks 可以拦截工具执行——例如用户可以配置 hook
    # 禁止在特定目录下执行 Bash 命令。被拦截时直接返回错误，不执行工具。
    if hooks:
        # 延迟导入避免循环依赖：hooks 模块可能也依赖 tools 模块的类型
        from cc.hooks.hook_runner import run_pre_tool_hooks

        hook_result = await run_pre_tool_hooks(hooks, block.name, block.input)
        if hook_result.blocked:
            return ToolResult(
                content=f"Blocked by hook: {hook_result.message}",
                is_error=True,
            )

    # Execute tool
    # 关键设计：用 try/except 包裹工具执行，将任何未捕获异常转为 ToolResult。
    # 这保证了单个工具的崩溃不会中断整个 query_loop，
    # 模型可以看到错误信息并决定下一步行动。
    try:
        result = await tool.execute(block.input)
    except Exception as e:
        logger.warning("Tool %s failed: %s", block.name, e)
        result = ToolResult(content=f"Error: {e}", is_error=True)

    # Run PostToolUse hooks
    # PostToolUse hooks 在工具执行完成后触发，用于审计、通知等目的。
    # 传入 result.text 而非整个 result，因为 hooks 只需要文本摘要。
    if hooks:
        from cc.hooks.hook_runner import run_post_tool_hooks

        await run_post_tool_hooks(hooks, block.name, block.input, result.text)

    return result

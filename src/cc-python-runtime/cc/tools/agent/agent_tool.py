"""AgentTool — spawn a sub-agent with its own query loop.

Corresponds to TS: tools/AgentTool/AgentTool.tsx + runAgent.ts.

P0.5a: Uses QueryEngine for runtime assembly instead of manual wiring.
"""

# 本模块实现了"子 agent"工具——模型可以调用此工具来派生一个独立的子 agent，
# 子 agent 拥有自己的 query_loop、工具集和对话历史，可以自主完成复杂任务。
#
# 支持三种运行模式：
# 1. 前台模式（默认）：阻塞父 agent，等待子 agent 完成后返回结果
# 2. 后台模式（run_in_background=True）：立即返回，子 agent 在后台异步执行
# 3. Worktree 隔离模式（isolation="worktree"）：在 git worktree 中执行，
#    子 agent 的文件修改不影响主工作目录
#
# 这三种模式可以组合使用，例如后台 + worktree 隔离。

from __future__ import annotations

import logging
from typing import Any

from cc.core.events import TextDelta, TurnComplete
from cc.models.messages import Message, UserMessage
from cc.prompts.sections import DEFAULT_AGENT_PROMPT
from cc.tools.base import Tool, ToolRegistry, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

# 工具名称常量，API 层面的唯一标识
AGENT_TOOL_NAME = "Agent"


class AgentTool(Tool):
    """Spawn a sub-agent to handle complex tasks.

    Corresponds to TS: tools/AgentTool/AgentTool.tsx.
    P0.5a: Accepts QueryEngine or call_model_factory for sub-agent creation.
    """

    def __init__(
        self,
        parent_registry: ToolRegistry,
        call_model_factory: Any,
        model: str = "claude-sonnet-4-20250514",
        bg_manager: Any | None = None,  # BackgroundAgentManager
        cwd: str | None = None,
    ) -> None:
        # parent_registry: 父 agent 的工具注册表，子 agent 会继承其中的工具
        # （但排除 AgentTool 本身，防止无限递归派生子 agent）
        self._parent_registry = parent_registry
        # call_model_factory: 工厂函数，接受 model 参数返回 call_model 可调用对象，
        # 使得子 agent 可以使用不同的模型（例如用更便宜的模型执行简单子任务）
        self._call_model_factory = call_model_factory
        # 子 agent 默认使用的模型，可通过 tool_input 中的 model 字段覆盖
        self._model = model
        # bg_manager: 后台 agent 管理器，负责跟踪后台任务的生命周期
        # 为 None 时后台模式不可用，会退化为前台模式
        self._bg_manager = bg_manager
        # cwd: 当前工作目录，worktree 隔离模式需要知道 git 仓库位置
        self._cwd = cwd

    def get_name(self) -> str:
        return AGENT_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=AGENT_TOOL_NAME,
            description="Launch a sub-agent to handle complex, multi-step tasks autonomously.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The task for the agent to perform",
                    },
                    "description": {
                        "type": "string",
                        "description": "A short description of what the agent will do",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override for this agent",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "Run agent in background, get notified on completion",
                    },
                    "subagent_type": {
                        "type": "string",
                        "description": "Specialized agent type to use",
                    },
                    "isolation": {
                        "type": "string",
                        "enum": ["worktree"],
                        "description": "Isolation mode: 'worktree' creates a git worktree for the agent",
                    },
                },
                "required": ["prompt"],
            },
        )

    def _build_sub_permission_checker(self, *, is_background: bool) -> Any:
        """Build permission checker for sub-agents.

        W1: All sub-agent execution paths get permission checking.
        Background agents use non-interactive (fail-fast).
        """
        # 子 agent 的权限策略与父 agent 不同：
        # - 前台子 agent：使用 ACCEPT_EDITS 模式，允许文件编辑但可交互询问用户
        # - 后台子 agent：使用 ACCEPT_EDITS 模式但 is_interactive=False，
        #   遇到需要用户确认的操作时直接失败（fail-fast），
        #   因为后台 agent 无法与用户交互。
        from cc.permissions.gate import PermissionContext, PermissionMode

        # Sub-agents use ACCEPT_EDITS mode, non-interactive for background
        ctx = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            is_interactive=not is_background,
        )

        async def _check(tool_name: str, tool_input: dict[str, Any]) -> bool:
            result: bool = await ctx.check(tool_name, tool_input)
            return result

        return _check

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        # 延迟导入 query_loop 以避免模块级循环依赖：
        # agent_tool → query_loop → tools → agent_tool
        from cc.core.query_loop import query_loop

        prompt = tool_input.get("prompt", "")
        agent_model = tool_input.get("model") or self._model
        run_in_bg = tool_input.get("run_in_background", False)
        description = tool_input.get("description", "agent task")
        isolation = tool_input.get("isolation")

        if not prompt:
            return ToolResult(content="Error: prompt is required", is_error=True)

        # P4c: Worktree isolation — create isolated working copy
        # Worktree 隔离：在 git worktree 中创建独立工作副本，
        # 子 agent 的所有文件修改都发生在 worktree 中，不影响主工作目录。
        # 这对于需要并行修改同一仓库的多个后台 agent 尤为重要。
        worktree_path: str | None = None
        if isolation == "worktree":
            from uuid import uuid4

            from cc.tools.agent.worktree import create_agent_worktree

            # 生成唯一的 worktree 标识符，避免多个 agent 的 worktree 冲突
            agent_wt_id = f"agent-{uuid4().hex[:8]}"
            try:
                worktree_path = await create_agent_worktree(
                    self._cwd or ".", agent_wt_id,
                )
            except RuntimeError as e:
                return ToolResult(
                    content=f"Failed to create worktree: {e}",
                    is_error=True,
                )

        # Build child registry — inherit parent tools but exclude AgentTool
        # 构建子 agent 的工具注册表：继承父 agent 的所有工具，但排除以下工具：
        # 1. AgentTool 自身——防止子 agent 再次派生子 agent 形成无限递归
        # 2. 后台模式下排除交互式工具（如 AskUserQuestion），
        #    因为后台 agent 无法与用户交互
        # P4b: Background mode also excludes interactive tools
        child_registry = ToolRegistry()
        interactive_tools = {"AskUserQuestion"} if run_in_bg else set()
        for tool in self._parent_registry.list_tools():
            if tool.get_name() == AGENT_TOOL_NAME:
                continue
            if tool.get_name() in interactive_tools:
                continue
            child_registry.register(tool)

        # 使用预定义的 agent system prompt，为子 agent 设定行为准则
        system_prompt = DEFAULT_AGENT_PROMPT
        # 通过工厂函数创建 call_model，允许子 agent 使用不同的模型
        call_model = self._call_model_factory(model=agent_model)

        # W1: Build permission checker for sub-agents
        # 为子 agent 构建权限检查器，确保子 agent 的操作也受权限控制。
        # Background agents MUST use non-interactive (fail-fast on ASK)
        # Foreground agents inherit parent's interactivity
        sub_perm_checker = self._build_sub_permission_checker(is_background=run_in_bg)

        # P4a: Background mode — spawn and return immediately
        # 后台模式：将子 agent 的执行封装为协程，交给 bg_manager 管理，
        # 立即返回 task_id 给父 agent。子 agent 完成后通过 bg_manager 通知。
        if run_in_bg and self._bg_manager is not None:
            _bg_perm = sub_perm_checker  # bind for closure

            async def _run_agent() -> str:
                # 子 agent 的完整执行：创建初始消息 → 运行 query_loop → 收集输出
                msgs: list[Message] = [UserMessage(content=prompt)]
                parts: list[str] = []
                async for event in query_loop(
                    messages=msgs,
                    system_prompt=system_prompt,
                    tools=child_registry,
                    call_model=call_model,
                    max_turns=30,
                    permission_checker=_bg_perm,
                ):
                    if isinstance(event, TextDelta):
                        parts.append(event.text)
                return "".join(parts) or "(no output)"

            from uuid import uuid4

            agent_id = f"agent-{uuid4().hex[:8]}"
            # spawn 返回 task_id，后续可以通过 bg_manager.poll_completed() 查询结果
            task_id = await self._bg_manager.spawn(agent_id, description, _run_agent())
            return ToolResult(
                content=f"Agent '{description}' launched in background (task_id: {task_id}). "
                "You will be notified when it completes."
            )

        # Foreground mode — block until complete
        # 前台模式：阻塞当前 agent，运行子 agent 的 query_loop 直到完成。
        # max_turns=30 限制子 agent 的最大轮次，防止子 agent 陷入无限循环。
        messages: list[Message] = [UserMessage(content=prompt)]
        output_parts: list[str] = []
        try:
            async for event in query_loop(
                messages=messages,
                system_prompt=system_prompt,
                tools=child_registry,
                call_model=call_model,
                max_turns=30,
                permission_checker=sub_perm_checker,
            ):
                # 收集子 agent 的文本输出
                if isinstance(event, TextDelta):
                    output_parts.append(event.text)
                # end_turn 表示子 agent 认为任务完成，提前终止循环
                elif isinstance(event, TurnComplete) and event.stop_reason == "end_turn":
                    break

        except Exception as e:
            logger.warning("Agent failed: %s", e)
            return ToolResult(content=f"Agent error: {e}", is_error=True)
        finally:
            # P4c: Cleanup worktree after agent completes
            # 无论子 agent 成功还是失败，都要清理 worktree。
            # 使用 finally 确保即使异常也不会留下泄漏的 worktree。
            if worktree_path is not None:
                from cc.tools.agent.worktree import cleanup_agent_worktree

                try:
                    await cleanup_agent_worktree(worktree_path, self._cwd or ".")
                except Exception as e:
                    # 清理失败不应影响结果返回，只记录警告
                    logger.warning("Worktree cleanup failed: %s", e)

        result_text = "".join(output_parts)
        if not result_text.strip():
            return ToolResult(content="(Agent produced no output)")

        return ToolResult(content=result_text)

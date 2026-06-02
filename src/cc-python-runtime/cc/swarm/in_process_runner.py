"""In-process teammate runner — real execution loop.

Runs a teammate in the same process with contextvars isolation.
Uses QueryEngine + query_loop for actual tool execution.

Corresponds to TS: utils/swarm/inProcessRunner.ts.
"""

from __future__ import annotations

import contextvars
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from cc.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

# --- contextvars：用于在同一进程内隔离不同 teammate 的身份信息 ---
# 之所以使用 contextvars 而非实例变量或全局变量，是因为多个 teammate 可能
# 以 asyncio.Task 形式并发运行在同一线程中。contextvars 能保证每个协程任务
# 拥有独立的上下文副本，互不干扰。这等价于 TS 版本中通过 AsyncLocalStorage 实现的隔离。
_teammate_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "teammate_agent_id", default=None
)
_teammate_team_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "teammate_team_name", default=None
)
_teammate_agent_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "teammate_agent_name", default=None
)


def get_current_teammate_id() -> str | None:
    # 供外部模块（如 mailbox、SendMessage 工具）查询当前协程正在运行哪个 teammate
    return _teammate_agent_id.get()


def get_current_team_name() -> str | None:
    # 返回当前协程所属的团队名称，用于消息路由
    return _teammate_team_name.get()


def is_in_process_teammate() -> bool:
    # 判断当前执行上下文是否处于 teammate 内部
    # 用于区分"主 agent / team-lead"与"子 agent / teammate"的行为差异
    return _teammate_agent_id.get() is not None


class InProcessTeammate:
    """Runs a teammate with a real query_loop execution.

    Uses contextvars for identity isolation, QueryEngine-compatible
    call_model + tools, non-interactive permissions, and mailbox
    communication for results.

    整体执行分为 5 步（见 _execute_with_query_loop 方法）：
    1. 构建子工具注册表（过滤危险工具）
    2. 组装 teammate 专属的 system prompt
    3. 配置非交互式权限上下文
    4. 运行 query_loop 主循环收集输出
    5. 通过 mailbox 将结果发送给 team-lead
    """

    def __init__(
        self,
        agent_id: str,
        team_name: str,
        agent_name: str,
        call_model_factory: Any,
        parent_registry: ToolRegistry,
        claude_dir: Path | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.team_name = team_name
        self.agent_name = agent_name
        # call_model_factory 是一个工厂函数，调用后返回可执行的 call_model 可调用对象
        # 这样每个 teammate 可以拥有独立的模型调用实例
        self._call_model_factory = call_model_factory
        # 父级（team-lead）的工具注册表，teammate 会基于此构建子注册表
        self._parent_registry = parent_registry
        self._claude_dir = claude_dir
        # 标记任务是否已完成，供外部查询
        self._completed = False

    async def run(self, initial_task: str) -> str:
        """Run the teammate agent loop with identity isolation."""
        # 进入 teammate 上下文：设置 contextvars 使当前协程具有 teammate 身份
        # set() 返回 token，用于在 finally 中精确重置到进入前的状态
        token_id = _teammate_agent_id.set(self.agent_id)
        token_team = _teammate_team_name.set(self.team_name)
        token_name = _teammate_agent_name.set(self.agent_name)

        try:
            logger.info("Teammate %s starting: %.80s...", self.agent_id, initial_task)
            result = await self._execute_with_query_loop(initial_task)
            self._completed = True
            logger.info("Teammate %s completed.", self.agent_id)
            return result
        except Exception:
            logger.exception("Teammate %s failed.", self.agent_id)
            raise
        finally:
            # 无论成功或失败，都必须重置 contextvars
            # 使用 token 重置（而非直接 set(None)）保证嵌套场景下的正确性
            _teammate_agent_id.reset(token_id)
            _teammate_team_name.reset(token_team)
            _teammate_agent_name.reset(token_name)

    async def _execute_with_query_loop(self, task: str) -> str:
        """Execute using real query_loop with tools and permissions.

        1. Build child tool registry (exclude AgentTool to prevent recursion)
        2. Build teammate system prompt with addendum
        3. Use non-interactive PermissionContext
        4. Run query_loop and collect text output
        5. Send result to leader via mailbox
        """
        from cc.core.events import TextDelta, TurnComplete
        from cc.core.query_loop import query_loop
        from cc.models.messages import Message, UserMessage
        from cc.permissions.gate import PermissionContext, PermissionMode
        from cc.prompts.sections import DEFAULT_AGENT_PROMPT
        from cc.prompts.teammate_prompt import build_teammate_prompt_addendum
        from cc.swarm.identity import TEAM_LEAD_NAME
        from cc.swarm.mailbox import TeammateMailbox, TeammateMessage
        from cc.tools.base import ToolRegistry

        # --- 第 1 步：构建子工具注册表 ---
        # 关键过滤规则：排除以下工具以防止递归和越权
        #   - Agent：防止 teammate 再次 spawn 子 teammate，导致无限递归
        #   - AskUserQuestion：teammate 无法与终端用户交互
        #   - TeamCreate/TeamDelete：teammate 不应有权管理团队生命周期
        # SendMessage 需要特殊处理：注入当前 teammate 的身份信息（team_name, sender_name）
        child_registry = ToolRegistry()
        for tool in self._parent_registry.list_tools():
            if tool.get_name() in ("Agent", "AskUserQuestion", "TeamCreate", "TeamDelete"):
                continue
            # 为 SendMessage 工具注入 teammate 身份，使其发送消息时自动携带正确的发件人
            if tool.get_name() == "SendMessage":
                from cc.tools.send_message.send_message_tool import SendMessageTool

                child_registry.register(SendMessageTool(
                    team_name=self.team_name,
                    sender_name=self.agent_name,
                ))
                continue
            child_registry.register(tool)

        # --- 第 2 步：组装 system prompt ---
        # 使用极简版的 DEFAULT_AGENT_PROMPT 作为基础（而非完整的主 agent prompt）
        # 再追加 teammate 专属的补充说明（如团队名称、角色、汇报对象等）
        teammate_addendum = build_teammate_prompt_addendum(self.team_name, self.agent_name)
        system_prompt = f"{DEFAULT_AGENT_PROMPT}\n\n{teammate_addendum}"

        # --- 第 3 步：配置非交互式权限 ---
        # teammate 运行在后台，无法弹出交互式确认对话框
        # 因此使用 ACCEPT_EDITS 模式 + is_interactive=False，自动授权所有合理操作
        perm_ctx = PermissionContext(mode=PermissionMode.ACCEPT_EDITS, is_interactive=False)

        async def _perm_check(tool_name: str, tool_input: dict[str, object]) -> bool:
            result: bool = await perm_ctx.check(tool_name, tool_input)
            return result

        # --- 第 4 步：运行 query_loop 主循环 ---
        # 创建独立的 call_model 实例，model=None 表示使用默认模型
        call_model = self._call_model_factory(model=None)
        # 将任务文本包装为 UserMessage 作为对话的第一条消息
        messages: list[Message] = [UserMessage(content=task)]
        output_parts: list[str] = []

        try:
            async for event in query_loop(
                messages=messages,
                system_prompt=system_prompt,
                tools=child_registry,
                call_model=call_model,
                max_turns=30,
                permission_checker=_perm_check,
            ):
                # 只收集文本增量事件作为最终输出
                if isinstance(event, TextDelta):
                    output_parts.append(event.text)
                # end_turn 表示模型主动结束对话（而非被工具调用中断）
                elif isinstance(event, TurnComplete) and event.stop_reason == "end_turn":
                    break
        except Exception as e:
            logger.warning("Teammate %s query_loop failed: %s", self.agent_id, e)
            # 即使执行失败也记录错误信息，而非直接抛出，确保后续 mailbox 发送能执行
            output_parts.append(f"(Error: {e})")

        result_text = "".join(output_parts) or "(no output)"

        # --- 第 5 步：通过 mailbox 将结果发回给 team-lead ---
        # team-lead 会在其主循环的轮次间隙轮询自己的 inbox，获取各 teammate 的完成通知
        try:
            mailbox = TeammateMailbox(self.team_name, claude_dir=self._claude_dir)
            mailbox.send(
                TEAM_LEAD_NAME,
                TeammateMessage(
                    from_name=self.agent_name,
                    text=result_text,
                    timestamp=time.time(),
                    summary=f"{self.agent_name} completed",
                ),
            )
        except Exception as e:
            logger.warning("Teammate %s mailbox send failed: %s", self.agent_id, e)

        return result_text

    async def check_inbox(self) -> list[str]:
        """Check for incoming messages and return their texts.

        Called between turns to inject received messages into the
        teammate's conversation context.
        """
        from cc.swarm.mailbox import TeammateMailbox

        try:
            mailbox = TeammateMailbox(self.team_name, claude_dir=self._claude_dir)
            # 读取所有未读消息
            messages = mailbox.receive(self.agent_name)
            if messages:
                # 标记已读，防止下次轮询时重复处理
                mailbox.mark_all_read(self.agent_name)
                # 返回格式化后的消息文本，包含发件人信息便于 agent 理解来源
                return [f"[From {m.from_name}]: {m.text}" for m in messages]
        except Exception as e:
            logger.debug("Inbox check failed: %s", e)
        return []

    @property
    def is_completed(self) -> bool:
        return self._completed

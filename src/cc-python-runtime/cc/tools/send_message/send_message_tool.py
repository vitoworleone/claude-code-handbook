"""SendMessageTool — send messages to teammates in a swarm.

Corresponds to TS: tools/SendMessageTool/SendMessageTool.ts.

该工具是 swarm（多智能体协作）架构中的核心通信组件。
通过邮箱机制实现异步消息传递，支持点对点和广播两种模式。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from cc.swarm.identity import TEAM_LEAD_NAME
from cc.swarm.mailbox import TeammateMailbox, TeammateMessage
from cc.swarm.team_file import load_team_file
from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)


class SendMessageTool(Tool):
    """Send a message to a teammate or broadcast to all teammates.

    Corresponds to TS: tools/SendMessageTool/SendMessageTool.ts.

    Supports:
    - Point-to-point: to="agent_name"  — 精确发送给一个队友
    - Broadcast: to="*"  — 广播给团队中除自己之外的所有成员
    """

    def __init__(
        self,
        *,
        team_name: str = "",
        sender_name: str = TEAM_LEAD_NAME,
        team_context: Any | None = None,  # TeamContext — dynamic team state
    ) -> None:
        # team_name 可以显式指定，也可以在运行时从 team_context 动态获取
        self._team_name = team_name
        # sender_name 标识消息发送者，默认为 team_lead（主协调智能体）
        self._sender_name = sender_name
        # team_context 是动态团队状态，由 _build_engine 注入
        self._team_context = team_context

    def get_name(self) -> str:
        return "SendMessage"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="SendMessage",
            description="Send a message to a teammate or broadcast to all teammates.",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": (
                            'Recipient: teammate name, or "*" for broadcast '
                            "to all teammates."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Plain text message content.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "A 5-10 word summary shown as preview in the UI.",
                    },
                },
                "required": ["to", "text"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        """Send or broadcast a message."""
        to = tool_input.get("to", "")
        text = tool_input.get("text", "")
        summary = tool_input.get("summary")

        if not to or not to.strip():
            return ToolResult(
                content="Error: 'to' must not be empty.",
                is_error=True,
            )

        if not text:
            return ToolResult(
                content="Error: 'text' must not be empty.",
                is_error=True,
            )

        # 团队名解析优先级：构造时显式指定 > 运行时 team_context > 报错
        # 这种多来源设计是为了支持不同的初始化场景
        team_name = self._team_name
        if not team_name and self._team_context is not None:
            team_name = self._team_context.team_name or ""
        if not team_name:
            return ToolResult(
                content=(
                    "Error: Not in a team context. "
                    "Create a team with TeamCreate first."
                ),
                is_error=True,
            )

        # 每次调用创建新的 Mailbox 实例——邮箱基于文件系统，无状态
        mailbox = TeammateMailbox(team_name)
        msg = TeammateMessage(
            from_name=self._sender_name,
            text=text,
            timestamp=time.time(),
            summary=summary,
        )

        # "*" 为广播标识符，发送给团队中除自己外的所有成员
        if to == "*":
            return await self._broadcast(mailbox, msg)
        # 否则点对点发送给指定的队友
        return await self._send_to(mailbox, to, msg)

    async def _send_to(
        self,
        mailbox: TeammateMailbox,
        recipient: str,
        msg: TeammateMessage,
    ) -> ToolResult:
        """Send a point-to-point message.
        点对点消息直接写入目标队友的收件箱文件。
        """
        mailbox.send(recipient, msg)
        result = {
            "success": True,
            "message": f"Message sent to {recipient}'s inbox.",
        }
        return ToolResult(content=json.dumps(result))

    async def _broadcast(
        self,
        mailbox: TeammateMailbox,
        msg: TeammateMessage,
    ) -> ToolResult:
        """Broadcast a message to all teammates except the sender.
        广播时需要加载团队文件以获取成员列表，
        排除发送者自己以避免自己收到自己的消息。
        """
        team = load_team_file(self._team_name)
        if team is None:
            return ToolResult(
                content=f'Error: Team "{self._team_name}" does not exist.',
                is_error=True,
            )

        # 收集除自己之外的所有队友名称
        recipients: list[str] = []
        for member in team.members:
            # 使用 lower() 进行大小写不敏感比较，避免命名不一致导致的问题
            if member.name.lower() != self._sender_name.lower():
                recipients.append(member.name)

        if not recipients:
            # 团队中只有自己时的边界情况处理
            result = {
                "success": True,
                "message": "No teammates to broadcast to (you are the only team member).",
                "recipients": [],
            }
            return ToolResult(content=json.dumps(result))

        # 逐一发送给每个队友——每个队友有独立的收件箱文件
        for name in recipients:
            mailbox.send(name, msg)

        result = {
            "success": True,
            "message": f"Message broadcast to {len(recipients)} teammate(s): {', '.join(recipients)}.",
            "recipients": recipients,
        }
        return ToolResult(content=json.dumps(result))

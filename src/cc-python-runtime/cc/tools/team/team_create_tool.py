"""TeamCreateTool — create a new agent team.

Corresponds to TS: tools/TeamCreateTool/TeamCreateTool.ts.

该工具是 swarm 多智能体协作的入口点。
创建团队后，系统会初始化团队文件、设置 team_lead（主协调者），
并激活 TeamContext，使得 SendMessage 和队友生成等功能可用。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from cc.swarm.identity import TEAM_LEAD_NAME, format_agent_id
from cc.swarm.team_file import TeamFile, TeamMember, load_team_file, save_team_file
from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)


class TeamCreateTool(Tool):
    """Create a new team for coordinating multiple agents.

    Sets the session's TeamContext so SendMessage and teammate spawning become available.
    创建团队时会自动将当前智能体注册为 team_lead（主协调者），
    team_lead 负责分配任务、协调其他智能体、汇总结果。
    """

    def __init__(self, team_context: Any | None = None) -> None:
        # team_context 由 _build_engine 注入，用于在会话级别管理团队状态
        self._team_context = team_context  # TeamContext, set by _build_engine

    def get_name(self) -> str:
        return "TeamCreate"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TeamCreate",
            description="Create a new team for coordinating multiple agents.",
            input_schema={
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Name for the new team to create.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Team description/purpose.",
                    },
                },
                "required": ["team_name"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        """Create the team file and return team info."""
        team_name = tool_input.get("team_name", "")
        description = tool_input.get("description")

        if not team_name or not team_name.strip():
            return ToolResult(
                content="Error: team_name is required for TeamCreate.",
                is_error=True,
            )

        # 防止重复创建——团队名称在系统中必须唯一
        existing = load_team_file(team_name)
        if existing is not None:
            return ToolResult(
                content=f"Error: Team '{team_name}' already exists.",
                is_error=True,
            )

        # 为 team_lead 生成唯一标识符，格式为 "{agent_name}@{team_name}"
        lead_agent_id = format_agent_id(TEAM_LEAD_NAME, team_name)
        now = time.time()

        # 构造团队文件数据结构，初始成员只有 team_lead 自己
        team = TeamFile(
            name=team_name,
            description=description,
            created_at=now,
            lead_agent_id=lead_agent_id,
            members=[
                TeamMember(
                    agent_id=lead_agent_id,
                    name=TEAM_LEAD_NAME,
                    agent_type=TEAM_LEAD_NAME,
                    joined_at=now,
                    # 记录 team_lead 的工作目录，供后续队友参考
                    cwd=str(Path.cwd()),
                )
            ],
        )

        # 将团队信息持久化到磁盘（JSON 文件），便于跨进程共享
        team_file_path = save_team_file(team)

        # 激活会话级别的团队上下文，使 SendMessage 等工具能感知团队
        if self._team_context is not None:
            self._team_context.enter_team(team_name)

        result = {
            "team_name": team_name,
            "team_file_path": str(team_file_path),
            "lead_agent_id": lead_agent_id,
        }
        logger.info("Created team: %s", team_name)
        return ToolResult(content=json.dumps(result))

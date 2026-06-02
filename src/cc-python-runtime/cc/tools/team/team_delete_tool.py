"""TeamDeleteTool — delete an agent team and clean up.

Corresponds to TS: tools/TeamDeleteTool/TeamDeleteTool.ts.

该工具是团队生命周期的终点。在所有队友完成任务并关闭后，
team_lead 调用此工具清理团队目录和状态文件。
为防止数据丢失，工具会拒绝清理仍有活跃成员的团队。
"""

from __future__ import annotations

import json
import logging
import shutil
from typing import Any

from cc.swarm.team_file import _get_team_dir, load_team_file
from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)


class TeamDeleteTool(Tool):
    """Delete a team and clean up its directories.

    Corresponds to TS: tools/TeamDeleteTool/TeamDeleteTool.ts.
    """

    def get_name(self) -> str:
        return "TeamDelete"

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name="TeamDelete",
            description="Clean up team and task directories when the swarm is complete.",
            input_schema={
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Name of the team to delete.",
                    },
                },
                "required": ["team_name"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        """Delete the team file and directory."""
        team_name = tool_input.get("team_name", "")

        if not team_name or not team_name.strip():
            return ToolResult(
                content="Error: team_name is required for TeamDelete.",
                is_error=True,
            )

        # 检查团队是否存在——防止对不存在的团队执行删除
        team = load_team_file(team_name)
        if team is None:
            result = {
                "success": False,
                "message": f'Team "{team_name}" does not exist.',
            }
            return ToolResult(content=json.dumps(result), is_error=True)

        # 安全检查：确保没有活跃的非 lead 成员，
        # 强制在清理前先通过 requestShutdown 终止所有队友
        from cc.swarm.identity import TEAM_LEAD_NAME

        non_lead = [m for m in team.members if m.name != TEAM_LEAD_NAME]
        active = [m for m in non_lead if m.is_active]
        if active:
            # 拒绝删除仍有活跃成员的团队，避免丢失进行中的工作
            names = ", ".join(m.name for m in active)
            result = {
                "success": False,
                "message": (
                    f"Cannot cleanup team with {len(active)} active member(s): {names}. "
                    "Use requestShutdown to gracefully terminate teammates first."
                ),
                "team_name": team_name,
            }
            return ToolResult(content=json.dumps(result), is_error=True)

        # 递归删除整个团队目录（包含团队文件、邮箱文件等）
        team_dir = _get_team_dir(team_name)
        try:
            if team_dir.exists():
                shutil.rmtree(team_dir)
                logger.info("Deleted team directory: %s", team_dir)
        except OSError as e:
            # 目录删除失败不是致命错误（可能是权限问题），记录警告继续执行
            logger.warning("Failed to clean up team directory %s: %s", team_dir, e)

        # 清除会话级团队上下文，使 SendMessage 等工具恢复到非团队状态
        if hasattr(self, '_team_context') and self._team_context is not None:
            self._team_context.leave_team()

        result = {
            "success": True,
            "message": f'Cleaned up directories for team "{team_name}".',
            "team_name": team_name,
        }
        logger.info("Deleted team: %s", team_name)
        return ToolResult(content=json.dumps(result))

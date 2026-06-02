"""Team context — tracks whether the current session is in a team.

When a team is created via TeamCreate, the leader session enters a team context.
This enables SendMessage and teammate spawning.

TeamContext 是会话级的可变状态容器，用于记录"当前会话是否处于团队模式"。
它不持有任何 teammate 实例或消息，仅作为一个轻量级的状态开关：
- is_active=True 时，SendMessage 工具和 AgentTool（spawn）才会被启用
- is_active=False 时，这些团队相关的功能被禁用
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TeamContext:
    """Mutable state: whether the current session is operating as a team leader.

    Set after TeamCreate succeeds. Consumed by SendMessage, AgentTool (spawn),
    and inbox polling.
    """

    def __init__(self) -> None:
        # 当前所属团队名称；None 表示未加入任何团队
        self._team_name: str | None = None
        # team-lead 的固定名称，与 identity.py 中的 TEAM_LEAD_NAME 保持一致
        self._leader_name: str = "team-lead"

    @property
    def team_name(self) -> str | None:
        return self._team_name

    @property
    def is_active(self) -> bool:
        # 通过判断 team_name 是否为 None 来确定团队上下文是否激活
        # 这比使用独立的 bool 标志更可靠，因为只需要维护一个状态源
        return self._team_name is not None

    @property
    def leader_name(self) -> str:
        return self._leader_name

    def enter_team(self, team_name: str) -> None:
        """Enter a team context (called after TeamCreate)."""
        # TeamCreate 工具执行成功后调用此方法，激活团队模式
        self._team_name = team_name
        logger.info("Entered team context: %s", team_name)

    def leave_team(self) -> None:
        """Leave the team context (called after TeamDelete)."""
        # TeamDelete 工具执行后调用此方法，退出团队模式
        # 退出后 SendMessage 和 spawn 功能将被禁用
        prev = self._team_name
        self._team_name = None
        logger.info("Left team context: %s", prev)

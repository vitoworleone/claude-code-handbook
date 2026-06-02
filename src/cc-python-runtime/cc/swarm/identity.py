"""Teammate identity utilities for agent swarm coordination.

Corresponds to TS: utils/teammate.ts + utils/agentId.ts (formatAgentId, parseAgentId).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 团队领导的固定名称，与 TS 端保持一致
# 所有 teammate 完成任务后都会通过 mailbox 将结果发送给这个名称对应的 agent
# Corresponds to TS: utils/swarm/constants.ts TEAM_LEAD_NAME
TEAM_LEAD_NAME = "team-lead"


@dataclass
class AgentRoute:
    """Parsed agent identity from 'agentName@teamName' format.

    Corresponds to TS: utils/agentId.ts parseAgentId return type.
    """

    # agent 在团队内的唯一名称（如 "researcher"、"coder"）
    agent_name: str
    # 所属团队的名称，用于隔离不同团队的消息和状态
    team_name: str


def parse_agent_id(agent_id: str) -> AgentRoute:
    """Parse 'agentName@teamName' format into an AgentRoute.

    Corresponds to TS: utils/agentId.ts parseAgentId().

    Raises:
        ValueError: If agent_id does not contain exactly one '@'.
    """
    # agent_id 使用 "name@team" 格式（类似 email 地址格式），便于在扁平字符串中同时编码身份和归属
    if "@" not in agent_id:
        raise ValueError(
            f"Invalid agent_id format: '{agent_id}'. Expected 'agentName@teamName'."
        )
    # 使用 maxsplit=1 确保只在第一个 '@' 处分割，允许 team_name 中包含 '@' 字符
    parts = agent_id.split("@", 1)
    if not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid agent_id format: '{agent_id}'. Both agentName and teamName must be non-empty."
        )
    return AgentRoute(agent_name=parts[0], team_name=parts[1])


def format_agent_id(name: str, team: str) -> str:
    """Format as 'name@team'.

    Corresponds to TS: utils/agentId.ts formatAgentId().
    """
    # parse_agent_id 的逆操作，将分散的 name 和 team 拼接为标准 agent_id 字符串
    return f"{name}@{team}"


def sanitize_name(name: str) -> str:
    """Sanitize a name for use in file paths.

    Replaces all non-alphanumeric characters with hyphens and lowercases.

    Corresponds to TS: utils/swarm/teamHelpers.ts sanitizeName().
    """
    # 将非字母数字字符统一替换为连字符并转小写
    # 这样做是为了确保名称可以安全地用于文件系统路径（避免空格、特殊字符等问题）
    return re.sub(r"[^a-zA-Z0-9]", "-", name).lower()

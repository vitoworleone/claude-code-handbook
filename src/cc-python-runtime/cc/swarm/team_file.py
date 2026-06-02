"""Team configuration file management.

Manages team config files at ~/.claude/teams/{name}/config.json.

团队配置文件是整个 swarm 协作的"注册中心"：
- TeamCreate 创建团队时会生成 config.json
- spawn_teammate 会往 config.json 的 members 列表中追加成员
- team-lead 可以通过读取 config.json 了解当前团队的所有成员

Corresponds to TS: utils/swarm/teamHelpers.ts (TeamFile, readTeamFile,
writeTeamFile, removeTeammateFromTeamFile, etc.).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from cc.swarm.identity import sanitize_name

logger = logging.getLogger(__name__)

# 默认的 claude 配置根目录
_DEFAULT_CLAUDE_DIR = Path.home() / ".claude"


@dataclass
class TeamMember:
    """A member in a team.

    Corresponds to TS: utils/swarm/teamHelpers.ts TeamFile.members[].
    """

    # 格式为 "name@team" 的唯一标识符
    agent_id: str
    # agent 的可读名称（如 "researcher"）
    name: str
    # agent 类型标识，当前预留未使用
    agent_type: str | None = None
    # 使用的模型名称，当前预留未使用
    model: str | None = None
    # Unix 时间戳，记录加入团队的时间
    joined_at: float = 0
    # agent 的工作目录
    cwd: str = ""
    # 是否仍在活动中（完成任务后可置为 False）
    is_active: bool = True

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict."""
        # 使用驼峰命名（camelCase）作为 JSON key，与 TS 版本的数据格式兼容
        # 这样 Python 版和 TS 版可以读取彼此生成的 team file
        d: dict[str, object] = {
            "agentId": self.agent_id,
            "name": self.name,
            "joinedAt": self.joined_at,
            "cwd": self.cwd,
            "isActive": self.is_active,
        }
        # 可选字段仅在有值时写入，减少 JSON 体积
        if self.agent_type is not None:
            d["agentType"] = self.agent_type
        if self.model is not None:
            d["model"] = self.model
        return d

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TeamMember:
        """Deserialize from dict."""
        return cls(
            agent_id=str(data.get("agentId", "")),
            name=str(data.get("name", "")),
            agent_type=str(data["agentType"]) if data.get("agentType") is not None else None,
            model=str(data["model"]) if data.get("model") is not None else None,
            joined_at=float(data["joinedAt"]) if "joinedAt" in data else 0.0,  # type: ignore[arg-type]
            cwd=str(data.get("cwd", "")),
            is_active=bool(data.get("isActive", True)),
        )


@dataclass
class TeamFile:
    """Team configuration file.

    对应磁盘上 ~/.claude/teams/{name}/config.json 的内存表示。
    包含团队元信息和所有成员列表。

    Corresponds to TS: utils/swarm/teamHelpers.ts TeamFile type.
    """

    # 团队名称
    name: str
    # 团队描述，可选
    description: str | None = None
    # 团队创建时间（Unix 时间戳）
    created_at: float = 0
    # 团队领导的 agent_id（格式："team-lead@{team_name}"）
    lead_agent_id: str = ""
    # 团队成员列表，使用 field(default_factory=list) 避免可变默认值陷阱
    members: list[TeamMember] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict for JSON storage."""
        d: dict[str, object] = {
            "name": self.name,
            "createdAt": self.created_at,
            "leadAgentId": self.lead_agent_id,
            "members": [m.to_dict() for m in self.members],
        }
        if self.description is not None:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TeamFile:
        """Deserialize from dict."""
        members_data = data.get("members", [])
        members = []
        # 防御性检查：确保 members_data 是列表且每个元素是字典
        # 应对 JSON 文件被手动编辑或损坏的情况
        if isinstance(members_data, list):
            for m in members_data:
                if isinstance(m, dict):
                    members.append(TeamMember.from_dict(m))
        return cls(
            name=str(data.get("name", "")),
            description=str(data["description"]) if data.get("description") is not None else None,
            created_at=float(data["createdAt"]) if "createdAt" in data else 0.0,  # type: ignore[arg-type]
            lead_agent_id=str(data.get("leadAgentId", "")),
            members=members,
        )


def _get_team_dir(team_name: str, claude_dir: Path | None = None) -> Path:
    """Get the path to a team's directory."""
    base = claude_dir or _DEFAULT_CLAUDE_DIR
    # 对团队名称进行 sanitize 后用于目录名，确保路径安全
    return base / "teams" / sanitize_name(team_name)


def _get_team_file_path(team_name: str, claude_dir: Path | None = None) -> Path:
    """Get the path to a team's config.json file.

    Corresponds to TS: utils/swarm/teamHelpers.ts getTeamFilePath().
    """
    return _get_team_dir(team_name, claude_dir) / "config.json"


def load_team_file(team_name: str, claude_dir: Path | None = None) -> TeamFile | None:
    """Read a team file by name.

    Corresponds to TS: utils/swarm/teamHelpers.ts readTeamFile().

    Returns:
        TeamFile if found, None if not found or unreadable.
    """
    path = _get_team_file_path(team_name, claude_dir)
    # 文件不存在返回 None（而非抛异常），让调用方自行决定如何处理"团队不存在"的情况
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TeamFile.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read team file for %s: %s", team_name, e)
        return None


def save_team_file(team: TeamFile, claude_dir: Path | None = None) -> Path:
    """Write a team file.

    Corresponds to TS: utils/swarm/teamHelpers.ts writeTeamFileAsync().

    Returns:
        Path to the written config.json.
    """
    team_dir = _get_team_dir(team.name, claude_dir)
    # 确保团队目录存在（首次创建团队时需要）
    team_dir.mkdir(parents=True, exist_ok=True)
    path = _get_team_file_path(team.name, claude_dir)
    # indent=2 格式化输出，便于人工检查和调试
    path.write_text(json.dumps(team.to_dict(), indent=2), encoding="utf-8")
    logger.debug("Saved team file: %s", path)
    return path


def add_member(team_name: str, member: TeamMember, claude_dir: Path | None = None) -> None:
    """Add a member to a team.

    Loads the team file, appends the member, and saves.

    Raises:
        ValueError: If the team does not exist.
    """
    team = load_team_file(team_name, claude_dir)
    if team is None:
        # 团队必须先通过 TeamCreate 创建 config.json，才能添加成员
        raise ValueError(f"Team '{team_name}' does not exist.")
    # 检查是否已存在相同 agent_id 的成员，避免重复注册
    # 这在 teammate 因异常重启后重新 spawn 时可能发生
    existing_ids = {m.agent_id for m in team.members}
    if member.agent_id in existing_ids:
        logger.warning("Member %s already in team %s", member.agent_id, team_name)
        return
    team.members.append(member)
    save_team_file(team, claude_dir)
    logger.debug("Added member %s to team %s", member.agent_id, team_name)


def remove_member(team_name: str, agent_name: str, claude_dir: Path | None = None) -> None:
    """Remove a member from a team by agent name.

    Corresponds to TS: utils/swarm/teamHelpers.ts removeTeammateFromTeamFile().

    Raises:
        ValueError: If the team does not exist.
    """
    team = load_team_file(team_name, claude_dir)
    if team is None:
        raise ValueError(f"Team '{team_name}' does not exist.")
    original_count = len(team.members)
    # 使用列表推导过滤掉指定名称的成员
    # 按 name 而非 agent_id 匹配，因为调用方通常只知道 agent 的可读名称
    team.members = [m for m in team.members if m.name != agent_name]
    if len(team.members) < original_count:
        # 只在确实删除了成员时才写回文件
        save_team_file(team, claude_dir)
        logger.debug("Removed member %s from team %s", agent_name, team_name)
    else:
        logger.warning("Member %s not found in team %s", agent_name, team_name)

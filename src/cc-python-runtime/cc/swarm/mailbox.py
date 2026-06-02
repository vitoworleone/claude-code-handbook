"""Teammate Mailbox - File-based messaging system for agent swarms.

Each teammate has an inbox file at ~/.claude/teams/{team_name}/inboxes/{agent_name}.json.
Other teammates can write messages to it, and the recipient sees them as attachments.

之所以采用文件系统而非内存队列，是因为：
1. agent 可能运行在不同进程中（跨进程通信需要持久化介质）
2. 消息需要在 agent 重启后仍然可读（持久化）
3. 实现简单可靠，不依赖外部消息中间件

Corresponds to TS: utils/teammateMailbox.ts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from cc.swarm.identity import sanitize_name

logger = logging.getLogger(__name__)

# 默认的 claude 配置目录，所有团队数据存储在其下的 teams/ 子目录中
_DEFAULT_CLAUDE_DIR = Path.home() / ".claude"


@dataclass
class TeammateMessage:
    """A message in a teammate's inbox.

    Corresponds to TS: utils/teammateMailbox.ts TeammateMessage type.
    """

    # 发送者的 agent 名称
    from_name: str
    # 消息正文
    text: str
    # Unix 时间戳，用于消息排序和过期检测
    timestamp: float
    # 是否已读标记，用于 receive() 方法过滤未读消息
    read: bool = False
    # 可选的摘要信息，供 team-lead 快速预览而无需读取完整 text
    summary: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict for JSON storage."""
        # 使用 "from" 作为 JSON key（而非 "from_name"），与 TS 版本的数据格式保持一致
        d: dict[str, object] = {
            "from": self.from_name,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
        }
        # summary 为可选字段，仅在有值时写入 JSON，减少文件体积
        if self.summary is not None:
            d["summary"] = self.summary
        return d

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TeammateMessage:
        """Deserialize from dict."""
        return cls(
            from_name=str(data.get("from", "")),
            text=str(data.get("text", "")),
            timestamp=float(data["timestamp"]) if "timestamp" in data else 0.0,  # type: ignore[arg-type]
            read=bool(data.get("read", False)),
            summary=str(data["summary"]) if data.get("summary") is not None else None,
        )


class TeammateMailbox:
    """File-backed message store at ~/.claude/teams/{team}/inboxes/{agent}.json.

    每个 agent 拥有一个独立的 JSON 文件作为收件箱。
    发送消息 = 读取收件人的 JSON 文件 → 追加消息 → 写回文件。
    这是一种简单的"追加式"队列实现。

    注意：当前实现没有文件锁，在高并发写入同一收件箱时可能丢失消息。
    对于 agent swarm 场景（低频消息），这种简化是可接受的。

    Corresponds to TS: utils/teammateMailbox.ts (readMailbox, writeToMailbox, markAllAsRead).
    """

    def __init__(self, team_name: str, claude_dir: Path | None = None) -> None:
        self._team_name = team_name
        self._claude_dir = claude_dir or _DEFAULT_CLAUDE_DIR
        # 收件箱目录路径：~/.claude/teams/{sanitized_team_name}/inboxes/
        self._inbox_dir = (
            self._claude_dir / "teams" / sanitize_name(team_name) / "inboxes"
        )

    def _inbox_path(self, agent_name: str) -> Path:
        """Get the path to an agent's inbox file."""
        # 对 agent_name 进行 sanitize，确保文件名不包含非法字符
        safe_name = sanitize_name(agent_name)
        return self._inbox_dir / f"{safe_name}.json"

    def _ensure_inbox_dir(self) -> None:
        """Ensure the inbox directory exists."""
        # parents=True 递归创建所有缺失的父目录；exist_ok=True 如果目录已存在不报错
        self._inbox_dir.mkdir(parents=True, exist_ok=True)

    def _read_inbox(self, agent_name: str) -> list[TeammateMessage]:
        """Read all messages from an agent's inbox file."""
        path = self._inbox_path(agent_name)
        # 收件箱文件不存在说明该 agent 从未收到过消息，返回空列表
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [TeammateMessage.from_dict(m) for m in data]
        except (json.JSONDecodeError, OSError) as e:
            # JSON 损坏或文件不可读时，记录警告但不抛异常
            # 因为 mailbox 读取失败不应阻断 agent 的核心执行流程
            logger.warning("Failed to read inbox for %s: %s", agent_name, e)
            return []

    def _write_inbox(self, agent_name: str, messages: list[TeammateMessage]) -> None:
        """Write messages to an agent's inbox file."""
        self._ensure_inbox_dir()
        path = self._inbox_path(agent_name)
        # 每次写入都是全量覆盖（非增量追加），因此需要先读取再追加再写回
        data = [m.to_dict() for m in messages]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def send(self, to: str, message: TeammateMessage) -> None:
        """Write a message to a teammate's inbox.

        Corresponds to TS: utils/teammateMailbox.ts writeToMailbox().

        Args:
            to: The recipient's agent name.
            message: The message to send (read flag forced to False).
        """
        # 强制将 read 标记设为 False，确保收件人的 receive() 能正确获取到这条新消息
        message.read = False
        # 读取收件人当前的所有消息 → 追加新消息 → 全量写回
        messages = self._read_inbox(to)
        messages.append(message)
        self._write_inbox(to, messages)
        logger.debug(
            "Wrote message to %s's inbox from %s", to, message.from_name
        )

    def receive(self, agent_name: str) -> list[TeammateMessage]:
        """Read all unread messages from an agent's inbox.

        Corresponds to TS: utils/teammateMailbox.ts readUnreadMessages().

        Returns:
            List of unread messages.
        """
        messages = self._read_inbox(agent_name)
        # 只返回未读消息；调用方需要在处理完后调用 mark_all_read() 标记已读
        return [m for m in messages if not m.read]

    def receive_all(self, agent_name: str) -> list[TeammateMessage]:
        """Read all messages (read and unread) from an agent's inbox.

        Corresponds to TS: utils/teammateMailbox.ts readMailbox().
        """
        return self._read_inbox(agent_name)

    def mark_all_read(self, agent_name: str) -> None:
        """Mark all messages in an agent's inbox as read.

        Corresponds to TS: utils/teammateMailbox.ts markAllAsRead().
        """
        messages = self._read_inbox(agent_name)
        changed = False
        for m in messages:
            if not m.read:
                m.read = True
                changed = True
        # 仅在确实有消息被标记时才写入文件，避免不必要的磁盘 IO
        if changed:
            self._write_inbox(agent_name, messages)
            logger.debug("Marked all messages as read for %s", agent_name)

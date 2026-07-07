# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MailboxMessage:
    id: str
    from_agent: str
    to_agent: str
    content: str
    summary: str = ""
    message_type: str = "text"  # text | shutdown_request | shutdown_response
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MailboxMessage:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class Mailbox:
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def _agent_dir(self, agent_id: str) -> Path:
        return self._base_dir / agent_id


    def write(self, agent_id: str, message: MailboxMessage) -> None:
        d = self._agent_dir(agent_id)
        d.mkdir(parents=True, exist_ok=True)
        filename = f"{message.timestamp:.6f}_{message.id}.json"
        (d / filename).write_text(
            json.dumps(message.to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )

    def read(self, agent_id: str) -> list[MailboxMessage]:
        d = self._agent_dir(agent_id)
        if not d.exists():
            return []
        messages: list[MailboxMessage] = []
        for f in sorted(d.iterdir()):
            if f.suffix != ".json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                messages.append(MailboxMessage.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return messages

    def consume(self, agent_id: str) -> list[MailboxMessage]:
        d = self._agent_dir(agent_id)
        if not d.exists():
            return []
        messages: list[MailboxMessage] = []
        for f in sorted(d.iterdir()):
            if f.suffix != ".json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                messages.append(MailboxMessage.from_dict(data))
                f.unlink()
            except (json.JSONDecodeError, KeyError):
                continue
        return messages

    def broadcast(
        self,
        team_members: list[str],
        message: MailboxMessage,
        exclude: str = "",
    ) -> None:
        for agent_id in team_members:
            if agent_id == exclude:
                continue
            self.write(agent_id, message)


    def cleanup(self, agent_id: str) -> None:
        d = self._agent_dir(agent_id)
        if d.exists():
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()

    def cleanup_all(self) -> None:
        if not self._base_dir.exists():
            return
        for d in self._base_dir.iterdir():
            if d.is_dir():
                for f in d.iterdir():
                    f.unlink(missing_ok=True)
                d.rmdir()


def create_message(
    from_agent: str,
    to_agent: str,
    content: str,
    summary: str = "",
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        id=uuid.uuid4().hex[:12],
        from_agent=from_agent,
        to_agent=to_agent,
        content=content,
        summary=summary,
        message_type=message_type,
        timestamp=time.time(),
        metadata=metadata or {},
    )

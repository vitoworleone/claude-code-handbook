"""Command history — tracks user inputs per session.

Corresponds to TS: history.ts.

记录用户在 REPL 中输入的命令历史，支持按项目/会话筛选。
历史以 JSONL 格式追加写入，保证即使进程崩溃也不会丢失已写入的记录。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 历史记录的最大返回条数，防止在长期使用后一次性加载过多数据
MAX_HISTORY_ITEMS = 100


@dataclass
class HistoryEntry:
    """A single history entry.

    display: 展示给用户的文本（通常是用户输入的原始命令）
    timestamp: Unix 时间戳，用于排序
    project: 项目标识（通常是 cwd 路径），用于按项目筛选
    session_id: 所属会话 ID，用于将当前会话的历史优先展示
    """

    display: str
    timestamp: float
    project: str
    session_id: str = ""


def get_history_path(claude_dir: Path | None = None) -> Path:
    """返回历史记录文件路径 (~/.claude/history.jsonl)。

    所有项目共享同一个历史文件，通过 project 字段区分。
    """
    base = claude_dir or (Path.home() / ".claude")
    return base / "history.jsonl"


def add_to_history(
    entry: HistoryEntry,
    claude_dir: Path | None = None,
) -> None:
    """Append a history entry to the history file.

    使用追加模式（"a"）写入，保证并发安全且不会覆盖已有记录。
    每次写入一行 JSON，这是 JSONL 格式的核心特性。
    """
    path = get_history_path(claude_dir)
    # 确保父目录存在（首次使用时 ~/.claude 可能尚未创建）
    path.parent.mkdir(parents=True, exist_ok=True)

    # 字段名使用 camelCase（sessionId）与 TS 原版保持一致，确保跨实现兼容
    record = {
        "display": entry.display,
        "timestamp": entry.timestamp,
        "project": entry.project,
        "sessionId": entry.session_id,
    }

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_history(
    project: str | None = None,
    session_id: str | None = None,
    limit: int = MAX_HISTORY_ITEMS,
    claude_dir: Path | None = None,
) -> list[HistoryEntry]:
    """Read history entries, with current session prioritized.

    Corresponds to TS: history.ts getHistory().

    排序策略：
    1. 当前会话的历史条目优先展示（按时间倒序）
    2. 其他会话的历史条目随后展示（按时间倒序）
    这样用户在按上箭头翻阅历史时，先看到本次会话的输入，再看到之前的。
    """
    path = get_history_path(claude_dir)
    if not path.exists():
        return []

    entries: list[HistoryEntry] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                entries.append(HistoryEntry(
                    display=record.get("display", ""),
                    timestamp=record.get("timestamp", 0),
                    project=record.get("project", ""),
                    session_id=record.get("sessionId", ""),
                ))
            except json.JSONDecodeError:
                # 单行损坏不影响其余记录的加载
                continue
    except OSError:
        return []

    # 按项目过滤，只返回指定项目的历史
    if project:
        entries = [e for e in entries if e.project == project]

    # 排序：当前会话优先，各组内部按时间倒序
    if session_id:
        current = [e for e in entries if e.session_id == session_id]
        others = [e for e in entries if e.session_id != session_id]
        current.sort(key=lambda e: e.timestamp, reverse=True)
        others.sort(key=lambda e: e.timestamp, reverse=True)
        # 当前会话条目排在前面，方便用户快速找到最近输入
        entries = current + others
    else:
        entries.sort(key=lambda e: e.timestamp, reverse=True)

    # 截断到最大条数，避免返回过多数据
    return entries[:limit]

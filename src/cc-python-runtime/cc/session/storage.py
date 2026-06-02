"""Session persistence — save/load conversation to disk.

Corresponds to TS: utils/sessionStorage.ts + history.ts.

会话持久化模块：将完整的对话记录序列化为 JSONL 文件，支持会话恢复。
JSONL 格式（每行一个 JSON 对象）的好处是：
1. 流式写入友好 —— 每写完一条消息就可以 flush
2. 部分损坏可恢复 —— 跳过损坏行即可继续加载
3. 便于追加 —— 不需要读取整个文件来添加新消息
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cc.models.messages import (
    AssistantMessage,
    CompactBoundaryMessage,
    Message,
    SystemMessage,
    Usage,
    UserMessage,
)

logger = logging.getLogger(__name__)


def get_sessions_dir(claude_dir: Path | None = None) -> Path:
    """Return the sessions directory path. Does NOT create it.

    与 memory 模块一致，读操作不创建目录。
    """
    base = claude_dir or (Path.home() / ".claude")
    return base / "sessions"


def save_session(
    session_id: str,
    messages: list[Message],
    claude_dir: Path | None = None,
    task_snapshot: list[dict[str, Any]] | None = None,
) -> Path:
    """Save a conversation session to JSONL + optional task snapshot.

    W3: Also persists TaskRegistry snapshot alongside transcript.

    每次保存会完整覆盖写入（"w" 模式），而非追加写入。
    这确保会话文件始终反映当前完整状态（包括被 compact 修改过的消息列表）。
    任务快照（task_snapshot）单独存储在 .tasks.json 文件中，
    因为任务状态和对话记录的生命周期不同。
    """
    sessions_dir = get_sessions_dir(claude_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{session_id}.jsonl"

    # 逐条序列化消息并写入，每条占一行
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            record = _message_to_record(msg)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # W3: 任务快照与对话记录分文件存储，方便独立加载和管理
    if task_snapshot is not None:
        tasks_path = sessions_dir / f"{session_id}.tasks.json"
        tasks_path.write_text(json.dumps(task_snapshot, ensure_ascii=False), encoding="utf-8")

    return path


def load_session(session_id: str, claude_dir: Path | None = None) -> list[Message] | None:
    """Load a conversation session from JSONL.

    Corresponds to TS: utils/conversationRecovery.ts.
    Returns None if session not found.

    逐行解析 JSONL 文件，跳过损坏行（容错设计）。
    返回 None 而非空列表以区分"会话不存在"和"会话存在但为空"两种情况。
    """
    sessions_dir = get_sessions_dir(claude_dir)
    path = sessions_dir / f"{session_id}.jsonl"

    if not path.exists():
        return None

    messages: list[Message] = []
    for line_num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            msg = _record_to_message(record)
            if msg is not None:
                messages.append(msg)
        except (json.JSONDecodeError, KeyError) as e:
            # 记录损坏行的位置以便排查，但继续加载后续消息
            logger.warning("Skipping corrupt line %d in session %s: %s", line_num, session_id, e)
            continue

    # 如果所有行都损坏或为空，视为会话不存在
    return messages if messages else None


def load_task_snapshot(
    session_id: str,
    claude_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Load task snapshot for a session.

    W3: Returns task records for TaskRegistry.restore().

    任务快照是一个 JSON 数组，每个元素是一个任务记录的字典表示。
    返回 None 表示没有快照或加载失败。
    """
    sessions_dir = get_sessions_dir(claude_dir)
    tasks_path = sessions_dir / f"{session_id}.tasks.json"
    if not tasks_path.exists():
        return None
    try:
        data = json.loads(tasks_path.read_text(encoding="utf-8"))
        # 类型校验：快照必须是列表
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, OSError):
        return None


def list_sessions(claude_dir: Path | None = None) -> list[str]:
    """List available session IDs. Returns empty list if sessions dir doesn't exist.

    按文件修改时间倒序排列，最近的会话排在前面，方便用户选择恢复。
    """
    sessions_dir = get_sessions_dir(claude_dir)
    if not sessions_dir.is_dir():
        return []
    return [p.stem for p in sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)]


def _message_to_record(msg: Message) -> dict[str, Any]:
    """Serialize a message to a JSON-safe dict.

    将各类型消息统一转换为可 JSON 序列化的字典。
    每种消息类型有不同的字段集，通过 type 字段区分。
    content blocks 通过其自身的 to_api_dict() 方法序列化，
    保证序列化格式与 API 请求格式一致。
    """
    if isinstance(msg, UserMessage):
        # 用户消息的 content 可以是纯文本字符串或 content block 列表
        content: Any = (
            msg.content if isinstance(msg.content, str) else [b.to_api_dict() for b in msg.content]
        )
        return {
            "type": "user",
            "content": content,
            "uuid": msg.uuid,
            "timestamp": msg.timestamp,
        }

    if isinstance(msg, AssistantMessage):
        # 助手消息始终使用 content block 列表格式，包含文本和工具调用
        return {
            "type": "assistant",
            "content": [b.to_api_dict() for b in msg.content],
            "uuid": msg.uuid,
            "timestamp": msg.timestamp,
            "stop_reason": msg.stop_reason,
            "usage": {
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            },
            "model": msg.model,
        }

    if isinstance(msg, CompactBoundaryMessage):
        # 压缩边界消息只包含摘要文本，标记上下文压缩的位置
        return {
            "type": "compact_boundary",
            "summary": msg.summary,
            "uuid": msg.uuid,
            "timestamp": msg.timestamp,
        }

    if isinstance(msg, SystemMessage):
        # 系统消息包含级别信息（info/warning/error），用于 UI 展示
        return {
            "type": "system",
            "content": msg.content,
            "level": msg.level,
            "uuid": msg.uuid,
            "timestamp": msg.timestamp,
        }

    # 未识别的消息类型，写入标记以便后续排查
    return {"type": "unknown"}


def _record_to_message(record: dict[str, Any]) -> Message | None:
    """Deserialize a message from a JSON record.

    反序列化的逆过程：将 JSON 字典还原为对应的 Message 子类实例。
    使用 content_block_from_api_dict 将序列化的 content block 字典还原为对象，
    确保反序列化后的消息与原始消息行为一致。
    """
    msg_type = record.get("type")

    if msg_type == "user":
        content = record["content"]
        if isinstance(content, str):
            # 纯文本用户消息，直接使用字符串
            user_content: str | list[Any] = content
        else:
            # 包含 content blocks 的用户消息（如 tool_result），需要反序列化
            from cc.models.content_blocks import content_block_from_api_dict
            user_content = [content_block_from_api_dict(b) for b in content]
        return UserMessage(
            content=user_content,
            uuid=record.get("uuid", ""),
            timestamp=record.get("timestamp", ""),
        )

    if msg_type == "assistant":
        from cc.models.content_blocks import content_block_from_api_dict
        # 助手消息的 content 始终是 block 列表
        blocks = [content_block_from_api_dict(b) for b in record.get("content", [])]
        usage_data = record.get("usage", {})
        return AssistantMessage(
            content=blocks,  # type: ignore[arg-type]
            uuid=record.get("uuid", ""),
            timestamp=record.get("timestamp", ""),
            stop_reason=record.get("stop_reason"),
            usage=Usage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
            ),
            model=record.get("model", ""),
        )

    if msg_type == "compact_boundary":
        return CompactBoundaryMessage(
            summary=record["summary"],
            uuid=record.get("uuid", ""),
            timestamp=record.get("timestamp", ""),
        )

    if msg_type == "system":
        return SystemMessage(
            content=record.get("content", ""),
            level=record.get("level", "info"),
            uuid=record.get("uuid", ""),
            timestamp=record.get("timestamp", ""),
        )

    # 返回 None 表示无法识别该记录类型，调用方会跳过
    return None

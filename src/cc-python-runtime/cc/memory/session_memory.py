"""Memory system — extract and persist memories from conversations.

Corresponds to TS: services/SessionMemory/prompts.ts + services/extractMemories/.

记忆系统的持久化层：负责记忆文件的读写、索引维护和目录管理。
每个项目通过 cwd 路径的哈希值获得独立的记忆目录，避免跨项目记忆混淆。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _project_id(cwd: str) -> str:
    """Deterministic project ID from cwd path.

    FIX: Uses hashlib.sha256 instead of hash() which is randomized per-process
    since Python 3.3 (PYTHONHASHSEED). This ensures the same cwd always maps
    to the same memory directory across process restarts.

    使用 SHA-256 的前 12 个十六进制字符作为项目 ID，
    足够避免碰撞（48 bit），同时保持目录名简短可读。
    """
    return hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:12]


def get_memory_dir(cwd: str, claude_dir: Path | None = None) -> Path:
    """Get the memory directory path for the current project.

    Does NOT create the directory — callers that need to write should mkdir themselves.

    目录结构为 ~/.claude/projects/<project_id>/memory/，
    不在此处创建目录是为了区分读操作和写操作：
    读操作在目录不存在时应返回空结果，而非意外创建空目录。
    """
    base = claude_dir or (Path.home() / ".claude")
    return base / "projects" / _project_id(cwd) / "memory"


def load_memories(cwd: str, claude_dir: Path | None = None) -> list[dict[str, str]]:
    """Load all saved memories for the current project.

    FIX: Does not mkdir on read. Returns empty list if directory doesn't exist.

    遍历记忆目录中所有 .md 文件（排除索引文件 MEMORY.md），
    按文件名排序以保证加载顺序的确定性。
    """
    mem_dir = get_memory_dir(cwd, claude_dir)
    # 目录不存在说明该项目从未保存过记忆，直接返回空列表
    if not mem_dir.is_dir():
        return []

    memories: list[dict[str, str]] = []
    for md_file in sorted(mem_dir.glob("*.md")):
        # 索引文件 MEMORY.md 不是记忆内容文件，跳过
        if md_file.name == "MEMORY.md":
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            # 使用文件名（不含扩展名）作为记忆名称
            memories.append({"name": md_file.stem, "content": text})
        except (OSError, UnicodeDecodeError):
            # 单个文件损坏不应阻止加载其他记忆
            continue

    return memories


def save_memory(
    cwd: str,
    name: str,
    content: str,
    claude_dir: Path | None = None,
) -> Path:
    """Save a memory to the project memory directory.

    Creates the directory on write (not on read).

    写操作时才创建目录（lazy mkdir），确保只有实际产生记忆的项目才会留下目录痕迹。
    文件名中的非法字符（非字母数字、非 - _）会被替换为下划线，防止路径注入。
    """
    mem_dir = get_memory_dir(cwd, claude_dir)
    # parents=True 确保中间目录也被创建，exist_ok=True 允许目录已存在
    mem_dir.mkdir(parents=True, exist_ok=True)

    # 将记忆名称中的特殊字符替换为下划线，保证文件名安全
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = mem_dir / f"{safe_name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def delete_memory(
    cwd: str,
    name: str,
    claude_dir: Path | None = None,
) -> bool:
    """Delete a memory by name.

    返回是否实际删除了文件，调用方可据此判断该记忆是否存在过。
    """
    mem_dir = get_memory_dir(cwd, claude_dir)
    # 使用与 save_memory 相同的名称清洗逻辑，保证一致性
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = mem_dir / f"{safe_name}.md"
    if path.is_file():
        path.unlink()
        return True
    return False


def format_memories_for_prompt(memories: list[dict[str, str]]) -> str | None:
    """Format loaded memories into a system prompt section.

    DEPRECATED: Use build_memory_prompt() from cc.prompts.sections instead,
    which generates the full behavioral instructions. This function is kept
    for backward compatibility with tests.

    将记忆列表格式化为 Markdown 格式的系统提示段落，
    每条记忆作为二级标题展示，方便模型在上下文中检索。
    """
    if not memories:
        return None

    parts = ["# Memories\n\nThe following memories were saved from previous conversations:\n"]
    for mem in memories:
        parts.append(f"## {mem['name']}\n{mem['content']}")

    return "\n\n".join(parts)


def update_memory_index(
    cwd: str,
    name: str,
    description: str,
    claude_dir: Path | None = None,
) -> None:
    """Append or update a memory entry in MEMORY.md index.

    P3a: Called by extractor after saving a memory file, so the memory
    appears in subsequent prompts (which load MEMORY.md).

    MEMORY.md 是记忆目录的索引文件，每行是一个 Markdown 链接指向具体记忆文件。
    采用 append-or-update 策略：
    - 若该记忆名称已在索引中，原地更新该行（保持描述最新）
    - 若为新记忆，追加到索引末尾
    """
    mem_dir = get_memory_dir(cwd, claude_dir)
    mem_dir.mkdir(parents=True, exist_ok=True)
    index_path = mem_dir / "MEMORY.md"

    # 读取现有索引内容（可能不存在）
    existing = ""
    if index_path.is_file():
        existing = index_path.read_text(encoding="utf-8").strip()

    # 构建安全文件名和索引条目行
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    entry_line = f"- [{safe_name}]({safe_name}.md) — {description}"

    if f"[{safe_name}]" in existing:
        # 该记忆已有索引条目，更新该行以反映最新描述
        lines = existing.split("\n")
        updated = [entry_line if f"[{safe_name}]" in line else line for line in lines]
        new_content = "\n".join(updated)
    else:
        # 新记忆，追加到索引末尾
        new_content = f"{existing}\n{entry_line}" if existing else entry_line

    # 确保文件以换行符结尾（符合 POSIX 文本文件规范）
    index_path.write_text(new_content.strip() + "\n", encoding="utf-8")
    logger.debug("Memory index updated: %s", safe_name)


def load_memory_index(cwd: str, claude_dir: Path | None = None) -> str | None:
    """Load the MEMORY.md index file content.

    Corresponds to TS: memdir/memdir.ts reading ENTRYPOINT_NAME in buildMemoryPrompt().

    Returns the content string if MEMORY.md exists and is non-empty, else None.

    此函数被 prompt 构建流程调用，将索引内容注入系统提示词，
    使模型知道有哪些可用记忆及其简要描述。
    """
    mem_dir = get_memory_dir(cwd, claude_dir)
    index_path = mem_dir / "MEMORY.md"
    if not index_path.is_file():
        return None
    try:
        content = index_path.read_text(encoding="utf-8").strip()
        # 空文件视为不存在，返回 None 使调用方可统一判断
        return content if content else None
    except (OSError, UnicodeDecodeError):
        return None

"""TodoWriteTool — persistent todo list management.

Corresponds to TS: tools/TodoWriteTool.

该工具提供跨会话持久化的待办事项管理。与 TaskTools 的内存存储不同，
TodoWriteTool 将数据写入磁盘，使得用户下次打开同一项目时
仍能看到之前的待办事项。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

TODO_WRITE_TOOL_NAME = "TodoWrite"


def _get_project_hash(project_dir: str | None = None) -> str:
    """Generate a stable hash for the current project directory.
    使用 SHA-256 哈希将项目路径映射为固定长度的标识符，
    避免路径中的特殊字符（空格、中文等）导致文件名问题。
    截取前 16 位作为哈希值，在实际使用中碰撞概率忽略不计。
    """
    project = project_dir or str(Path.cwd())
    return hashlib.sha256(project.encode()).hexdigest()[:16]


def _get_todos_dir(claude_dir: Path | None = None) -> Path:
    """Get the todos storage directory.
    todo 数据统一存储在 ~/.claude/todos/ 下，
    每个项目对应一个以项目路径哈希命名的 JSON 文件。
    """
    base = claude_dir or (Path.home() / ".claude")
    return base / "todos"


class TodoWriteTool(Tool):
    """Write/update a todo list for the current project.

    Corresponds to TS: tools/TodoWriteTool.
    Stores todos as JSON in {claude_dir}/todos/{project_hash}.json.

    采用"全量覆盖"策略：每次调用都写入完整的 todo 列表，
    而非增量修改。这简化了并发和一致性问题。
    """

    def __init__(self, project_dir: str | None = None, claude_dir: Path | None = None) -> None:
        # 支持注入自定义路径，便于测试时使用临时目录
        self._project_dir = project_dir
        self._claude_dir = claude_dir

    def get_name(self) -> str:
        return TODO_WRITE_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=TODO_WRITE_TOOL_NAME,
            description="Write and manage a todo list for the current project.",
            input_schema={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "List of todo items",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Unique identifier for the todo",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The todo content/description",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Current status of the todo",
                                },
                            },
                            "required": ["id", "content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        todos = tool_input.get("todos")
        if todos is None:
            return ToolResult(content="Error: todos is required", is_error=True)

        if not isinstance(todos, list):
            return ToolResult(content="Error: todos must be an array", is_error=True)

        # 逐项校验 todo 的数据结构，确保每项都有必填字段且状态值合法
        for i, todo in enumerate(todos):
            if not isinstance(todo, dict):
                return ToolResult(
                    content=f"Error: todo item at index {i} must be an object",
                    is_error=True,
                )
            for field in ("id", "content", "status"):
                if field not in todo:
                    return ToolResult(
                        content=f"Error: todo item at index {i} missing required field '{field}'",
                        is_error=True,
                    )
            if todo["status"] not in ("pending", "in_progress", "completed"):
                return ToolResult(
                    content=f"Error: todo item at index {i} has invalid status '{todo['status']}'",
                    is_error=True,
                )

        # 生成项目哈希并确定存储路径
        project_hash = _get_project_hash(self._project_dir)
        todos_dir = _get_todos_dir(self._claude_dir)

        try:
            # 自动创建目录结构，首次使用时无需手动创建
            todos_dir.mkdir(parents=True, exist_ok=True)
            file_path = todos_dir / f"{project_hash}.json"
            # 全量写入——覆盖之前的数据，由模型负责维护列表的完整性
            file_path.write_text(json.dumps(todos, indent=2), encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error writing todos: {e}", is_error=True)

        # 统计各状态的数量，提供给模型作为确认信息
        pending = sum(1 for t in todos if t["status"] == "pending")
        in_progress = sum(1 for t in todos if t["status"] == "in_progress")
        completed = sum(1 for t in todos if t["status"] == "completed")

        return ToolResult(
            content=(
                f"Wrote {len(todos)} todo(s) to {file_path}. "
                f"Status: {pending} pending, {in_progress} in progress, {completed} completed."
            )
        )

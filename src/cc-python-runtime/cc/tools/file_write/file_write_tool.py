"""FileWriteTool implementation.

Corresponds to TS: tools/FileWriteTool/FileWriteTool.ts.
"""

# 本模块实现了文件写入工具，用于创建新文件或完全覆盖已有文件。
#
# 与 FileEditTool 的区别：
# - FileEditTool 是局部修改（字符串替换），适用于编辑已有文件
# - FileWriteTool 是全量写入，适用于创建新文件或完全重写
#
# 关键设计：使用「原子写入」策略——先写入临时文件，再用 os.replace() 原子性
# 地替换目标文件。这保证了写入过程中如果程序崩溃或断电，目标文件要么是
# 旧内容（完整），要么是新内容（完整），不会出现写了一半的损坏文件。

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

FILE_WRITE_TOOL_NAME = "Write"


class FileWriteTool(Tool):
    """Write files atomically.

    Corresponds to TS: tools/FileWriteTool/FileWriteTool.ts.
    """

    def get_name(self) -> str:
        return FILE_WRITE_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=FILE_WRITE_TOOL_NAME,
            description="Writes a file to the local filesystem.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["file_path", "content"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")

        if not file_path:
            return ToolResult(content="Error: file_path is required", is_error=True)

        path = Path(file_path)

        # Create parent directories
        # 自动创建父目录（递归），exist_ok=True 表示目录已存在时不报错。
        # 这使得模型可以一步创建深层路径的文件，无需先手动创建目录。
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return ToolResult(content=f"Error creating directories: {e}", is_error=True)

        # Atomic write: write to temp file then rename
        # 原子写入实现步骤：
        # 1. 在目标文件的同一目录下创建临时文件（同一目录确保在同一文件系统，
        #    os.replace 要求源和目标在同一文件系统才能保证原子性）
        # 2. 将内容写入临时文件
        # 3. 使用 os.replace() 原子性地将临时文件替换为目标文件
        #    os.replace 在 POSIX 上是原子操作（单次 rename 系统调用），
        #    在 Windows 上也保证替换语义（即使目标文件已存在）
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                # os.replace 原子性替换：要么完全成功，要么完全不生效
                os.replace(tmp_path, str(path))
            except Exception:
                # Clean up temp file on error
                # 写入失败时清理临时文件，suppress(OSError) 防止清理本身也失败
                # （例如临时文件已被其他进程删除）
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        # 计算写入的行数作为反馈信息。
        # 计算逻辑：换行符数量 + 1（最后一行），但如果内容以换行符结尾则不 +1，
        # 因为末尾换行符后面没有实际的新行内容。
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return ToolResult(content=f"Successfully wrote {line_count} lines to {file_path}")

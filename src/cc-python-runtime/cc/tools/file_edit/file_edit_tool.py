"""FileEditTool implementation.

Corresponds to TS: tools/FileEditTool/FileEditTool.ts.
"""

# 本模块实现了文件编辑工具，通过"精确字符串替换"的方式修改文件。
#
# 为什么用字符串替换而不是行号替换？
# - 行号在多次编辑后会偏移，导致第二次编辑可能改错位置
# - 字符串替换要求模型提供足够的上下文使匹配唯一，天然避免了误操作
# - 如果 old_string 在文件中出现多次且未指定 replace_all，会报错要求
#   模型提供更多上下文来消歧
#
# 设计要点：
# - 使用二进制读写保留原始行尾（CRLF/LF），避免在 Windows 文件上引入行尾变化
# - 返回 unified diff 格式的变更摘要，让模型和用户都能看到改了什么
# - 未标记为并发安全（默认 False），因为写操作可能与其他读/写操作冲突

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

FILE_EDIT_TOOL_NAME = "Edit"


class FileEditTool(Tool):
    """Edit files via string replacement.

    Corresponds to TS: tools/FileEditTool/FileEditTool.ts.
    """

    def get_name(self) -> str:
        return FILE_EDIT_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=FILE_EDIT_TOOL_NAME,
            description="Performs exact string replacements in files.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute path to the file to modify",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The text to replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences (default false)",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        )

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        replace_all = bool(tool_input.get("replace_all", False))

        if not file_path:
            return ToolResult(content="Error: file_path is required", is_error=True)

        path = Path(file_path)
        if not path.is_file():
            return ToolResult(content=f"Error: File does not exist: {file_path}", is_error=True)

        # old_string 与 new_string 相同时拒绝操作，避免无意义的"修改"
        if old_string == new_string:
            return ToolResult(content="Error: old_string and new_string must be different", is_error=True)

        # FIX (check.md #8): Read in binary mode to preserve CRLF line endings.
        # 使用二进制模式读取文件，再手动解码为 UTF-8。
        # 这比 read_text() 的优势在于：read_text() 在 Windows 上会自动将
        # CRLF 转为 LF（universal newlines），导致写回时行尾被意外修改。
        # 二进制读取 + 手动解码可以精确保留原始行尾。
        try:
            raw_bytes = path.read_bytes()
            content = raw_bytes.decode("utf-8")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        # 检查 old_string 是否存在于文件中
        count = content.count(old_string)
        if count == 0:
            return ToolResult(content=f"Error: old_string not found in {file_path}", is_error=True)

        # 如果 old_string 出现多次且未指定 replace_all，报错要求消歧。
        # 这是一个重要的安全机制：防止模型在不了解全部上下文的情况下
        # 意外修改了文件中其他位置的相同文本。
        if count > 1 and not replace_all:
            return ToolResult(
                content=(
                    f"Error: old_string found {count} times in {file_path}."
                    " Use replace_all=true or provide more context to make it unique."
                ),
                is_error=True,
            )

        # Perform replacement
        if replace_all:
            # 替换所有出现的位置
            new_content = content.replace(old_string, new_string)
        else:
            # 只替换第一个出现的位置（此时 count 必为 1）
            new_content = content.replace(old_string, new_string, 1)

        # Write back in binary mode to preserve original line endings
        # 写回时同样使用二进制模式，确保不会引入额外的行尾转换
        try:
            path.write_bytes(new_content.encode("utf-8"))
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)

        # Generate diff
        # 生成 unified diff 格式的变更摘要，方便模型和用户审查修改内容。
        # keepends=True 保留行尾字符，使 diff 输出更准确。
        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=file_path, tofile=file_path))

        return ToolResult(content=diff or "File updated (no visible diff)")

"""FileReadTool implementation.

Corresponds to TS: tools/FileReadTool/FileReadTool.ts.
"""

# 本模块实现了文件读取工具，是模型查看文件内容的主要方式。
# 支持两种文件类型：
# 1. 文本文件：以 cat -n 格式返回带行号的内容，支持 offset/limit 分页
# 2. 图片文件：以 base64 编码返回，模型可以"看到"图片内容（多模态能力）
#
# 设计要点：
# - 始终返回行号，因为 FileEditTool 需要用户指定精确的文本片段进行替换，
#   行号帮助模型定位要编辑的内容
# - 默认只读取前 2000 行，防止大文件耗尽上下文窗口
# - 标记为并发安全（is_concurrency_safe=True），因为读操作没有副作用

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

FILE_READ_TOOL_NAME = "Read"
# 默认读取行数上限，防止大文件（如日志文件、数据文件）占满上下文窗口
DEFAULT_LIMIT = 2000
# 支持的图片扩展名集合，这些文件会以 base64 方式返回而非文本
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}


class FileReadTool(Tool):
    """Read file contents.

    Corresponds to TS: tools/FileReadTool/FileReadTool.ts.
    """

    def get_name(self) -> str:
        return FILE_READ_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=FILE_READ_TOOL_NAME,
            description="Reads a file from the local filesystem.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute path to the file to read",
                    },
                    "offset": {
                        "type": "number",
                        "description": "Line number to start reading from",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Number of lines to read",
                    },
                },
                "required": ["file_path"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # 文件读取是纯只读操作，不会修改文件系统状态，
        # 因此可以安全地与其他工具（包括其他 Read）并行执行。
        return True  # Reading is always safe

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        file_path = tool_input.get("file_path", "")
        # offset 默认从第 1 行开始（1-based），与 cat -n 的行号对齐
        offset = int(tool_input.get("offset", 1))
        limit = int(tool_input.get("limit", DEFAULT_LIMIT))

        if not file_path:
            return ToolResult(content="Error: file_path is required", is_error=True)

        path = Path(file_path)
        if not path.exists():
            return ToolResult(content=f"Error: File does not exist: {file_path}", is_error=True)

        if not path.is_file():
            # 防止传入目录路径，目录应使用 ls 命令而非 Read 工具
            return ToolResult(content=f"Error: Not a file: {file_path}", is_error=True)

        # Check if image — return rich content block with base64 data
        # 图片处理：以 base64 编码返回富内容块，利用模型的多模态能力"看图"。
        # 这使得模型可以分析截图、UI 设计稿、图表等。
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            try:
                data = path.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                # 使用 mimetypes 自动推断 MIME 类型，默认回退到 image/png
                media_type = mimetypes.guess_type(str(path))[0] or "image/png"
                # 返回 list[dict] 格式的富内容块，与 Anthropic API 的 image content block 对齐
                return ToolResult(
                    content=[{
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    }],
                )
            except Exception as e:
                return ToolResult(content=f"Error reading image: {e}", is_error=True)

        # Read text file with line numbers (cat -n format)
        # 使用 errors="replace" 处理非 UTF-8 字符，避免读取二进制文件时崩溃
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        if not text:
            return ToolResult(content=f"(file is empty: {file_path})")

        lines = text.splitlines()
        total_lines = len(lines)

        # Apply offset (1-based) and limit
        # offset 是 1-based（与行号一致），转换为 0-based 索引进行切片
        start_idx = max(0, offset - 1)
        end_idx = min(total_lines, start_idx + limit)
        selected = lines[start_idx:end_idx]

        # Format with line numbers
        # 使用制表符分隔行号和内容，与 cat -n 输出格式一致。
        # 行号从 start_idx+1 开始（保持绝对行号），这样无论 offset 是多少，
        # 行号总是反映文件中的实际位置，方便后续 Edit 操作定位。
        numbered = []
        for i, line in enumerate(selected, start=start_idx + 1):
            numbered.append(f"{i}\t{line}")

        result = "\n".join(numbered)

        # 如果文件还有未显示的行，在末尾添加提示信息，
        # 引导模型使用 offset/limit 参数查看更多内容
        if end_idx < total_lines:
            remaining = total_lines - end_idx
            result += f"\n\n(... {remaining} more lines not shown. Use offset/limit to read more.)"

        return ToolResult(content=result)

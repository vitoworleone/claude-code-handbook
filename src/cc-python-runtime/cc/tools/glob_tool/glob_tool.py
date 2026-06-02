"""GlobTool implementation.

Corresponds to TS: tools/GlobTool/GlobTool.ts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cc.tools.base import Tool, ToolResult, ToolSchema

GLOB_TOOL_NAME = "Glob"
# 限制最多返回 100 个文件，避免大量匹配结果撑爆上下文窗口
MAX_RESULTS = 100


class GlobTool(Tool):
    """Find files by glob pattern.

    Corresponds to TS: tools/GlobTool/GlobTool.ts.
    使用 Python 标准库 pathlib.glob 进行文件匹配，
    不依赖外部工具，适用于任意规模的代码库。
    """

    def get_name(self) -> str:
        return GLOB_TOOL_NAME

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            name=GLOB_TOOL_NAME,
            description="Fast file pattern matching tool that works with any codebase size.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match files against",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default: cwd)",
                    },
                },
                "required": ["pattern"],
            },
        )

    def is_concurrency_safe(self, tool_input: dict[str, Any]) -> bool:
        # glob 操作纯读取文件系统元数据，不做任何修改，可安全并发
        return True

    async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        pattern = tool_input.get("pattern", "")
        search_path = tool_input.get("path", ".")

        if not pattern:
            return ToolResult(content="Error: pattern is required", is_error=True)

        # resolve() 将相对路径转为绝对路径，确保后续匹配结果也是绝对路径
        base = Path(search_path).resolve()
        if not base.is_dir():
            return ToolResult(content=f"Error: Directory not found: {search_path}", is_error=True)

        try:
            # 按修改时间降序排列，让模型优先看到最近修改的文件
            matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            # 只保留文件，排除目录——因为模型需要的是可读取的文件路径
            matches = [m for m in matches if m.is_file()]
        except Exception as e:
            return ToolResult(content=f"Error: {e}", is_error=True)

        if not matches:
            return ToolResult(content="No files found")

        total = len(matches)
        # 截断到 MAX_RESULTS，避免超大结果集浪费上下文
        truncated = matches[:MAX_RESULTS]
        result = "\n".join(str(m) for m in truncated)

        if total > MAX_RESULTS:
            result += f"\n\n(... {total - MAX_RESULTS} more files not shown)"

        return ToolResult(content=result)
